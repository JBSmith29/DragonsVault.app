"""Read-only EDHREC recommendation access using cached payloads."""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from services import scryfall_cache as sc
from services.commander_utils import primary_commander_oracle_id
from services.edhrec_client import commander_cardviews, merge_cardviews, slugify_commander, slugify_theme
from services.request_cache import request_cached

_LOG = logging.getLogger(__name__)


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


def _normalize_payload(raw: Any) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _load_commander_payload(slug: str, theme_slug: str | None) -> dict | None:
    if not slug or not _is_postgres():
        return None

    if theme_slug:
        sql = text(
            f"""
            SELECT payload
            FROM {_EDHREC_SCHEMA}.edhrec_commanders
            WHERE slug = :slug AND theme_slug = :theme_slug
            ORDER BY fetched_at DESC NULLS LAST
            LIMIT 1
            """
        )
        params = {"slug": slug, "theme_slug": theme_slug}
    else:
        sql = text(
            f"""
            SELECT payload
            FROM {_EDHREC_SCHEMA}.edhrec_commanders
            WHERE slug = :slug AND theme_slug IS NULL
            ORDER BY fetched_at DESC NULLS LAST
            LIMIT 1
            """
        )
        params = {"slug": slug}

    try:
        row = db.session.execute(sql, params).first()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.warning("EDHREC cache lookup failed: %s", exc)
        return None

    if not row:
        return None
    return _normalize_payload(row[0])


def _normalize_tags(tags: Iterable[str] | None) -> list[str]:
    if not tags:
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for tag in tags:
        label = (tag or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(label)
    return cleaned


def _commander_name_from_oracle(commander_oracle_id: str) -> str | None:
    primary = primary_commander_oracle_id(commander_oracle_id)
    if not primary:
        return None
    prints = sc.prints_for_oracle(primary) or ()
    if not prints:
        return None
    return prints[0].get("name")


def _oracle_id_for_name(name: str, cache: dict[str, str | None]) -> str | None:
    key = (name or "").strip().casefold()
    if not key:
        return None
    if key in cache:
        return cache[key]
    try:
        oid = sc.unique_oracle_by_name(name)
    except Exception:
        oid = None
    cache[key] = oid
    return oid


def _get_commander_synergy(commander_oracle_id: str, tags: Iterable[str] | None) -> list[dict]:
    commander_oracle_id = (commander_oracle_id or "").strip()
    if not commander_oracle_id:
        return []

    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for EDHREC recommendations: %s", exc)
        return []

    commander_name = _commander_name_from_oracle(commander_oracle_id)
    if not commander_name:
        _LOG.info("EDHREC synergy skipped; commander name unavailable for %s.", commander_oracle_id)
        return []

    slug = slugify_commander(commander_name)
    if not slug:
        _LOG.info("EDHREC synergy skipped; slugify failed for %s.", commander_name)
        return []

    base_payload = _load_commander_payload(slug, None)
    if not base_payload:
        _LOG.info("EDHREC cache missing for commander %s (%s).", commander_name, slug)
        return []

    tag_list = _normalize_tags(tags)
    tag_views: list[list] = []
    tag_matches: dict[str, set[str]] = defaultdict(set)

    for tag in tag_list:
        theme_slug = slugify_theme(tag)
        if not theme_slug:
            continue
        payload = _load_commander_payload(slug, theme_slug)
        if not payload:
            continue
        views = commander_cardviews(payload)
        tag_views.append(views)
        for view in views:
            tag_matches[view.slug].add(tag)

    base_views = commander_cardviews(base_payload)
    merged = merge_cardviews(base_views, *tag_views)
    if not merged:
        return []

    oracle_cache: dict[str, str | None] = {}
    results: list[dict] = []
    for slug_key, view in merged.items():
        oracle_id = _oracle_id_for_name(view.name, oracle_cache)
        if not oracle_id:
            continue
        matches = sorted(tag_matches.get(slug_key, set()), key=lambda s: s.lower())
        if view.tag and tag_list:
            for candidate in tag_list:
                if candidate.casefold() == view.tag.casefold() and candidate not in matches:
                    matches.append(candidate)
        results.append(
            {
                "oracle_id": oracle_id,
                "name": view.name,
                "synergy_score": float(view.synergy or 0.0),
                "source": "edhrec",
                "category": view.category,
                "tag_matches": matches,
                "edhrec_slug": view.slug,
                "edhrec_url": view.url,
                "inclusion": float(view.inclusion or 0.0) if view.inclusion is not None else None,
            }
        )

    results.sort(
        key=lambda item: (
            -(item.get("synergy_score") or 0.0),
            -(item.get("inclusion") or 0.0),
            (item.get("name") or "").lower(),
        )
    )
    return results


def get_commander_synergy(commander_oracle_id: str, tags: list[str] | None = None) -> list[dict]:
    """
    Return cached EDHREC synergy suggestions for a commander.

    Results are derived from locally cached EDHREC payloads only; no live fetches occur.
    """

    key = ("edhrec_synergy", commander_oracle_id, tuple(_normalize_tags(tags)))
    return request_cached(key, lambda: _get_commander_synergy(commander_oracle_id, tags))


__all__ = ["get_commander_synergy"]
