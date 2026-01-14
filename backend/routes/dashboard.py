"""Dashboard landing page and lightweight API endpoints."""

from __future__ import annotations

from services import card_service, scryfall_service
from .base import views


@views.route("/dashboard/index")
def index():
    """Legacy route that forwards to the dashboard summary."""
    return card_service.dashboard_index()


@views.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    """Render the high-level collection overview tiles and deck summaries."""
    return card_service.dashboard()


@views.route("/api/card/<int:card_id>")
def api_card(card_id):
    """Lightweight JSON used by hover/quick-view."""
    return card_service.api_card(card_id)


@views.route("/api/print/<sid>/faces", methods=["GET"])
def api_print_faces(sid):
    """Provide client-side render helpers with the available image faces for a print."""
    return scryfall_service.api_print_faces(sid)


__all__ = ["api_card", "api_print_faces", "dashboard", "index"]
