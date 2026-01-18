"""Local EDHREC cache management for deck recommendations."""

from __future__ import annotations

import logging
import os
from typing import Iterable

from sqlalchemy import func, inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import (
    EdhrecCommanderCard,
    EdhrecCommanderCategoryCard,
    EdhrecCommanderTypeDistribution,
    EdhrecCommanderTag,
    EdhrecCommanderTagCard,
    EdhrecCommanderTagCategoryCard,
    EdhrecTagCommander,
    EdhrecMetadata,
    Folder,
)
from services import scryfall_cache as sc
from services.commander_utils import primary_commander_name, primary_commander_oracle_id
from services.deck_tags import ensure_deck_tag, normalize_tag_label, resolve_deck_tag_from_slug
from services.edhrec_client import (
    commander_cardviews,
    edhrec_index,
    edhrec_service_enabled,
    ensure_commander_data,
    slugify_theme,
)
from services.request_cache import request_cached

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


def _synergy_percent(score: float | None) -> float | None:
    if score is None:
        return None
    return round(score * 100.0, 1)


def _ensure_tables() -> None:
    try:
        db.metadata.create_all(
            db.engine,
            tables=[
                EdhrecCommanderCard.__table__,
                EdhrecCommanderCategoryCard.__table__,
                EdhrecCommanderTag.__table__,
                EdhrecCommanderTagCard.__table__,
                EdhrecCommanderTagCategoryCard.__table__,
                EdhrecTagCommander.__table__,
                EdhrecCommanderTypeDistribution.__table__,
                EdhrecMetadata.__table__,
            ],
        )
        _ensure_columns()
    except Exception as exc:
        _LOG.error("Failed to ensure EDHREC cache tables: %s", exc)


def _ensure_columns() -> None:
    try:
        inspector = inspect(db.engine)
        columns = {col["name"] for col in inspector.get_columns("edhrec_commander_cards")}
        if "synergy_rank" not in columns:
            db.session.execute(text("ALTER TABLE edhrec_commander_cards ADD COLUMN synergy_rank INTEGER"))
        if "inclusion_percent" not in columns:
            db.session.execute(text("ALTER TABLE edhrec_commander_cards ADD COLUMN inclusion_percent FLOAT"))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _LOG.warning("Failed to ensure EDHREC cache columns: %s", exc)

    def _ensure_inclusion_column(table: str) -> None:
        try:
            table_columns = {col["name"] for col in inspector.get_columns(table)}
            if "inclusion_percent" not in table_columns:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN inclusion_percent FLOAT"))
                db.session.commit()
        except Exception as exc:
            db.session.rollback()
            _LOG.warning("Failed to ensure EDHREC cache columns for %s: %s", table, exc)

    _ensure_inclusion_column("edhrec_commander_tag_cards")
    _ensure_inclusion_column("edhrec_commander_category_cards")
    _ensure_inclusion_column("edhrec_commander_tag_category_cards")


def _inclusion_percent(value: float | None) -> float | None:
    if value is None:
        return None
    clamped = min(max(float(value), 0.0), 100.0)
    return round(clamped, 1)


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


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        label = (value or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(label)
    return ordered


def _commander_name_from_oracle(oracle_id: str) -> str | None:
    if not oracle_id:
        return None
    try:
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return None
    if prints:
        return prints[0].get("name")
    return None


def collect_edhrec_targets() -> dict:
    folders = Folder.query.order_by(func.lower(Folder.name)).all()
    deck_folders = [folder for folder in folders if not folder.is_collection]
    commander_targets: list[dict] = []
    tag_names: list[str] = []
    with_commander = 0
    with_tag = 0

    cache_ready = False
    for folder in deck_folders:
        tag = (folder.deck_tag or "").strip()
        if tag:
            with_tag += 1
            tag_names.append(tag)

        commander_oracle_id = primary_commander_oracle_id(folder.commander_oracle_id)
        commander_name = primary_commander_name(folder.commander_name)
        if not commander_name and commander_oracle_id:
            try:
                cache_ready = cache_ready or sc.ensure_cache_loaded()
                if cache_ready:
                    commander_name = _commander_name_from_oracle(commander_oracle_id)
            except Exception:
                commander_name = None

        if commander_oracle_id and commander_name:
            with_commander += 1
            commander_targets.append(
                {"oracle_id": commander_oracle_id, "name": commander_name}
            )

    deduped_targets: list[dict] = []
    seen_oracles: set[str] = set()
    for target in commander_targets:
        oracle_id = (target.get("oracle_id") or "").strip()
        if not oracle_id:
            continue
        key = oracle_id.casefold()
        if key in seen_oracles:
            continue
        seen_oracles.add(key)
        deduped_targets.append(target)

    return {
        "deck_total": len(deck_folders),
        "with_commander": with_commander,
        "with_tag": with_tag,
        "commanders": deduped_targets,
        "tags": _dedupe(tag_names),
    }


def collect_edhrec_index_targets(*, include_themes: bool = True) -> dict:
    index = edhrec_index(
        include_commanders=True,
        include_themes=include_themes,
    )
    commanders = index.get("commanders") or []
    tag_names: list[str] = []
    if include_themes:
        for entry in index.get("themes") or []:
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str) and name.strip():
                    tag_names.append(name.strip())

    deduped_tags = _dedupe(tag_names)
    return {
        "source": "edhrec",
        "commanders": commanders,
        "tags": deduped_tags,
        "commanders_total": len(commanders),
        "tags_total": len(deduped_tags),
    }


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


