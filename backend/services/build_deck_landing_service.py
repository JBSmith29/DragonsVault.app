"""Read-only landing page insights for the Build-A-Deck workflow."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Card, Folder, FolderRole
from services import scryfall_cache as sc
from services.commander_utils import split_commander_names, split_commander_oracle_ids
from services.edhrec_client import slugify_theme
from services.edhrec_recommendation_service import get_commander_synergy
from services.request_cache import request_cached

_LOG = logging.getLogger(__name__)

_COMMANDER_POOL_LIMIT = 120
_TAG_POOL_LIMIT = 80
_HIGH_SYNERGY_LIMIT = 60
_RESULT_LIMIT = 8


def _preferred_print(oracle_id: str) -> dict | None:
    prints = sc.prints_for_oracle(oracle_id) or ()
    if not prints:
        return None
    for pr in prints:
        if pr.get("digital"):
            continue
        if (pr.get("lang") or "en").lower() == "en":
            return pr
    for pr in prints:
        if (pr.get("lang") or "en").lower() == "en":
            return pr
    return prints[0]


def _commander_color_mask(commander_oracle_id: str | None) -> tuple[int, bool]:
    mask = 0
    resolved = False
    for oid in split_commander_oracle_ids(commander_oracle_id):
        pr = _preferred_print(oid)
        if not pr:
            continue
        resolved = True
        meta = sc.metadata_from_print(pr)
        mask |= int(meta.get("color_identity_mask") or 0)
    return mask, resolved


def _card_color_mask(card_oracle_id: str) -> tuple[int, bool]:
    pr = _preferred_print(card_oracle_id)
    if not pr:
        return 0, False
    meta = sc.metadata_from_print(pr)
    return int(meta.get("color_identity_mask") or 0), True


def _safe_schema_name(value: str) -> str:
    value = (value or "").strip()
    if not value or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value):
        return "edhrec_service"
    return value


_EDHREC_SCHEMA = _safe_schema_name(os.getenv("EDHREC_SERVICE_SCHEMA", "edhrec_service"))


def _is_postgres() -> bool:
    try:
        return db.engine.dialect.name == "postgresql"
    except Exception:
        return False


def _normalize_payload(raw: object) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _collection_folder_ids(user_id: int | None) -> list[int]:
    if not user_id:
        return []
    rows = (
        db.session.query(FolderRole.folder_id)
        .join(Folder, Folder.id == FolderRole.folder_id)
        .filter(
            FolderRole.role == FolderRole.ROLE_COLLECTION,
            Folder.owner_user_id == user_id,
        )
        .all()
    )
    return [row[0] for row in rows]


def _owned_oracle_ids(user_id: int | None) -> set[str]:
    if not user_id:
        return set()

    cache_key = ("build_landing_owned_oracles", user_id)

    def _load() -> set[str]:
        folder_ids = _collection_folder_ids(user_id)
        if not folder_ids:
            return set()
        rows = (
            db.session.query(Card.oracle_id)
            .filter(Card.folder_id.in_(folder_ids), Card.oracle_id.isnot(None))
            .distinct()
            .all()
        )
        return {str(row[0]).strip() for row in rows if row and row[0]}

    return request_cached(cache_key, _load)


def _load_commander_candidates(theme_slug: str | None, limit: int) -> list[dict]:
    if not _is_postgres() or limit <= 0:
        return []

    cache_key = ("build_landing_candidates", theme_slug or "", limit)

    def _load() -> list[dict]:
        if theme_slug:
            sql = text(
                f"""
                SELECT slug, theme_slug, name, payload, fetched_at
                FROM {_EDHREC_SCHEMA}.edhrec_commanders
                WHERE theme_slug = :theme_slug
                ORDER BY fetched_at DESC NULLS LAST, name ASC
                LIMIT :limit
                """
            )
            params = {"theme_slug": theme_slug, "limit": limit}
        else:
            sql = text(
                f"""
                SELECT slug, theme_slug, name, payload, fetched_at
                FROM {_EDHREC_SCHEMA}.edhrec_commanders
                WHERE theme_slug IS NULL
                ORDER BY fetched_at DESC NULLS LAST, name ASC
                LIMIT :limit
                """
            )
            params = {"limit": limit}

        try:
            rows = db.session.execute(sql, params).mappings().all()
        except SQLAlchemyError as exc:
            db.session.rollback()
            _LOG.warning("EDHREC commander cache lookup failed: %s", exc)
            return []
        return list(rows)

    return request_cached(cache_key, _load)


def _commander_name_from_row(row: dict) -> str | None:
    name = (row.get("name") or "").strip()
    if name:
        return name
    payload = _normalize_payload(row.get("payload")) or {}
    name = (payload.get("name") or "").strip()
    return name or None


def _commander_oracle_id_from_name(name: str, cache: dict[str, str | None]) -> str | None:
    key = (name or "").strip().casefold()
    if not key:
        return None
    if key in cache:
        return cache[key]
    oracle_ids: list[str] = []
    for part in split_commander_names(name):
        try:
            oid = sc.unique_oracle_by_name(part)
        except Exception:
            oid = None
        if oid:
            oracle_ids.append(oid)
    value = ",".join(oracle_ids) if oracle_ids else None
    cache[key] = value
    return value


def _build_fit_summary(
    commander_oracle_id: str,
    commander_name: str,
    owned_oracle_ids: set[str],
    *,
    tag: str | None = None,
) -> dict | None:
    recs = get_commander_synergy(commander_oracle_id, [tag] if tag else None)
    if not recs:
        return None

    commander_mask, commander_ok = _commander_color_mask(commander_oracle_id)
    if commander_ok:
        filtered: list[dict] = []
        mask_cache: dict[str, int] = {}
        for rec in recs:
            oracle_id = (rec.get("oracle_id") or "").strip()
            if not oracle_id:
                continue
            if oracle_id not in mask_cache:
                mask_val, card_ok = _card_color_mask(oracle_id)
                mask_cache[oracle_id] = mask_val if card_ok else -1
            mask_val = mask_cache.get(oracle_id, -1)
            if mask_val >= 0 and (commander_mask & mask_val == mask_val):
                filtered.append(rec)
        recs = filtered
        if not recs:
            return None

    relevant = recs
    if tag:
        tag_key = tag.casefold()
        tagged = []
        for rec in recs:
            matches = rec.get("tag_matches") or []
            if any(tag_key == str(match).casefold() for match in matches):
                tagged.append(rec)
        if tagged:
            relevant = tagged

    top = list(relevant[:_HIGH_SYNERGY_LIMIT])
    if not top:
        return None

    owned = [rec for rec in top if str(rec.get("oracle_id") or "").strip() in owned_oracle_ids]
    owned_count = len(owned)
    if owned_count == 0:
        return None

    owned_synergy = sum(float(rec.get("synergy_score") or 0.0) for rec in owned)
    score = (owned_count * 3.0) + owned_synergy
    coverage_pct = int(round((owned_count / len(top)) * 100)) if top else 0

    return {
        "commander_name": commander_name,
        "commander_oracle_id": commander_oracle_id,
        "owned_count": owned_count,
        "total_considered": len(top),
        "coverage_pct": coverage_pct,
        "score": score,
        "tag": tag,
    }


def _rank_candidates(
    rows: Iterable[dict],
    owned_oracle_ids: set[str],
    *,
    tag: str | None = None,
    limit: int = _RESULT_LIMIT,
) -> list[dict]:
    if not owned_oracle_ids:
        return []
    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for build landing: %s", exc)
        return []

    oracle_cache: dict[str, str | None] = {}
    results: list[dict] = []
    for row in rows:
        commander_name = _commander_name_from_row(row)
        if not commander_name:
            continue
        commander_oracle_id = _commander_oracle_id_from_name(commander_name, oracle_cache)
        if not commander_oracle_id:
            continue
        summary = _build_fit_summary(
            commander_oracle_id,
            commander_name,
            owned_oracle_ids,
            tag=tag,
        )
        if summary:
            results.append(summary)

    results.sort(
        key=lambda item: (
            -(item.get("score") or 0.0),
            -(item.get("owned_count") or 0),
            (item.get("commander_name") or "").lower(),
        )
    )
    return results[:limit]


def get_build_landing_data(
    user_id: int | None,
    selected_tag: str | None = None,
    *,
    limit: int = _RESULT_LIMIT,
) -> dict:
    """
    Aggregate Build-A-Deck landing recommendations.

    Returns read-only summaries without creating any deck state.
    """

    owned_oracle_ids = _owned_oracle_ids(user_id)
    collection_count = len(owned_oracle_ids)

    base_rows = _load_commander_candidates(None, _COMMANDER_POOL_LIMIT)
    edhrec_ready = bool(base_rows)

    collection_fits: list[dict] = []
    if owned_oracle_ids and edhrec_ready:
        collection_fits = _rank_candidates(base_rows, owned_oracle_ids, limit=limit)

    tag_fits: list[dict] = []
    tag_candidates = 0
    if selected_tag:
        theme_slug = slugify_theme(selected_tag)
        tag_rows = _load_commander_candidates(theme_slug, _TAG_POOL_LIMIT)
        tag_candidates = len(tag_rows)
        if owned_oracle_ids and tag_rows:
            tag_fits = _rank_candidates(tag_rows, owned_oracle_ids, tag=selected_tag, limit=limit)

    return {
        "collection_count": collection_count,
        "collection_fits": collection_fits,
        "tag_fits": tag_fits,
        "tag_candidates": tag_candidates,
        "edhrec_ready": edhrec_ready,
    }


__all__ = ["get_build_landing_data"]
