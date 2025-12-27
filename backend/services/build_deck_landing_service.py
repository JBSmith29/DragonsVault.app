"""Compatibility wrapper for Build-A-Deck landing services."""

from services.build_deck.build_landing_service import (
    get_build_landing_data,
    get_commander_fits_by_tag,
    get_commander_fits_from_collection,
)

__all__ = [
    "get_build_landing_data",
    "get_commander_fits_from_collection",
    "get_commander_fits_by_tag",
]
