"""Common types + dispatch for deck importers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit


class ImportError(Exception):
    """Raised when a deck cannot be imported (bad URL, upstream failure, …)."""


# Public alias — avoids shadowing the builtin at call sites.
DeckImportError = ImportError


@dataclass
class ImportedDeck:
    """A normalized decklist pulled from a public deck site."""

    source: str
    source_id: Optional[str]
    url: Optional[str]
    name: str
    commanders: list[str] = field(default_factory=list)
    color_identity: Optional[str] = None  # subset of WUBRG, e.g. "WUB"
    format: Optional[str] = None
    bracket: Optional[int] = None
    bracket_estimated: bool = False  # True when the bracket is a site estimate
    cards: list[dict[str, Any]] = field(default_factory=list)  # {"name","quantity"}

    @property
    def commander_name(self) -> Optional[str]:
        return " // ".join(self.commanders) if self.commanders else None

    @property
    def card_count(self) -> int:
        total = sum(int(c.get("quantity") or 0) for c in self.cards)
        return total + len(self.commanders)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "url": self.url,
            "name": self.name,
            "commander_name": self.commander_name,
            "commanders": self.commanders,
            "color_identity": self.color_identity,
            "colors": list(self.color_identity or ""),
            "format": self.format,
            "bracket": self.bracket,
            "bracket_estimated": self.bracket_estimated,
            "card_count": self.card_count,
            "cards": self.cards,
        }


# Host fragment -> source key. Checked as a substring of the URL hostname.
_HOST_SOURCE = {
    "archidekt.com": "archidekt",
    "moxfield.com": "moxfield",
    "mtggoldfish.com": "mtggoldfish",
}


def detect_source(url: str | None) -> Optional[str]:
    """Return the source key for a deck URL, or None if unrecognised."""
    host = (urlsplit((url or "").strip()).hostname or "").lower()
    if not host:
        return None
    for fragment, source in _HOST_SOURCE.items():
        if host == fragment or host.endswith("." + fragment):
            return source
    return None


def _adapter(source: str):
    if source == "archidekt":
        from . import archidekt
        return archidekt
    if source == "moxfield":
        from . import moxfield
        return moxfield
    if source == "mtggoldfish":
        from . import mtggoldfish
        return mtggoldfish
    raise ImportError(f"Unsupported deck source: {source}")


def import_from_url(url: str) -> ImportedDeck:
    """Detect the site from the URL and import the deck."""
    source = detect_source(url)
    if not source:
        raise ImportError(
            "Unrecognised deck link. Use an Archidekt, Moxfield, or MTGGoldfish URL."
        )
    return _adapter(source).fetch_deck(url)


def fetch_deck(source: str, deck_ref: str) -> ImportedDeck:
    """Import a deck by explicit source + a URL or bare site deck id."""
    return _adapter(source).fetch_deck(deck_ref)


def supports_username_listing(source: str) -> bool:
    return source in ("archidekt", "moxfield")


def list_user_decks(source: str, username: str, *, limit: int = 60) -> list[dict[str, Any]]:
    """List a user's public decks (Archidekt/Moxfield only)."""
    adapter = _adapter(source)
    lister = getattr(adapter, "list_user_decks", None)
    if not callable(lister):
        raise ImportError(f"{source} does not support listing decks by username.")
    return lister(username, limit=limit)


__all__ = [
    "ImportedDeck",
    "ImportError",
    "DeckImportError",
    "detect_source",
    "fetch_deck",
    "import_from_url",
    "list_user_decks",
    "supports_username_listing",
]
