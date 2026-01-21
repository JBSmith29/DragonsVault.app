"""Set overview, gallery, and detail routes."""

from __future__ import annotations

from core.domains.cards.services import scryfall_service
from core.routes.base import views


@views.route("/sets")
def sets_overview():
    return scryfall_service.sets_overview()


@views.route("/sets/<set_code>/gallery")
def set_gallery(set_code):
    return scryfall_service.set_gallery(set_code)


@views.route("/sets/<set_code>")
def set_detail(set_code):
    """Legacy route retained for compatibility; redirect to the gallery view."""
    return scryfall_service.set_detail(set_code)


__all__ = ["set_detail", "set_gallery", "sets_overview"]
