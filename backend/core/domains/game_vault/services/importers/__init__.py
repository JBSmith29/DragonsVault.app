"""Deck importers for Game Vault.

A tiny, uniform interface over three public deck sites. Each adapter turns a
deck URL (or a site deck id) into an :class:`ImportedDeck`. Archidekt and
Moxfield can additionally list a user's public decks by username.
"""

from .base import (
    ImportedDeck,
    ImportError as DeckImportError,
    detect_source,
    fetch_deck,
    import_from_url,
    list_user_decks,
    supports_username_listing,
)

__all__ = [
    "ImportedDeck",
    "DeckImportError",
    "detect_source",
    "fetch_deck",
    "import_from_url",
    "list_user_decks",
    "supports_username_listing",
]