def _extract_commander_tag_entries(payload: dict) -> list[dict]:
    raw_options = payload.get("theme_options") or []
    if not isinstance(raw_options, list):
        return []
    entries: dict[str, dict] = {}
    for entry in raw_options:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug") or ""
        label = entry.get("label") or ""
        candidate = resolve_deck_tag_from_slug(str(slug))
        if not candidate:
            candidate = resolve_deck_tag_from_slug(str(label))
        if candidate:
            tag = candidate
            slug_value = str(slug or "").strip().lower() or slugify_theme(tag)
            if tag not in entries:
                entries[tag] = {"tag": tag, "slug": slug_value}
    return list(entries.values())


def _extract_commander_tags(payload: dict) -> list[str]:
    return [entry["tag"] for entry in _extract_commander_tag_entries(payload)]


def _upsert_index_tags(tag_names: Iterable[str]) -> int:
    inserted = 0
    seen: set[str] = set()
    for tag in tag_names or []:
        cleaned = normalize_tag_label(tag)
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
            _LOG.warning("Failed to store EDHREC index tags: %s", exc)
            return 0
    return inserted


def refresh_edhrec_cache(*, force_refresh: bool = False, scope: str = "all") -> dict:
    _ensure_tables()
    if not edhrec_service_enabled():
        message = "EDHREC service is not configured."
        _LOG.warning(message)
        return {"status": "error", "message": message}

    scope_key = (scope or "all").strip().lower()
    if scope_key == "all":
        try:
            targets = collect_edhrec_index_targets(include_themes=True)
        except Exception as exc:
            _LOG.warning("EDHREC index lookup failed: %s", exc)
            return {"status": "error", "message": "EDHREC index lookup failed."}
    else:
        try:
            targets = collect_edhrec_targets()
        except SQLAlchemyError:
            db.session.rollback()
            _LOG.error("EDHREC refresh failed due to database error.", exc_info=True)
            return {"status": "error", "message": "Database error while collecting EDHREC targets."}

    tag_inserted = _upsert_index_tags(targets.get("tags") or []) if scope_key == "all" else 0
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

        tag_entries = _extract_commander_tag_entries(payload)
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
    message = (
        f"EDHREC cache updated for {commander_ok}/{len(commander_targets)} commanders."
    )
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


def _normalize_tags(tags: Iterable[str] | None) -> list[str]:
    if not tags:
        return []
    return _dedupe(tags)


def get_commander_tags(commander_oracle_id: str) -> list[str]:
    _ensure_tables()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []
    rows = (
        EdhrecCommanderTag.query.filter_by(commander_oracle_id=commander_oracle_id)
        .order_by(EdhrecCommanderTag.tag.asc())
        .all()
    )
    return [row.tag for row in rows if row.tag]


def get_commander_type_distribution(
    commander_oracle_id: str,
    *,
    tag: str | None = None,
) -> list[tuple[str, int]]:
    _ensure_tables()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []
    tag_label = (tag or "").strip()
    if tag_label:
        query = EdhrecCommanderTypeDistribution.query.filter_by(
            commander_oracle_id=commander_oracle_id,
            tag=tag_label,
        )
    else:
        query = EdhrecCommanderTypeDistribution.query.filter_by(
            commander_oracle_id=commander_oracle_id,
            tag="",
        )
    rows = query.order_by(EdhrecCommanderTypeDistribution.card_type.asc()).all()
    return [(row.card_type, int(row.count or 0)) for row in rows if row.card_type]


def get_tag_commanders(tag: str) -> list[str]:
    _ensure_tables()
    label = (tag or "").strip()
    if not label:
        return []
    rows = (
        EdhrecTagCommander.query.filter_by(tag=label)
        .order_by(EdhrecTagCommander.commander_oracle_id.asc())
        .all()
    )
    return [row.commander_oracle_id for row in rows if row.commander_oracle_id]


