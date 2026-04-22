"""Local EDHREC cache management for deck recommendations."""

from __future__ import annotations

import logging
import os

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import (
    EdhrecCommanderCard,
    EdhrecCommanderTypeDistribution,
    EdhrecCommanderTag,
    EdhrecCommanderTagCard,
    EdhrecMetadata,
)
from core.domains.decks.services import edhrec_cache_query_service
from core.domains.decks.services import edhrec_cache_refresh_service
from core.domains.decks.services import edhrec_cache_target_service as target_service
from core.domains.decks.services.edhrec import edhrec_ingestion_persistence_service as persistence_service

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


_MAX_SYNERGY_CARDS = _parse_max_cards_env("EDHREC_CACHE_MAX_CARDS", None)
def _ensure_tables() -> None:
    try:
        persistence_service.ensure_schema()
    except Exception as exc:
        _LOG.error("Failed to ensure EDHREC cache tables: %s", exc)


def collect_edhrec_targets() -> dict:
    return target_service.collect_edhrec_targets()


def collect_edhrec_index_targets(*, include_themes: bool = True) -> dict:
    return target_service.collect_edhrec_index_targets(include_themes=include_themes)
def refresh_edhrec_cache(*, force_refresh: bool = False, scope: str = "all") -> dict:
    return edhrec_cache_refresh_service.refresh_edhrec_cache(
        force_refresh=force_refresh,
        scope=scope,
        ensure_tables_fn=_ensure_tables,
    )


def edhrec_cache_snapshot() -> dict:
    _ensure_tables()
    try:
        commander_count = db.session.query(
            func.count(func.distinct(EdhrecCommanderCard.commander_oracle_id))
        ).scalar()
        card_rows = db.session.query(func.count(EdhrecCommanderCard.card_oracle_id)).scalar()
        tag_card_rows = db.session.query(func.count(EdhrecCommanderTagCard.card_oracle_id)).scalar()
        tag_count = db.session.query(
            func.count(func.distinct(EdhrecCommanderTag.tag))
        ).scalar()
        meta_rows = db.session.query(EdhrecMetadata).all()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.warning("Failed to read EDHREC cache snapshot: %s", exc)
        return {"status": "error", "error": "Unable to read EDHREC cache."}

    metadata = {row.key: row.value for row in meta_rows if row.key and row.value}
    return {
        "status": "ok",
        "commanders": {"count": int(commander_count or 0), "cards": int(card_rows or 0)},
        "tags": {"count": int(tag_count or 0), "tag_cards": int(tag_card_rows or 0)},
        "metadata": metadata,
    }


def get_commander_tags(commander_oracle_id: str) -> list[str]:
    return edhrec_cache_query_service.get_commander_tags(
        commander_oracle_id,
        ensure_tables_fn=_ensure_tables,
    )


def get_commander_type_distribution(
    commander_oracle_id: str,
    *,
    tag: str | None = None,
) -> list[tuple[str, int]]:
    return edhrec_cache_query_service.get_commander_type_distribution(
        commander_oracle_id,
        tag=tag,
        ensure_tables_fn=_ensure_tables,
    )


def get_tag_commanders(tag: str) -> list[str]:
    return edhrec_cache_query_service.get_tag_commanders(
        tag,
        ensure_tables_fn=_ensure_tables,
    )


def get_commander_category_groups(
    commander_oracle_id: str,
    *,
    tag: str | None = None,
    limit: int | None = _MAX_SYNERGY_CARDS,
) -> list[dict]:
    return edhrec_cache_query_service.get_commander_category_groups(
        commander_oracle_id,
        tag=tag,
        limit=limit,
        ensure_tables_fn=_ensure_tables,
    )


def get_commander_tag_synergy_groups(
    commander_oracle_id: str,
    tags: Iterable[str] | None = None,
    *,
    limit: int | None = _MAX_SYNERGY_CARDS,
) -> list[dict]:
    return edhrec_cache_query_service.get_commander_tag_synergy_groups(
        commander_oracle_id,
        tags,
        limit=limit,
        ensure_tables_fn=_ensure_tables,
    )


def get_commander_synergy(
    commander_oracle_id: str,
    tags: list[str] | None = None,
    *,
    prefer_tag_specific: bool = False,
    limit: int | None = _MAX_SYNERGY_CARDS,
) -> list[dict]:
    return edhrec_cache_query_service.get_commander_synergy(
        commander_oracle_id,
        tags,
        prefer_tag_specific=prefer_tag_specific,
        limit=limit,
        ensure_tables_fn=_ensure_tables,
    )


def cache_ready() -> bool:
    return edhrec_cache_query_service.cache_ready(
        edhrec_cache_snapshot_fn=edhrec_cache_snapshot,
    )


__all__ = [
    "collect_edhrec_targets",
    "refresh_edhrec_cache",
    "edhrec_cache_snapshot",
    "get_commander_synergy",
    "get_commander_tags",
    "get_commander_type_distribution",
    "get_commander_category_groups",
    "get_commander_tag_synergy_groups",
    "get_tag_commanders",
    "cache_ready",
]
