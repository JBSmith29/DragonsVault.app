"""Background helpers for EDHREC refresh tasks."""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from core.domains.decks.services.edhrec.edhrec_ingestion_service import run_monthly_edhrec_ingestion

_LOG = logging.getLogger(__name__)


def refresh_edhrec_synergy_cache(*, force_refresh: bool, scope: str = "all") -> dict:
    _LOG.info("EDHREC ingestion started (force=%s, scope=%s).", force_refresh, scope)
    scope_key = (scope or "all").strip().lower()
    full_refresh = bool(force_refresh) or scope_key in {"all", "full"}
    try:
        summary = run_monthly_edhrec_ingestion(full_refresh=full_refresh, scope=scope_key)
    except SQLAlchemyError:
        db.session.rollback()
        _LOG.error("EDHREC ingestion failed due to database error.", exc_info=True)
        return {"status": "error", "message": "Database error while refreshing EDHREC cache."}
    except Exception as exc:
        _LOG.exception("EDHREC ingestion failed")
        return {
            "status": "error",
            "message": f"EDHREC refresh failed: {exc}",
        }

    errors = summary.get("errors") or 0
    commanders = summary.get("commanders_processed") or 0
    cards = summary.get("cards_inserted") or 0
    tags = summary.get("tags_inserted") or 0
    tag_cards = summary.get("tag_cards_inserted") or 0
    status = "success" if commanders else "warning"
    detail_parts = [f"{cards} cards", f"{tags} tags"]
    if tag_cards:
        detail_parts.append(f"{tag_cards} tag-specific cards")
    message = f"EDHREC cache updated for {commanders} commanders ({', '.join(detail_parts)})."
    if errors:
        status = "warning" if commanders else "error"
        _LOG.warning("EDHREC ingestion completed with %s error(s).", errors)
    _LOG.info("EDHREC ingestion completed: %s", message)
    return {
        "status": status,
        "message": message,
        "errors": [f"{errors} error(s) encountered."] if errors else [],
        "commanders": {"processed": commanders, "cards": cards},
        "themes": {"count": tags},
    }
