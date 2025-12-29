"""Read-only landing page insights for the Build-A-Deck workflow."""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Card, EdhrecCommanderCard, Folder, FolderRole
from services import scryfall_cache as sc
from services.commander_utils import split_commander_oracle_ids
from services.edhrec_cache_service import (
    cache_ready,
    get_commander_synergy,
    get_commander_tags,
    get_tag_commanders,
)
from services.request_cache import request_cached

_LOG = logging.getLogger(__name__)

_COMMANDER_POOL_LIMIT = 160
_HIGH_SYNERGY_LIMIT = 60
_RESULT_LIMIT = 8
_TAG_HINT_LIMIT = 2


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


def _bit_count(value: int) -> int:
    return bin(value).count("1") if value else 0


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


def _commander_name(commander_oracle_id: str) -> str | None:
    names: list[str] = []
    for oid in split_commander_oracle_ids(commander_oracle_id):
        pr = _preferred_print(oid)
        if pr and pr.get("name"):
            names.append(pr.get("name"))
    if not names:
        return None
    return " // ".join(names)


def _commander_image(commander_oracle_id: str) -> str | None:
    for oid in split_commander_oracle_ids(commander_oracle_id):
        pr = _preferred_print(oid)
        if not pr:
            continue
        meta = sc.metadata_from_print(pr)
        faces = meta.get("faces_json") or []
        if faces:
            for face in faces:
                image_uris = face.get("image_uris") or {}
                for key in ("normal", "large", "small"):
                    url = image_uris.get(key)
                    if url:
                        return url
        image_uris = meta.get("image_uris") or {}
        for key in ("normal", "large", "small"):
            url = image_uris.get(key)
            if url:
                return url
        image = sc.image_for_print(pr)
        for key in ("normal", "large", "small"):
            url = image.get(key)
            if url:
                return url
    return None


def _load_commander_candidates(limit: int) -> list[str]:
    if limit <= 0:
        return []
    cache_key = ("build_landing_candidates", limit)

    def _load() -> list[str]:
        try:
            rows = (
                db.session.query(
                    EdhrecCommanderCard.commander_oracle_id,
                    func.max(EdhrecCommanderCard.synergy_score).label("top_score"),
                )
                .group_by(EdhrecCommanderCard.commander_oracle_id)
                .order_by(func.max(EdhrecCommanderCard.synergy_score).desc())
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as exc:
            db.session.rollback()
            _LOG.warning("EDHREC commander cache lookup failed: %s", exc)
            return []
        return [row[0] for row in rows if row and row[0]]

    return request_cached(cache_key, _load)


def _color_coverage_bonus(commander_oracle_id: str, owned_oracle_ids: list[str]) -> tuple[float, int, int]:
    commander_mask, commander_ok = _commander_color_mask(commander_oracle_id)
    if not commander_ok or commander_mask <= 0:
        return 0.0, 0, 0

    owned_mask = 0
    mask_cache: dict[str, int] = {}
    for oracle_id in owned_oracle_ids:
        if oracle_id not in mask_cache:
            mask_val, card_ok = _card_color_mask(oracle_id)
            mask_cache[oracle_id] = mask_val if card_ok else 0
        owned_mask |= mask_cache[oracle_id]

    total_colors = _bit_count(commander_mask)
    covered_colors = _bit_count(commander_mask & owned_mask)
    if total_colors == 0:
        return 0.0, 0, 0
    coverage_ratio = covered_colors / total_colors
    bonus = coverage_ratio * 6.0
    return bonus, covered_colors, total_colors


def _build_fit_summary(
    commander_oracle_id: str,
    commander_name: str,
    owned_oracle_ids: set[str],
    *,
    tag: str | None = None,
    tag_cache: dict[str, list[str]] | None = None,
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

    top = list(recs[:_HIGH_SYNERGY_LIMIT])
    if not top:
        return None

    owned = [rec for rec in top if str(rec.get("oracle_id") or "").strip() in owned_oracle_ids]
    owned_count = len(owned)
    if owned_count == 0:
        return None

    owned_synergy = sum(float(rec.get("synergy_score") or 0.0) for rec in owned)
    owned_ids = [rec.get("oracle_id") for rec in owned if rec.get("oracle_id")]
    coverage_bonus, covered_colors, total_colors = _color_coverage_bonus(
        commander_oracle_id,
        owned_ids,
    )

    commander_tags = tag_cache.get(commander_oracle_id) if tag_cache is not None else None
    if commander_tags is None:
        commander_tags = get_commander_tags(commander_oracle_id)
        if tag_cache is not None:
            tag_cache[commander_oracle_id] = commander_tags

    tag_match = bool(tag and tag in commander_tags)
    if tag and not tag_match:
        return None

    tag_bonus = 8.0 if tag_match else 0.0
    score = (owned_count * 3.0) + owned_synergy + tag_bonus + coverage_bonus
    coverage_pct = int(round((owned_count / len(top)) * 100)) if top else 0
    commander_image = _commander_image(commander_oracle_id)

    return {
        "commander_name": commander_name,
        "commander_oracle_id": commander_oracle_id,
        "commander_image": commander_image,
        "owned_count": owned_count,
        "total_considered": len(top),
        "coverage_pct": coverage_pct,
        "score": score,
        "tag": tag,
        "tag_match": tag_match,
        "tag_hints": commander_tags[:_TAG_HINT_LIMIT],
        "color_covered": covered_colors,
        "color_total": total_colors,
    }


def _rank_candidates(
    commander_ids: Iterable[str],
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

    tag_cache: dict[str, list[str]] = {}
    results: list[dict] = []
    for commander_oracle_id in commander_ids:
        if not commander_oracle_id:
            continue
        commander_name = _commander_name(commander_oracle_id)
        if not commander_name:
            continue
        summary = _build_fit_summary(
            commander_oracle_id,
            commander_name,
            owned_oracle_ids,
            tag=tag,
            tag_cache=tag_cache,
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


def get_commander_fits_from_collection(
    user_id: int | None,
    *,
    limit: int = _RESULT_LIMIT,
) -> list[dict]:
    owned_oracle_ids = _owned_oracle_ids(user_id)
    if not owned_oracle_ids or not cache_ready():
        return []
    commander_ids = _load_commander_candidates(_COMMANDER_POOL_LIMIT)
    return _rank_candidates(commander_ids, owned_oracle_ids, limit=limit)


def get_commander_fits_by_tag(
    user_id: int | None,
    tag: str,
    *,
    limit: int = _RESULT_LIMIT,
) -> tuple[list[dict], int]:
    owned_oracle_ids = _owned_oracle_ids(user_id)
    commander_ids = get_tag_commanders(tag)
    tag_candidates = len(commander_ids)
    if not owned_oracle_ids or not commander_ids:
        return [], tag_candidates
    return _rank_candidates(commander_ids, owned_oracle_ids, tag=tag, limit=limit), tag_candidates


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
    edhrec_ready = cache_ready()

    collection_fits = get_commander_fits_from_collection(user_id, limit=limit)

    tag_fits: list[dict] = []
    tag_candidates = 0
    if selected_tag:
        tag_fits, tag_candidates = get_commander_fits_by_tag(
            user_id,
            selected_tag,
            limit=limit,
        )

    return {
        "collection_count": collection_count,
        "collection_fits": collection_fits,
        "tag_fits": tag_fits,
        "tag_candidates": tag_candidates,
        "edhrec_ready": edhrec_ready,
    }


__all__ = [
    "get_commander_fits_from_collection",
    "get_commander_fits_by_tag",
    "get_build_landing_data",
]
