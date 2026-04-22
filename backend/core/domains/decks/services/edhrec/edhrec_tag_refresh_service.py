"""Commander-specific EDHREC refresh helpers."""

from __future__ import annotations

import logging
import os
from typing import Iterable

from sqlalchemy.exc import SQLAlchemyError

from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.deck_tags import ensure_deck_tag
from . import edhrec_ingestion_fetch_service as fetch_service
from . import edhrec_ingestion_persistence_service as persistence_service
from . import edhrec_target_service

_LOG = logging.getLogger(__name__)


def _parse_max_cards_env(name: str, default: int | None) -> int | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"0", "none", "null", "all", "unlimited"}:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else None


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


_REQUEST_INTERVAL_SECONDS = max(1.0, float(os.getenv("EDHREC_INGEST_INTERVAL", "1.0")))
_MAX_SYNERGY_CARDS = _parse_max_cards_env("EDHREC_INGEST_MAX_CARDS", None)
_DEFAULT_SOURCE_VERSION = os.getenv("EDHREC_SOURCE_VERSION")
_USE_INDEX_SLUGS = _bool_env("EDHREC_USE_INDEX_SLUGS", True)


def normalize_requested_tags(tags: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        candidate = edhrec_target_service.normalize_deck_tag(tag)
        if not candidate:
            continue
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        tag_row = ensure_deck_tag(candidate, source="user")
        normalized.append(tag_row.name if tag_row else candidate)
    return normalized


def ingest_commander_tag_data(
    commander_oracle_id: str,
    commander_name: str | None,
    tags: Iterable[str] | None,
    *,
    force_refresh: bool = True,
) -> dict:
    persistence_service.ensure_schema()
    oracle_id = (commander_oracle_id or "").strip()
    if not oracle_id:
        return {"status": "error", "message": "Commander is missing."}

    cache_ready = False
    try:
        cache_ready = sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for EDHREC fetch: %s", exc)

    index_slugs = edhrec_target_service.load_edhrec_index_slugs() if _USE_INDEX_SLUGS else {}
    target = edhrec_target_service.commander_target_from_oracle(
        oracle_id,
        commander_name,
        index_slugs=index_slugs,
        cache_ready=cache_ready,
    )
    if not target:
        return {"status": "error", "message": "Commander not found in cache."}

    requested_tags = normalize_requested_tags(tags)
    if not force_refresh and persistence_service.commander_tag_refresh_ready(target.oracle_id, requested_tags):
        return {"status": "ok", "message": "EDHREC data already cached."}

    slug_candidates = edhrec_target_service.slug_candidates_for_target(target)
    if not slug_candidates:
        return {"status": "error", "message": "Unable to derive EDHREC slug."}

    session = fetch_service.build_edhrec_session()
    primary = fetch_service.fetch_commander_bundle(
        session,
        target_name=target.name,
        target_oracle_id=target.oracle_id,
        candidates_to_try=slug_candidates,
        last_request_at=0.0,
        interval_seconds=_REQUEST_INTERVAL_SECONDS,
        lookup_oracle_id_fn=sc.unique_oracle_by_name,
        max_synergy_cards=_MAX_SYNERGY_CARDS,
    )
    if primary["fetch_error"] and not primary["slug_used"]:
        _LOG.warning("EDHREC fetch failed for %s: %s", target.name, primary["fetch_error"])
        return {"status": "error", "message": primary["fetch_error"]}
    if not primary["payload"] or not primary["raw_json"]:
        _LOG.warning("EDHREC payload missing for %s", target.name)
        return {"status": "error", "message": "EDHREC payload missing."}

    tag_rows = fetch_service.fetch_tag_rows(
        session,
        target_name=target.name,
        tag_names=requested_tags,
        slug_base=primary["slug_used"] or slug_candidates[0],
        last_request_at=primary["last_request_at"],
        interval_seconds=_REQUEST_INTERVAL_SECONDS,
        lookup_oracle_id_fn=sc.unique_oracle_by_name,
        max_synergy_cards=_MAX_SYNERGY_CARDS,
        logger=_LOG,
    )

    commander_rows = primary["commander_rows"]
    try:
        persistence_service.persist_commander_tag_refresh(
            target.oracle_id,
            synergy_rows=commander_rows["synergy_rows"],
            category_rows=commander_rows["category_rows"],
            tags=commander_rows["tags"],
            commander_type_rows=commander_rows["commander_type_rows"],
            tag_card_rows=tag_rows["tag_card_rows"],
            tag_category_rows=tag_rows["tag_category_rows"],
            tag_type_rows=tag_rows["tag_type_rows"],
            default_source_version=_DEFAULT_SOURCE_VERSION,
            now_iso_fn=persistence_service.now_iso,
        )
    except SQLAlchemyError as exc:
        _LOG.warning("EDHREC cache write failed for %s: %s", target.name, exc)
        return {"status": "error", "message": "Database error while saving EDHREC data."}

    return {
        "status": "ok",
        "message": f"EDHREC data refreshed for {target.name}.",
        "cards_inserted": len(commander_rows["synergy_rows"]),
        "tags_inserted": len(commander_rows["tags"]),
        "tag_cards_inserted": tag_rows["tag_cards_added"],
    }


__all__ = ["ingest_commander_tag_data", "normalize_requested_tags"]
