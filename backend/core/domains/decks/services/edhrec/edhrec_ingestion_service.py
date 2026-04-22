"""Monthly EDHREC ingestion for commander synergy and tag data."""

from __future__ import annotations

import logging
import os

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import (
    EdhrecCommanderCard,
    EdhrecCommanderCategoryCard,
    EdhrecCommanderTagCard,
    EdhrecCommanderTagCategoryCard,
    EdhrecCommanderTypeDistribution,
)
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.deck_tags import ensure_deck_tag, normalize_tag_label
from core.domains.decks.services.edhrec import (
    edhrec_ingestion_fetch_service as fetch_service,
    edhrec_ingestion_persistence_service as persistence_service,
    edhrec_tag_refresh_service,
    edhrec_payload_service,
    edhrec_target_service,
)
from core.domains.decks.services.edhrec_client import edhrec_index, edhrec_service_enabled

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
_MISSING_TTL_DAYS = int(os.getenv("EDHREC_MISSING_TTL_DAYS", "30"))
_TOP_COMMANDER_LIMIT = int(os.getenv("EDHREC_TOP_COMMANDER_LIMIT", "500"))
_TOP_COMMANDER_TAG_LIMIT = int(os.getenv("EDHREC_TOP_COMMANDER_TAG_LIMIT", "5"))
_USE_INDEX_SLUGS = _bool_env("EDHREC_USE_INDEX_SLUGS", True)
_INDEX_ONLY = _bool_env("EDHREC_INDEX_ONLY", False)
_INCLUDE_TOP_COMMANDERS = _bool_env("EDHREC_INCLUDE_TOP_COMMANDERS", True)
CommanderTarget = edhrec_target_service.CommanderTarget


def _upsert_index_theme_tags() -> int:
    if not edhrec_service_enabled():
        return 0
    try:
        index = edhrec_index(include_commanders=False, include_themes=True)
    except Exception as exc:
        _LOG.warning("EDHREC index theme lookup failed: %s", exc)
        return 0
    themes = index.get("themes") or []
    seen: set[str] = set()
    inserted = 0
    for entry in themes:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("label")
        else:
            name = entry
        cleaned = normalize_tag_label(name or "")
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        tag_row = ensure_deck_tag(cleaned, source="edhrec")
        if tag_row and tag_row.id is None:
            inserted += 1
    if inserted:
        try:
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            _LOG.warning("EDHREC index theme upsert failed: %s", exc)
            return 0
    return inserted


def _commander_existing_sets(
    *,
    full_refresh: bool,
    tag_map: dict[str, set[str]],
    top_limits: dict[str, int],
) -> tuple[set[str], set[tuple[str, str]], set[str], set[tuple[str, str]], dict[str, int]]:
    existing_ids: set[str] = set()
    existing_tag_pairs: set[tuple[str, str]] = set()
    existing_category_ids: set[str] = set()
    existing_tag_category_pairs: set[tuple[str, str]] = set()
    existing_top_counts: dict[str, int] = {}
    if full_refresh:
        return (
            existing_ids,
            existing_tag_pairs,
            existing_category_ids,
            existing_tag_category_pairs,
            existing_top_counts,
        )

    existing_ids = {
        row[0]
        for row in db.session.query(EdhrecCommanderCard.commander_oracle_id).distinct().all()
        if row and row[0]
    }
    existing_category_ids = {
        row[0]
        for row in db.session.query(EdhrecCommanderCategoryCard.commander_oracle_id).distinct().all()
        if row and row[0]
    }
    if tag_map:
        existing_tag_pairs = {
            (row[0], row[1])
            for row in db.session.query(
                EdhrecCommanderTagCard.commander_oracle_id,
                EdhrecCommanderTagCard.tag,
            )
            .distinct()
            .all()
            if row and row[0] and row[1]
        }
        existing_tag_category_pairs = {
            (row[0], row[1])
            for row in db.session.query(
                EdhrecCommanderTagCategoryCard.commander_oracle_id,
                EdhrecCommanderTagCategoryCard.tag,
            )
            .distinct()
            .all()
            if row and row[0] and row[1]
        }
    if top_limits:
        top_ids = list(top_limits.keys())
        rows = (
            db.session.query(
                EdhrecCommanderTagCard.commander_oracle_id,
                func.count(func.distinct(EdhrecCommanderTagCard.tag)),
            )
            .filter(EdhrecCommanderTagCard.commander_oracle_id.in_(top_ids))
            .group_by(EdhrecCommanderTagCard.commander_oracle_id)
            .all()
        )
        existing_top_counts = {
            row[0]: int(row[1] or 0)
            for row in rows
            if row and row[0]
        }
    return (
        existing_ids,
        existing_tag_pairs,
        existing_category_ids,
        existing_tag_category_pairs,
        existing_top_counts,
    )


