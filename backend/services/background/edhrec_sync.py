"""Background helpers for EDHREC refresh tasks."""

from __future__ import annotations

import logging
from typing import List

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Folder
from services import scryfall_cache as sc
from services.commander_utils import primary_commander_name, primary_commander_oracle_id
from services.edhrec_client import edhrec_service_enabled, refresh_edhrec_cache
from services.scryfall_cache import ensure_cache_loaded

_LOG = logging.getLogger(__name__)


def _dedupe(items: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def collect_edhrec_targets() -> dict:
    folders = Folder.query.order_by(func.lower(Folder.name)).all()
    deck_folders = [folder for folder in folders if not folder.is_collection]
    commander_names: List[str] = []
    tag_names: List[str] = []
    with_commander = 0
    with_tag = 0
    cache_ready = False
    for folder in deck_folders:
        tag = (folder.deck_tag or "").strip()
        if tag:
            with_tag += 1
            tag_names.append(tag)
        name = primary_commander_name(folder.commander_name)
        if not name and folder.commander_oracle_id:
            oid = primary_commander_oracle_id(folder.commander_oracle_id)
            if oid:
                try:
                    cache_ready = cache_ready or ensure_cache_loaded()
                    if cache_ready:
                        prints = sc.prints_for_oracle(oid) or []
                        if prints:
                            name = prints[0].get("name")
                except Exception:
                    name = None
        if name:
            with_commander += 1
            commander_names.append(name)
    return {
        "deck_total": len(deck_folders),
        "with_commander": with_commander,
        "with_tag": with_tag,
        "commander_names": _dedupe(commander_names),
        "tag_names": _dedupe(tag_names),
    }


def refresh_edhrec_synergy_cache(*, force_refresh: bool) -> dict:
    _LOG.info("EDHREC refresh started (force=%s).", force_refresh)
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
    commander_names = targets.get("commander_names", [])
    tag_names = targets.get("tag_names", [])
    if not commander_names and not tag_names:
        message = "No commander names or deck tags found to refresh."
        _LOG.warning(message)
        return {
            "status": "info",
            "message": message,
            "targets": targets,
        }

    try:
        result = refresh_edhrec_cache(
            commanders=commander_names,
            themes=tag_names,
            force_refresh=force_refresh,
        )
    except Exception as exc:
        _LOG.exception("EDHREC refresh failed")
        return {
            "status": "error",
            "message": f"EDHREC refresh failed: {exc}",
            "targets": targets,
        }

    if result.get("status") == "error":
        return {
            "status": "error",
            "message": result.get("error") or "EDHREC refresh failed.",
            "targets": targets,
        }

    commander_summary = result.get("commanders") or {}
    theme_summary = result.get("themes") or {}
    message = (
        f"EDHREC cache: {commander_summary.get('ok', 0)}/{commander_summary.get('requested', 0)} "
        f"commanders, {theme_summary.get('ok', 0)}/{theme_summary.get('requested', 0)} deck tags."
    )
    if result.get("errors"):
        _LOG.warning("EDHREC refresh completed with errors: %s", len(result.get("errors") or []))
    _LOG.info("EDHREC refresh completed: %s", message)
    return {
        "status": result.get("status", "success"),
        "message": message,
        "errors": result.get("errors") or [],
        "targets": targets,
        "commanders": commander_summary,
        "themes": theme_summary,
    }
