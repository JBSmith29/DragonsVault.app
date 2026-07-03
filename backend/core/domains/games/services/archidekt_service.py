"""Read-only Archidekt integration for Commander pod logging.

Pulls a user's Commander decks and a deck's details (name, commander(s),
bracket, and card list) from Archidekt's public API so a pod player can pick a
deck without it existing as a local Folder. Metadata is snapshotted into the
existing game deck model, so saved games are unaffected.

Archidekt API (reverse-engineered, public/no-auth):
  - list:   GET /api/decks/v3/?owner=<username>   -> {count, next, results:[...]}
  - detail: GET /api/decks/<id>/                   -> {name, edhBracket, cards:[...]}
The commander is the card whose ``categories`` include "Commander"; the bracket
is ``edhBracket`` (1-5 or null); Commander decks have ``deckFormat`` == 3.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import quote

from shared.http_client import EXTERNAL_SERVICE_TIMEOUT, safe_get

API_BASE = "https://archidekt.com/api"
COMMANDER_FORMAT = 3  # Archidekt deckFormat code for Commander / EDH
_MAX_LIST_PAGES = 6
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_HEADERS = {
    "User-Agent": "DragonsVault/1.0 (+https://github.com/JBSmith29/DragonsVault.app)",
    "Accept": "application/json",
}
# Archidekt categories whose cards are not part of the 100-card deck.
_EXCLUDED_CATEGORIES = {"maybeboard", "sideboard", "considering"}


class ArchidektError(Exception):
    """Raised for a bad username, missing deck, or an upstream failure."""


def normalize_username(raw: str | None) -> str:
    """Accept a bare handle, an ``@handle``, or an Archidekt profile URL."""
    text = (raw or "").strip()
    match = re.search(r"archidekt\.com/(?:u|user)/([^/?#]+)", text, re.IGNORECASE)
    if match:
        text = match.group(1)
    return text.lstrip("@").strip()


def _get_json(url: str) -> dict[str, Any]:
    try:
        resp = safe_get(url, timeout=EXTERNAL_SERVICE_TIMEOUT, headers=_HEADERS)
    except Exception as exc:  # network / timeout
        raise ArchidektError("Couldn't reach Archidekt. Try again in a moment.") from exc
    if resp.status_code == 404:
        raise ArchidektError("Not found on Archidekt.")
    if resp.status_code != 200:
        raise ArchidektError(f"Archidekt returned HTTP {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise ArchidektError("Archidekt returned an unexpected response.") from exc


def _deck_url(deck_id: Any) -> str:
    return f"https://archidekt.com/decks/{deck_id}"


def list_commander_decks(username: str | None, *, limit: int = 60) -> list[dict[str, Any]]:
    """Return the user's public Commander decks (newest first)."""
    handle = normalize_username(username)
    if not handle or not _USERNAME_RE.match(handle):
        raise ArchidektError("Enter a valid Archidekt username.")

    decks: list[dict[str, Any]] = []
    url: Optional[str] = f"{API_BASE}/decks/v3/?owner={quote(handle)}&orderBy=-updatedAt"
    pages = 0
    while url and pages < _MAX_LIST_PAGES and len(decks) < limit:
        pages += 1
        data = _get_json(url)
        for item in data.get("results") or []:
            if item.get("deckFormat") != COMMANDER_FORMAT:
                continue
            decks.append(
                {
                    "id": item.get("id"),
                    "name": (item.get("name") or "Untitled deck").strip(),
                    "bracket": item.get("edhBracket"),
                    "size": item.get("size"),
                    "colors": item.get("colors") or {},
                    "updated_at": item.get("updatedAt"),
                    "url": _deck_url(item.get("id")),
                }
            )
            if len(decks) >= limit:
                break
        # Archidekt returns absolute "next" URLs (sometimes http://); keep https.
        nxt = data.get("next")
        url = nxt.replace("http://", "https://", 1) if isinstance(nxt, str) and nxt else None
    return decks


def _card_name(entry: dict[str, Any]) -> str:
    card = entry.get("card") or {}
    oracle = card.get("oracleCard") or {}
    return (oracle.get("name") or card.get("displayName") or "").strip()


def fetch_deck(deck_id: Any) -> dict[str, Any]:
    """Return parsed deck details: name, commander(s), bracket, and card list."""
    if deck_id in (None, "") or not re.fullmatch(r"\d+", str(deck_id)):
        raise ArchidektError("Invalid Archidekt deck id.")
    data = _get_json(f"{API_BASE}/decks/{deck_id}/")

    # Categories flagged not-included (maybeboard/sideboard) drop out of the deck.
    excluded = {
        (cat.get("name") or "").strip().lower()
        for cat in (data.get("categories") or [])
        if cat.get("includedInDeck") is False
    }
    excluded |= _EXCLUDED_CATEGORIES

    commanders: list[str] = []
    cards: list[dict[str, Any]] = []
    for entry in data.get("cards") or []:
        categories = [str(c) for c in (entry.get("categories") or [])]
        lowered = {c.strip().lower() for c in categories}
        name = _card_name(entry)
        if not name:
            continue
        if "commander" in lowered:
            if name not in commanders:
                commanders.append(name)
            continue
        if lowered and lowered.issubset(excluded):
            continue  # maybeboard / sideboard
        cards.append({"name": name, "quantity": int(entry.get("quantity") or 1)})

    return {
        "id": data.get("id"),
        "name": (data.get("name") or "Untitled deck").strip(),
        "commanders": commanders,
        "commander_name": " // ".join(commanders) if commanders else None,
        "bracket": data.get("edhBracket"),
        "card_count": sum(card["quantity"] for card in cards) + len(commanders),
        "cards": cards,
        "url": _deck_url(data.get("id")),
        "format": data.get("deckFormat"),
        "is_commander": data.get("deckFormat") == COMMANDER_FORMAT,
    }


__all__ = [
    "ArchidektError",
    "COMMANDER_FORMAT",
    "fetch_deck",
    "list_commander_decks",
    "normalize_username",
]
