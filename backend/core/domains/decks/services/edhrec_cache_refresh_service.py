"""Refresh/write pipeline for the local EDHREC cache."""

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
    EdhrecCommanderTag,
    EdhrecCommanderTagCard,
    EdhrecTagCommander,
)
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services import edhrec_cache_target_service as target_service
from core.domains.decks.services.edhrec_client import (
    commander_cardviews,
    edhrec_service_enabled,
    ensure_commander_data,
)

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


def _bulk_upsert(model, rows: list[dict], index_elements: list[str], update_cols: list[str]) -> None:
    if not rows:
        return
    bind = db.session.get_bind()
    dialect = bind.dialect.name if bind is not None else ""
    table = model.__table__
    if dialect == "postgresql":
        insert_stmt = pg_insert(table).values(rows)
    elif dialect == "sqlite":
        insert_stmt = sqlite_insert(table).values(rows)
    else:
        for row in rows:
            db.session.merge(model(**row))
        return

    if update_cols:
        update_map = {col: getattr(insert_stmt.excluded, col) for col in update_cols}
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=index_elements,
            set_=update_map,
        )
    else:
        stmt = insert_stmt.on_conflict_do_nothing(index_elements=index_elements)
    db.session.execute(stmt)


def _oracle_id_for_name(name: str, cache: dict[str, str | None]) -> str | None:
    key = (name or "").strip().casefold()
    if not key:
        return None
    if key in cache:
        return cache[key]
    try:
        oracle_id = sc.unique_oracle_by_name(name)
    except Exception:
        oracle_id = None
    cache[key] = oracle_id
    return oracle_id


