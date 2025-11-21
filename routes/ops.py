"""Operational endpoints for health/readiness/metrics checks."""

from __future__ import annotations

from flask import jsonify, current_app
from sqlalchemy import func, text

from extensions import db
from models import Card, Folder, WishlistItem
from .base import views


@views.route("/healthz", methods=["GET"])
def healthz():
    """Lightweight health probe that requires no external dependencies."""
    return jsonify(status="ok", service="DragonsVault"), 200


@views.route("/readyz", methods=["GET"])
def readyz():
    """Readiness probe that verifies database connectivity."""
    try:
        db.session.execute(text("SELECT 1"))
        db.session.commit()
    except Exception as exc:
        current_app.logger.warning("Readiness check failed: %s", exc, exc_info=True)
        db.session.rollback()
        return jsonify(status="error", reason="database"), 503
    return jsonify(status="ok"), 200


@views.route("/metrics", methods=["GET"])
def metrics():
    """Expose simple application metrics for dashboards/alerts."""
    total_cards = db.session.query(func.count(Card.id)).scalar() or 0
    total_folders = db.session.query(func.count(Folder.id)).scalar() or 0
    total_wishlist = db.session.query(func.count(WishlistItem.id)).scalar() or 0
    proxy_decks = (
        db.session.query(func.count(Folder.id))
        .filter(Folder.is_proxy.is_(True))
        .scalar()
        or 0
    )
    return jsonify(
        {
            "cards_total": total_cards,
            "folders_total": total_folders,
            "wishlist_total": total_wishlist,
            "proxy_folders": proxy_decks,
        }
    )
