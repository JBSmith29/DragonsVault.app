"""Background helpers for EDHREC refresh tasks."""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from services.edhrec_cache_service import refresh_edhrec_cache

_LOG = logging.getLogger(__name__)


def refresh_edhrec_synergy_cache(*, force_refresh: bool, scope: str = "all") -> dict:
    _LOG.info("EDHREC refresh started (force=%s, scope=%s).", force_refresh, scope)
    try:
        result = refresh_edhrec_cache(force_refresh=force_refresh, scope=scope)
    except SQLAlchemyError:
        db.session.rollback()
        _LOG.error("EDHREC refresh failed due to database error.", exc_info=True)
        return {"status": "error", "message": "Database error while refreshing EDHREC cache."}
    except Exception as exc:
        _LOG.exception("EDHREC refresh failed")
        return {
            "status": "error",
            "message": f"EDHREC refresh failed: {exc}",
        }

    if result.get("status") == "error":
        return {
            "status": "error",
            "message": result.get("message") or "EDHREC refresh failed.",
            "targets": result.get("targets"),
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
        "targets": result.get("targets") or {},
        "commanders": commander_summary,
        "themes": result.get("tags") or {},
    }
