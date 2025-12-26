"""Background helpers for EDHREC refresh tasks."""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from services.edhrec_cache_service import collect_edhrec_targets, refresh_edhrec_cache

_LOG = logging.getLogger(__name__)


def refresh_edhrec_synergy_cache(*, force_refresh: bool) -> dict:
    _LOG.info("EDHREC refresh started (force=%s).", force_refresh)
    try:
        targets = collect_edhrec_targets()
    except SQLAlchemyError:
        db.session.rollback()
        _LOG.error("EDHREC refresh failed due to database error.", exc_info=True)
        return {"status": "error", "message": "Database error while collecting EDHREC targets."}
    try:
        result = refresh_edhrec_cache(force_refresh=force_refresh)
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
            "message": result.get("message") or "EDHREC refresh failed.",
            "targets": targets,
        }

    commander_summary = result.get("commanders") or {}
    message = result.get("message") or "EDHREC cache updated."
    if result.get("errors"):
        _LOG.warning("EDHREC refresh completed with errors: %s", len(result.get("errors") or []))
    _LOG.info("EDHREC refresh completed: %s", message)
    return {
        "status": result.get("status", "success"),
        "message": message,
        "errors": result.get("errors") or [],
        "targets": targets,
        "commanders": commander_summary,
        "themes": result.get("tags") or {},
    }