def get_commander_category_groups(
    commander_oracle_id: str,
    *,
    tag: str | None = None,
    limit: int | None = _MAX_SYNERGY_CARDS,
) -> list[dict]:
    _ensure_tables()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []
    tag_label = (tag or "").strip()
    if tag_label:
        query = EdhrecCommanderTagCategoryCard.query.filter_by(
            commander_oracle_id=commander_oracle_id,
            tag=tag_label,
        )
        rows = (
            query.order_by(
                func.coalesce(EdhrecCommanderTagCategoryCard.category_rank, 9999).asc(),
                EdhrecCommanderTagCategoryCard.category.asc(),
                func.coalesce(EdhrecCommanderTagCategoryCard.synergy_rank, 999999).asc(),
                func.coalesce(EdhrecCommanderTagCategoryCard.synergy_score, 0).desc(),
            )
            .all()
        )
    else:
        query = EdhrecCommanderCategoryCard.query.filter_by(
            commander_oracle_id=commander_oracle_id
        )
        rows = (
            query.order_by(
                func.coalesce(EdhrecCommanderCategoryCard.category_rank, 9999).asc(),
                EdhrecCommanderCategoryCard.category.asc(),
                func.coalesce(EdhrecCommanderCategoryCard.synergy_rank, 999999).asc(),
                func.coalesce(EdhrecCommanderCategoryCard.synergy_score, 0).desc(),
            )
            .all()
        )

    grouped: dict[str, list[dict]] = {}
    ordered: list[str] = []
    for row in rows:
        category = (row.category or "").strip()
        oracle_id = (row.card_oracle_id or "").strip()
        if not category or not oracle_id:
            continue
        if category not in grouped:
            grouped[category] = []
            ordered.append(category)
        cards = grouped[category]
        if limit is not None and len(cards) >= limit:
            continue
        cards.append(
            {
                "oracle_id": oracle_id,
                "synergy_score": float(row.synergy_score) if row.synergy_score is not None else None,
                "synergy_percent": _synergy_percent(float(row.synergy_score)) if row.synergy_score is not None else None,
                "inclusion_percent": _inclusion_percent(row.inclusion_percent),
                "synergy_rank": int(row.synergy_rank or 0) if row.synergy_rank is not None else None,
            }
        )

    groups: list[dict] = []
    for category in ordered:
        cards = grouped.get(category) or []
        groups.append({"label": category, "cards": cards, "count": len(cards)})
    return groups


def get_commander_tag_synergy_groups(
    commander_oracle_id: str,
    tags: Iterable[str] | None = None,
    *,
    limit: int | None = _MAX_SYNERGY_CARDS,
) -> list[dict]:
    _ensure_tables()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []
    requested_tags = _normalize_tags(tags)
    query = EdhrecCommanderTagCard.query.filter_by(commander_oracle_id=commander_oracle_id)
    if requested_tags:
        query = query.filter(EdhrecCommanderTagCard.tag.in_(requested_tags))
    rows = (
        query.order_by(
            EdhrecCommanderTagCard.tag.asc(),
            func.coalesce(EdhrecCommanderTagCard.synergy_score, 0).desc(),
            func.coalesce(EdhrecCommanderTagCard.synergy_rank, 999999).asc(),
        )
        .all()
    )
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        tag = (row.tag or "").strip()
        oracle_id = (row.card_oracle_id or "").strip()
        if not tag or not oracle_id:
            continue
        grouped.setdefault(tag, []).append(
            {
                "oracle_id": oracle_id,
                "synergy_score": float(row.synergy_score or 0.0),
                "synergy_percent": _synergy_percent(float(row.synergy_score or 0.0)),
                "inclusion_percent": _inclusion_percent(row.inclusion_percent),
                "synergy_rank": int(row.synergy_rank or 0) if row.synergy_rank is not None else None,
            }
        )
    ordered_tags = requested_tags or sorted(grouped.keys())
    groups: list[dict] = []
    for tag in ordered_tags:
        cards = grouped.get(tag, [])
        if limit is not None:
            cards = cards[:limit]
        groups.append({"label": tag, "cards": cards, "count": len(cards)})
    return groups


def _oracle_name_for_id(oracle_id: str, cache: dict[str, str | None]) -> str | None:
    if not oracle_id:
        return None
    if oracle_id in cache:
        return cache[oracle_id]
    try:
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        cache[oracle_id] = None
        return None
    name = prints[0].get("name") if prints else None
    cache[oracle_id] = name
    return name


