"""Scryfall search and print detail routes."""

from __future__ import annotations

from services import scryfall_service
from .base import views


@views.route("/scryfall")
def scryfall_browser():
    """
    Scryfall browser backed by /cards/search with:
      q, set, type (multi), typal, color (multi), color_mode, unique, commander, foil (y/n)
    """
    return scryfall_service.scryfall_browser()


@views.route("/api/scryfall/print/<sid>")
def api_scryfall_print(sid):
    return scryfall_service.api_scryfall_print(sid)


@views.route("/scryfall/print/<sid>")
def scryfall_print_detail(sid):
    """Details for a specific Scryfall print id; reuses card_detail template."""
    return scryfall_service.scryfall_print_detail(sid)


@views.route("/scryfall/resolve-by-name")
def scryfall_resolve_by_name():
    """
    Resolve a card by exact name via Scryfall Named API, then
    redirect to the in-app scryfall_print_detail(sid=...).
    """
    return scryfall_service.scryfall_resolve_by_name()


__all__ = ["scryfall_browser", "api_scryfall_print", "scryfall_print_detail", "scryfall_resolve_by_name"]