def refresh_edhrec_cache(
    *,
    force_refresh: bool = False,
    scope: str = "all",
    ensure_tables_fn=None,
) -> dict:
    if ensure_tables_fn is not None:
        ensure_tables_fn()

    if not edhrec_service_enabled():
        message = "EDHREC service is not configured."
        _LOG.warning(message)
        return {"status": "error", "message": message}

    scope_key = (scope or "all").strip().lower()
    if scope_key == "all":
        try:
            targets = target_service.collect_edhrec_index_targets(include_themes=True)
        except Exception as exc:
            _LOG.warning("EDHREC index lookup failed: %s", exc)
            return {"status": "error", "message": "EDHREC index lookup failed."}
    else:
        try:
            targets = target_service.collect_edhrec_targets()
        except SQLAlchemyError:
            db.session.rollback()
            _LOG.error("EDHREC refresh failed due to database error.", exc_info=True)
            return {"status": "error", "message": "Database error while collecting EDHREC targets."}

    tag_inserted = target_service.upsert_index_tags(targets.get("tags") or []) if scope_key == "all" else 0
    commander_targets = targets.get("commanders") or []
    if not commander_targets:
        message = (
            "No commander data found to refresh."
            if scope_key != "all"
            else "No commander data found from EDHREC index."
        )
        _LOG.warning(message)
        return {
            "status": "info",
            "message": message,
            "targets": targets,
            "tags_inserted": tag_inserted,
        }

    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for EDHREC refresh: %s", exc)
        return {"status": "error", "message": "Scryfall cache unavailable; refresh card cache first."}

    errors: list[str] = []
    commander_ok = 0
    total_cards = 0
    total_tags = 0
    total_tag_cards = 0
    oracle_cache: dict[str, str | None] = {}

    total_targets = len(commander_targets)
    for idx, target in enumerate(commander_targets, start=1):
        if isinstance(target, dict):
            commander_name = (target.get("name") or "").strip()
            commander_oracle_id = (target.get("oracle_id") or "").strip()
            slug_override = (target.get("slug") or "").strip()
        else:
            commander_name = str(target or "").strip()
            commander_oracle_id = ""
            slug_override = ""

        if not commander_name:
            continue

        if not commander_oracle_id:
            commander_oracle_id = _oracle_id_for_name(commander_name, oracle_cache) or ""
        if not commander_oracle_id:
            continue

        slug, payload, warning = ensure_commander_data(
            commander_name,
            force_refresh=force_refresh,
            slug_override=slug_override or None,
        )
        if warning:
            errors.append(warning)
        if not payload:
            errors.append(f"EDHREC data missing for {commander_name}.")
            continue

        views = commander_cardviews(payload)
        card_map: dict[str, dict] = {}
        for view in views:
            oracle_id = _oracle_id_for_name(view.name, oracle_cache)
            if not oracle_id:
                continue
            score = float(view.synergy or 0.0)
            inclusion = float(view.inclusion) if view.inclusion is not None else None
            existing = card_map.get(oracle_id)
            if existing is None or score > existing.get("synergy_score", 0.0):
                card_map[oracle_id] = {"synergy_score": score, "inclusion_percent": inclusion}
            elif score == existing.get("synergy_score", 0.0) and inclusion is not None:
                if (existing.get("inclusion_percent") or 0) < inclusion:
                    existing["inclusion_percent"] = inclusion

        top_cards = sorted(
            card_map.items(),
            key=lambda item: (
                -(item[1].get("synergy_score") or 0.0),
                -(item[1].get("inclusion_percent") or 0.0),
            ),
        )
        if _MAX_SYNERGY_CARDS is not None:
            top_cards = top_cards[:_MAX_SYNERGY_CARDS]

        tag_entries = target_service.extract_commander_tag_entries(payload)
        commander_tags = [entry["tag"] for entry in tag_entries]

        card_rows = []
        for rank, (oracle_id, values) in enumerate(top_cards, start=1):
            card_rows.append(
                {
                    "commander_oracle_id": commander_oracle_id,
                    "card_oracle_id": oracle_id,
                    "synergy_rank": rank,
                    "synergy_score": values.get("synergy_score"),
                    "inclusion_percent": values.get("inclusion_percent"),
                }
            )
        tag_rows = [
            {"commander_oracle_id": commander_oracle_id, "tag": tag}
            for tag in commander_tags
        ]
        tag_card_rows: dict[str, list[dict]] = {}
        for entry in tag_entries:
            tag = entry.get("tag")
            slug_value = entry.get("slug")
            if not tag or not slug_value:
                continue
            tag_slug = str(slug_value).strip().lower()
            if not tag_slug:
                continue
            _, tag_payload, tag_warning = ensure_commander_data(
                commander_name,
                theme_slug=tag_slug,
                force_refresh=force_refresh,
                slug_override=slug_override or None,
            )
            if tag_warning:
                errors.append(tag_warning)
            if not tag_payload:
                continue
            tag_views = commander_cardviews(tag_payload)
            tag_card_map: dict[str, dict] = {}
            for view in tag_views:
                oracle_id = _oracle_id_for_name(view.name, oracle_cache)
                if not oracle_id:
                    continue
                score = float(view.synergy or 0.0)
                inclusion = float(view.inclusion) if view.inclusion is not None else None
                existing = tag_card_map.get(oracle_id)
                if existing is None or score > existing.get("synergy_score", 0.0):
                    tag_card_map[oracle_id] = {"synergy_score": score, "inclusion_percent": inclusion}
                elif score == existing.get("synergy_score", 0.0) and inclusion is not None:
                    if (existing.get("inclusion_percent") or 0) < inclusion:
                        existing["inclusion_percent"] = inclusion
            top_tag_cards = sorted(
                tag_card_map.items(),
                key=lambda item: (
                    -(item[1].get("synergy_score") or 0.0),
                    -(item[1].get("inclusion_percent") or 0.0),
                ),
            )
            if _MAX_SYNERGY_CARDS is not None:
                top_tag_cards = top_tag_cards[:_MAX_SYNERGY_CARDS]
            rows = []
            for rank, (oracle_id, values) in enumerate(top_tag_cards, start=1):
                rows.append(
                    {
                        "commander_oracle_id": commander_oracle_id,
                        "tag": tag,
                        "card_oracle_id": oracle_id,
                        "synergy_rank": rank,
                        "synergy_score": values.get("synergy_score"),
                        "inclusion_percent": values.get("inclusion_percent"),
                    }
                )
            if rows:
                tag_card_rows[tag] = rows
                total_tag_cards += len(rows)

        try:
            with db.session.no_autoflush:
                EdhrecCommanderCard.query.filter_by(
                    commander_oracle_id=commander_oracle_id
                ).delete(synchronize_session=False)
                _bulk_upsert(
                    EdhrecCommanderCard,
                    card_rows,
                    ["commander_oracle_id", "card_oracle_id"],
                    ["synergy_rank", "synergy_score", "inclusion_percent"],
                )

                EdhrecCommanderTag.query.filter_by(
                    commander_oracle_id=commander_oracle_id
                ).delete(synchronize_session=False)
                _bulk_upsert(
                    EdhrecCommanderTag,
                    tag_rows,
                    ["commander_oracle_id", "tag"],
                    [],
                )
                for tag, rows in tag_card_rows.items():
                    EdhrecCommanderTagCard.query.filter_by(
                        commander_oracle_id=commander_oracle_id,
                        tag=tag,
                    ).delete(synchronize_session=False)
                    _bulk_upsert(
                        EdhrecCommanderTagCard,
                        rows,
                        ["commander_oracle_id", "tag", "card_oracle_id"],
                        ["synergy_rank", "synergy_score", "inclusion_percent"],
                    )
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            _LOG.warning("Failed to store EDHREC cache for %s: %s", commander_name, exc)
            errors.append(f"Failed to cache {commander_name}.")
            continue

        commander_ok += 1
        total_cards += len(top_cards)
        total_tags += len(commander_tags)
        if idx == total_targets or idx % 100 == 0:
            _LOG.info(
                "EDHREC refresh progress: %s/%s commanders processed.",
                idx,
                total_targets,
            )

    tag_count = 0
    try:
        EdhrecTagCommander.query.delete(synchronize_session=False)
        rows = db.session.query(EdhrecCommanderTag.tag, EdhrecCommanderTag.commander_oracle_id).all()
        if rows:
            db.session.bulk_save_objects(
                [
                    EdhrecTagCommander(tag=tag, commander_oracle_id=oracle_id)
                    for tag, oracle_id in rows
                ]
            )
        tag_count = db.session.query(
            func.count(func.distinct(EdhrecCommanderTag.tag))
        ).scalar() or 0
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.warning("Failed to rebuild EDHREC tag commanders: %s", exc)
        errors.append("Failed to rebuild EDHREC tag index.")

    status = "success" if commander_ok else "warning"
    message = f"EDHREC cache updated for {commander_ok}/{len(commander_targets)} commanders."
    if errors and status == "success":
        status = "warning"

    return {
        "status": status,
        "message": message,
        "errors": errors,
        "commanders": {
            "requested": len(commander_targets),
            "ok": commander_ok,
            "cards": total_cards,
        },
        "tags": {
            "count": int(tag_count),
            "links": total_tags,
            "tag_cards": total_tag_cards,
        },
        "targets": targets,
    }


__all__ = ["refresh_edhrec_cache"]