def _get_commander_synergy(
    commander_oracle_id: str,
    tags: Iterable[str] | None,
    *,
    prefer_tag_specific: bool = False,
    limit: int | None = _MAX_SYNERGY_CARDS,
) -> list[dict]:
    _ensure_tables()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []

    requested_tags = _normalize_tags(tags)
    commander_tags = set(get_commander_tags(commander_oracle_id))
    tag_matches = [tag for tag in requested_tags if tag in commander_tags]

    name_cache: dict[str, str | None] = {}
    results: list[dict] = []
    rows = []
    if prefer_tag_specific and tag_matches:
        try:
            tag_rows = (
                EdhrecCommanderTagCard.query.filter_by(commander_oracle_id=commander_oracle_id)
                .filter(EdhrecCommanderTagCard.tag.in_(tag_matches))
                .all()
            )
        except SQLAlchemyError as exc:
            db.session.rollback()
            _LOG.warning("EDHREC tag cache lookup failed: %s", exc)
            tag_rows = []
        if tag_rows:
            merged: dict[str, dict] = {}
            for row in tag_rows:
                oracle_id = (row.card_oracle_id or "").strip()
                if not oracle_id:
                    continue
                score = float(row.synergy_score or 0.0)
                rank = int(row.synergy_rank or 0) if row.synergy_rank is not None else None
                inclusion = _inclusion_percent(row.inclusion_percent)
                current = merged.get(oracle_id)
                if not current or score > current["synergy_score"]:
                    merged[oracle_id] = {
                        "oracle_id": oracle_id,
                        "synergy_score": score,
                        "synergy_rank": rank,
                        "inclusion_percent": inclusion,
                    }
                elif score == current["synergy_score"]:
                    if inclusion is not None and (current.get("inclusion_percent") or 0) < inclusion:
                        current["inclusion_percent"] = inclusion
                    if rank is not None and (current["synergy_rank"] is None or rank < current["synergy_rank"]):
                        current["synergy_rank"] = rank
            rows = list(merged.values())

    if not rows:
        try:
            base_query = (
                EdhrecCommanderCard.query.filter_by(commander_oracle_id=commander_oracle_id)
                .order_by(
                    func.coalesce(EdhrecCommanderCard.synergy_score, 0).desc(),
                    func.coalesce(EdhrecCommanderCard.synergy_rank, 999999).asc(),
                )
            )
            rows = base_query.limit(limit).all() if limit is not None else base_query.all()
        except SQLAlchemyError as exc:
            db.session.rollback()
            _LOG.warning("EDHREC cache lookup failed: %s", exc)
            return []

    normalized_rows: list[dict] = []
    for row in rows:
        if isinstance(row, dict):
            normalized_rows.append(row)
        else:
            normalized_rows.append(
                {
                    "card_oracle_id": row.card_oracle_id,
                    "synergy_score": row.synergy_score,
                    "synergy_rank": row.synergy_rank,
                    "inclusion_percent": row.inclusion_percent,
                }
            )

    for row in normalized_rows:
        oracle_id = (row.get("card_oracle_id") or row.get("oracle_id") or "").strip()
        if not oracle_id:
            continue
        name = _oracle_name_for_id(oracle_id, name_cache) or oracle_id
        synergy_score = row.get("synergy_score")
        synergy_rank = row.get("synergy_rank")
        inclusion_percent = row.get("inclusion_percent")
        score_value = float(synergy_score or 0.0)
        results.append(
            {
                "oracle_id": oracle_id,
                "name": name,
                "synergy_score": score_value,
                "synergy_percent": _synergy_percent(score_value),
                "inclusion_percent": _inclusion_percent(inclusion_percent),
                "synergy_rank": int(synergy_rank or 0) if synergy_rank is not None else None,
                "source": "edhrec",
                "tag_matches": list(tag_matches),
            }
        )
    results.sort(
        key=lambda item: (
            -(item.get("synergy_score") or 0.0),
            item.get("synergy_rank") if item.get("synergy_rank") is not None else 999999,
        )
    )
    if limit is not None:
        return results[:limit]
    return results


def get_commander_synergy(
    commander_oracle_id: str,
    tags: list[str] | None = None,
    *,
    prefer_tag_specific: bool = False,
    limit: int | None = _MAX_SYNERGY_CARDS,
) -> list[dict]:
    key = (
        "edhrec_local_synergy",
        commander_oracle_id,
        tuple(_normalize_tags(tags)),
        bool(prefer_tag_specific),
        limit,
    )
    return request_cached(
        key,
        lambda: _get_commander_synergy(
            commander_oracle_id,
            tags,
            prefer_tag_specific=prefer_tag_specific,
            limit=limit,
        ),
    )


def cache_ready() -> bool:
    snapshot = edhrec_cache_snapshot()
    if snapshot.get("status") != "ok":
        return False
    return bool(snapshot.get("commanders", {}).get("count"))


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