def run_monthly_edhrec_ingestion(
    limit: int | None = None,
    *,
    full_refresh: bool = True,
    scope: str = "all",
) -> dict:
    """
    Run the EDHREC commander ingestion job.

    full_refresh=True refreshes all commanders.
    full_refresh=False only ingests commanders missing cached data.
    scope="delta" limits ingestion to commanders/tags used by current decks.
    """
    persistence_service.ensure_schema()
    scope_key = (scope or "all").strip().lower()
    index_tags_inserted = _upsert_index_theme_tags()
    if scope_key in {"themes", "tags", "index"}:
        return {
            "commanders_processed": 0,
            "cards_inserted": 0,
            "tags_inserted": 0,
            "tag_cards_inserted": 0,
            "index_tags_inserted": index_tags_inserted,
            "errors": 0,
        }

    tag_map: dict[str, set[str]] = {}
    retry_missing = False
    if scope_key in {"delta", "active", "deck", "current"}:
        targets, tag_map = edhrec_target_service.load_active_targets(use_index_slugs=_USE_INDEX_SLUGS)
    elif scope_key in {"missing", "failed"}:
        retry_missing = True
        targets = edhrec_target_service.load_commander_targets(
            use_index_slugs=_USE_INDEX_SLUGS,
            index_only=_INDEX_ONLY,
        )
    else:
        targets = edhrec_target_service.load_commander_targets(
            use_index_slugs=_USE_INDEX_SLUGS,
            index_only=_INDEX_ONLY,
        )

    top_limits: dict[str, int] = {}
    if scope_key not in {"missing", "failed"} and _INCLUDE_TOP_COMMANDERS and _TOP_COMMANDER_LIMIT > 0:
        top_targets = edhrec_target_service.load_top_index_targets(_TOP_COMMANDER_LIMIT)
        if _TOP_COMMANDER_TAG_LIMIT > 0:
            top_limits = {target.oracle_id: _TOP_COMMANDER_TAG_LIMIT for target in top_targets}
        if top_targets:
            merged_targets = {target.oracle_id: target for target in targets}
            for target in top_targets:
                merged_targets.setdefault(target.oracle_id, target)
            targets = list(merged_targets.values())
    if _TOP_COMMANDER_TAG_LIMIT > 0:
        for target in targets:
            top_limits.setdefault(target.oracle_id, _TOP_COMMANDER_TAG_LIMIT)
    if not targets:
        return {
            "commanders_processed": 0,
            "cards_inserted": 0,
            "tags_inserted": 0,
            "errors": 1,
            "index_tags_inserted": index_tags_inserted,
        }

    (
        existing_ids,
        existing_tag_pairs,
        existing_category_ids,
        existing_tag_category_pairs,
        existing_top_counts,
    ) = _commander_existing_sets(
        full_refresh=full_refresh,
        tag_map=tag_map,
        top_limits=top_limits,
    )

    if not full_refresh:
        filtered: list[CommanderTarget] = []
        for target in targets:
            needs_commander = (
                target.oracle_id not in existing_ids
                or target.oracle_id not in existing_category_ids
            )
            tag_set = tag_map.get(target.oracle_id, set())
            needs_tags = any(
                (target.oracle_id, tag) not in existing_tag_pairs
                or (target.oracle_id, tag) not in existing_tag_category_pairs
                for tag in tag_set
            )
            top_limit = top_limits.get(target.oracle_id, 0)
            needs_top_tags = bool(top_limit) and (existing_top_counts.get(target.oracle_id, 0) < top_limit)
            if needs_commander or needs_tags or needs_top_tags:
                filtered.append(target)
        targets = filtered

    if limit is not None and limit > 0:
        targets = targets[:limit]

    session = fetch_service.build_edhrec_session()
    commanders_processed = 0
    cards_inserted = 0
    tags_inserted = 0
    tag_cards_inserted = 0
    errors = 0
    last_request_at = 0.0
    missing_slugs = edhrec_target_service.prune_missing_slugs(
        edhrec_target_service.load_missing_slugs(),
        _MISSING_TTL_DAYS,
    )
    if scope_key in {"missing", "failed"}:
        missing_oracle_ids = edhrec_target_service.missing_oracle_ids(missing_slugs)
        if not missing_oracle_ids:
            return {
                "commanders_processed": 0,
                "cards_inserted": 0,
                "tags_inserted": 0,
                "tag_cards_inserted": 0,
                "errors": 0,
            }
        targets = [target for target in targets if target.oracle_id in missing_oracle_ids]

    for idx, target in enumerate(targets, start=1):
        slug_candidates = edhrec_target_service.slug_candidates_for_target(target)
        if not slug_candidates:
            errors += 1
            _LOG.warning("EDHREC slug missing for %s", target.name)
            continue

        candidates_to_try = fetch_service.filter_slug_candidates(
            slug_candidates,
            missing_slugs=missing_slugs,
            retry_missing=retry_missing,
        )
        if not candidates_to_try:
            continue

        tags_for_commander = tag_map.get(target.oracle_id, set())
        top_tag_limit = top_limits.get(target.oracle_id, 0)
        needs_commander = full_refresh or target.oracle_id not in existing_ids
        needs_top_tags = bool(top_tag_limit) and (
            full_refresh or existing_top_counts.get(target.oracle_id, 0) < top_tag_limit
        )

        commander_rows = {
            "synergy_rows": [],
            "category_rows": [],
            "tags": [],
            "commander_type_rows": [],
        }
        slug_base = candidates_to_try[0]
        if needs_commander or needs_top_tags:
            primary = fetch_service.fetch_commander_bundle(
                session,
                target_name=target.name,
                target_oracle_id=target.oracle_id,
                candidates_to_try=candidates_to_try,
                last_request_at=last_request_at,
                interval_seconds=_REQUEST_INTERVAL_SECONDS,
                lookup_oracle_id_fn=sc.unique_oracle_by_name,
                max_synergy_cards=_MAX_SYNERGY_CARDS,
                missing_slugs=missing_slugs,
                now_iso_fn=persistence_service.now_iso,
            )
            last_request_at = primary["last_request_at"]
            if primary["fetch_error"] and not primary["slug_used"]:
                errors += 1
                _LOG.warning("EDHREC fetch failed for %s: %s", target.name, primary["fetch_error"])
                continue
            if not primary["payload"] or not primary["raw_json"]:
                errors += 1
                _LOG.warning("EDHREC payload missing for %s", target.name)
                continue
            edhrec_target_service.clear_missing_for_oracle(missing_slugs, target.oracle_id)
            commander_rows = primary["commander_rows"]
            slug_base = primary["slug_used"] or slug_base

        top_tags: list[str] = []
        if needs_top_tags and commander_rows["tags"]:
            top_tags = commander_rows["tags"][:top_tag_limit]

        tags_to_fetch = edhrec_payload_service.merge_tags(tags_for_commander, top_tags)
        if not full_refresh:
            tags_to_fetch = [
                tag
                for tag in tags_to_fetch
                if (target.oracle_id, tag) not in existing_tag_pairs
            ]

        tag_rows = fetch_service.fetch_tag_rows(
            session,
            target_name=target.name,
            tag_names=tags_to_fetch,
            slug_base=slug_base,
            last_request_at=last_request_at,
            interval_seconds=_REQUEST_INTERVAL_SECONDS,
            lookup_oracle_id_fn=sc.unique_oracle_by_name,
            max_synergy_cards=_MAX_SYNERGY_CARDS,
            logger=_LOG,
        )
        last_request_at = tag_rows["last_request_at"]

        try:
            persistence_service.persist_monthly_commander_rows(
                target.oracle_id,
                needs_commander=needs_commander,
                synergy_rows=commander_rows["synergy_rows"],
                category_rows=commander_rows["category_rows"],
                tags=commander_rows["tags"],
                commander_type_rows=commander_rows["commander_type_rows"],
                tag_card_rows=tag_rows["tag_card_rows"],
                tag_category_rows=tag_rows["tag_category_rows"],
                tag_type_rows=tag_rows["tag_type_rows"],
            )
        except SQLAlchemyError as exc:
            errors += 1
            _LOG.warning("EDHREC cache write failed for %s: %s", target.name, exc)
            continue

        commanders_processed += 1
        cards_inserted += len(commander_rows["synergy_rows"])
        tags_inserted += len(commander_rows["tags"])
        tag_cards_inserted += tag_rows["tag_cards_added"]

        if idx == len(targets) or idx % 50 == 0:
            _LOG.info("EDHREC ingestion progress: %s/%s commanders.", idx, len(targets))

    try:
        persistence_service.finalize_monthly_ingestion(
            missing_slugs,
            default_source_version=_DEFAULT_SOURCE_VERSION,
            now_iso_fn=persistence_service.now_iso,
        )
    except SQLAlchemyError as exc:
        errors += 1
        _LOG.warning("EDHREC tag mapping rebuild failed: %s", exc)

    return {
        "commanders_processed": commanders_processed,
        "cards_inserted": cards_inserted,
        "tags_inserted": tags_inserted,
        "tag_cards_inserted": tag_cards_inserted,
        "index_tags_inserted": index_tags_inserted,
        "errors": errors,
    }


ingest_commander_tag_data = edhrec_tag_refresh_service.ingest_commander_tag_data


__all__ = ["run_monthly_edhrec_ingestion", "ingest_commander_tag_data"]
