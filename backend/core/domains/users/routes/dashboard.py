"""Dashboard landing page and lightweight API endpoints."""

from __future__ import annotations

from core.domains.cards.services import card_detail_service, scryfall_service
from core.domains.users.services import dashboard_service
from core.routes.api import api_bp
from core.routes.base import views


@views.route("/dashboard/index")
def index():
    """Legacy route that forwards to the dashboard summary."""
    return dashboard_service.dashboard_index()


@views.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    """Render the high-level collection overview tiles and deck summaries."""
    return dashboard_service.dashboard()


@api_bp.route("/card/<int:card_id>")
def api_card(card_id):
    """Lightweight JSON used by hover/quick-view."""
    return card_detail_service.api_card(card_id)


@api_bp.route("/print/<sid>/faces", methods=["GET"])
def api_print_faces(sid):
    """Provide client-side render helpers with the available image faces for a print."""
    return scryfall_service.api_print_faces(sid)


__all__ = ["api_card", "api_print_faces", "dashboard", "index"]
