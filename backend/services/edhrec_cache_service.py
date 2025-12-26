"""Local EDHREC cache management for deck recommendations."""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import (
    EdhrecCommanderCard,
    EdhrecCommanderTag,
    EdhrecTagCommander,
    Folder,
)
from services import scryfall_cache as sc
from services.commander_utils import primary_commander_name, primary_commander_oracle_id
from services.deck_tags import resolve_deck_tag_from_slug
from services.edhrec_client import commander_cardviews, edhrec_service_enabled, ensure_commander_data
from services.request_cache import request_cached

_LOG = logging.getLogger(__name__)

_MAX_SYNERGY_CARDS = 160


def _ensure_tables() -> None:
    try:
        db.metadata.create_all(
            db.engine,
            tables=[
                EdhrecCommanderCard.__table__,
                EdhrecCommanderTag.__table__,
                EdhrecTagCommander.__table__,
            ],
        )
    except Exception as exc:
        _LOG.error("Failed to ensure EDHREC cache tables: %s", exc)


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


def _extract_commander_tags(payload: dict) -> list[str]:
    raw_options = payload.get("theme_options") or []
    if not isinstance(raw_options, list):
        return []
    tags: list[str] = []
    for entry in raw_options:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug") or ""
        label = entry.get("label") or ""
        candidate = resolve_deck_tag_from_slug(str(slug))
        if not candidate:
            candidate = resolve_deck_tag_from_slug(str(label))
        if candidate:
            tags.append(candidate)
    return _dedupe(tags)


def refresh_edhrec_cache(*, force_refresh: bool = False) -> dict:
    _ensure_tables()
    if not edhrec_service_enabled():
        message = "EDHREC service is not configured."
        _LOG.warning(message)
        return {"status": "error", "message": message}

    try:
        targets = collect_edhrec_targets()
    except SQLAlchemyError:
        db.session.rollback()
        _LOG.error("EDHREC refresh failed due to database error.", exc_info=True)
        return {"status": "error", "message": "Database error while collecting EDHREC targets."}

    commander_targets = targets.get("commanders") or []
    if not commander_targets:
        message = "No commander data found to refresh."
        _LOG.warning(message)
        return {"status": "info", "message": message, "targets": targets}

    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for EDHREC refresh: %s", exc)
        return {"status": "error", "message": "Scryfall cache unavailable; refresh card cache first."}

    errors: list[str] = []
    commander_ok = 0
    total_cards = 0
    total_tags = 0
    oracle_cache: dict[str, str | None] = {}

    for target in commander_targets:
        commander_name = (target.get("name") or "").strip()
        commander_oracle_id = (target.get("oracle_id") or "").strip()
        if not commander_name or not commander_oracle_id:
            continue

        slug, payload, warning = ensure_commander_data(
            commander_name,
            force_refresh=force_refresh,
        )
        if warning:
            errors.append(warning)
        if not payload:
            errors.append(f"EDHREC data missing for {commander_name}.")
            continue

        views = commander_cardviews(payload)
        card_map: dict[str, float] = {}
        for view in views:
            oracle_id = _oracle_id_for_name(view.name, oracle_cache)
            if not oracle_id:
                continue
            score = float(view.synergy or 0.0)
            existing = card_map.get(oracle_id)
            if existing is None or score > existing:
                card_map[oracle_id] = score

        top_cards = sorted(
            card_map.items(), key=lambda item: -(item[1] or 0.0)
        )[:_MAX_SYNERGY_CARDS]

        commander_tags = _extract_commander_tags(payload)

        try:
            EdhrecCommanderCard.query.filter_by(
                commander_oracle_id=commander_oracle_id
            ).delete(synchronize_session=False)
            for oracle_id, score in top_cards:
                db.session.add(
                    EdhrecCommanderCard(
                        commander_oracle_id=commander_oracle_id,
                        card_oracle_id=oracle_id,
                        synergy_score=score,
                    )
                )

            EdhrecCommanderTag.query.filter_by(
                commander_oracle_id=commander_oracle_id
            ).delete(synchronize_session=False)
            for tag in commander_tags:
                db.session.add(
                    EdhrecCommanderTag(
                        commander_oracle_id=commander_oracle_id,
                        tag=tag,
                    )
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
        tag_count = db.session.query(
            func.count(func.distinct(EdhrecCommanderTag.tag))
        ).scalar()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.warning("Failed to read EDHREC cache snapshot: %s", exc)
        return {"status": "error", "error": "Unable to read EDHREC cache."}

    return {
        "status": "ok",
        "commanders": {"count": int(commander_count or 0), "cards": int(card_rows or 0)},
        "tags": {"count": int(tag_count or 0)},
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


def _get_commander_synergy(commander_oracle_id: str, tags: Iterable[str] | None) -> list[dict]:
    _ensure_tables()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []

    try:
        rows = (
            EdhrecCommanderCard.query.filter_by(commander_oracle_id=commander_oracle_id)
            .order_by(EdhrecCommanderCard.synergy_score.desc())
            .limit(_MAX_SYNERGY_CARDS)
            .all()
        )
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.warning("EDHREC cache lookup failed: %s", exc)
        return []

    commander_tags = set(get_commander_tags(commander_oracle_id))
    requested_tags = _normalize_tags(tags)
    tag_matches = [tag for tag in requested_tags if tag in commander_tags]

    name_cache: dict[str, str | None] = {}
    results: list[dict] = []
    for row in rows:
        oracle_id = (row.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        name = _oracle_name_for_id(oracle_id, name_cache) or oracle_id
        results.append(
            {
                "oracle_id": oracle_id,
                "name": name,
                "synergy_score": float(row.synergy_score or 0.0),
                "source": "edhrec",
                "tag_matches": list(tag_matches),
            }
        )
    return results


def get_commander_synergy(commander_oracle_id: str, tags: list[str] | None = None) -> list[dict]:
    key = ("edhrec_local_synergy", commander_oracle_id, tuple(_normalize_tags(tags)))
    return request_cached(key, lambda: _get_commander_synergy(commander_oracle_id, tags))


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
    "get_tag_commanders",
    "cache_ready",
]
