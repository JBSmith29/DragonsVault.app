"""Compatibility wrapper for Scryfall browser, set, and print services."""

from __future__ import annotations

from core.domains.cards.services.scryfall_browser_service import scryfall_browser
from core.domains.cards.services.scryfall_print_service import (
    api_print_faces,
    api_scryfall_print,
    scryfall_print_detail,
    scryfall_resolve_by_name,
)
from core.domains.cards.services.scryfall_sets_service import (
    set_detail,
    set_gallery,
    sets_overview,
)

__all__ = [
    "api_print_faces",
    "api_scryfall_print",
    "set_detail",
    "set_gallery",
    "sets_overview",
    "scryfall_browser",
    "scryfall_print_detail",
    "scryfall_resolve_by_name",
]
