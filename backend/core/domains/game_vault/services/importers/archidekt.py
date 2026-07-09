"""Archidekt importer (public, no-auth API).

    list:   GET /api/decks/v3/?owner=<username>  -> {results:[...], next}
    detail: GET /api/decks/<id>/                 -> {name, edhBracket, cards:[...]}

The commander is the card whose ``categories`` include "Commander"; Commander
decks have ``deckFormat`` == 3; the bracket is ``edhBracket`` (1-5 or null).
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from shared.http_client import EXTERNAL_SERVICE_TIMEOUT, safe_get

from .base import ImportedDeck, ImportError

API_BASE = "https://archidekt.com/api"
COMMANDER_FORMAT = 3
_MAX_LIST_PAGES = 6
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_HEADERS = {
    "User-Agent": "DragonsVault-GameVault/1.0 (+https://github.com/JBSmith29/DragonsVault.app)",
    "Accept": "application/json",
}
_EXCLUDED_CATEGORIES = {"maybeboard", "sideboard", "considering"}
_COLOR_ORDER = "WUBRG"


def normalize_username(raw: str | None) -> str:
    text = (raw or "").strip()
    match = re.search(r"archidekt\.com/(?:u|user)/([^/?#]+)", text, re.IGNORECASE)
    if match:
        text = match.group(1)
    return text.lstrip("@").strip()


def _get_json(url: str) -> dict[str, Any]:
    try:
        resp = safe_get(url, timeout=EXTERNAL_SERVICE_TIMEOUT, headers=_HEADERS)
    except Exception as exc:
        raise ImportError("Couldn't reach Archidekt. Try again in a moment.") from exc
    if resp.status_code == 404:
        raise ImportError("Not found on Archidekt.")
    if resp.status_code != 200:
        raise ImportError(f"Archidekt returned HTTP {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise ImportError("Archidekt returned an unexpected response.") from exc


def _deck_url(deck_id: Any) -> str:
    return f"https://archidekt.com/decks/{deck_id}"


def _extract_id(deck_ref: str) -> Optional[str]:
    raw = (deck_ref or "").strip()
    if raw.isdigit():
        return raw
    parts = urlsplit(raw)
    segments = [seg for seg in (parts.path or "").split("/") if seg]
    if "decks" in segments:
        idx = segments.index("decks")
        if idx + 1 < len(segments) and segments[idx + 1].isdigit():
            return segments[idx + 1]
    match = re.search(r"/decks/(\d+)", raw)
    return match.group(1) if match else None


def _colors_to_identity(colors: Any) -> Optional[str]:
    """Archidekt list `colors` is like {'W':3,'U':1,...}; keep WUBRG order."""
    if not isinstance(colors, dict):
        return None
    present = {k[:1].upper() for k, v in colors.items() if v}
    ident = "".join(c for c in _COLOR_ORDER if c in present)
    return ident or None


def list_user_decks(username: str | None, *, limit: int = 60) -> list[dict[str, Any]]:
    handle = normalize_username(username)
    if not handle or not _USERNAME_RE.match(handle):
        raise ImportError("Enter a valid Archidekt username.")

    decks: list[dict[str, Any]] = []
    # NOTE: Archidekt's `owner` param expects a numeric user id and silently
    # returns EVERY deck when given a username. `ownerUsername` is the correct
    # filter for a handle.
    handle_key = handle.casefold()
    url: Optional[str] = (
        f"{API_BASE}/decks/v3/?ownerUsername={quote(handle)}&orderBy=-updatedAt&pageSize=50"
    )
    pages = 0
    while url and pages < _MAX_LIST_PAGES and len(decks) < limit:
        pages += 1
        data = _get_json(url)
        for item in data.get("results") or []:
            if item.get("deckFormat") != COMMANDER_FORMAT:
                continue
            # Belt-and-suspenders: never surface a deck owned by someone else.
            owner_name = ((item.get("owner") or {}).get("username") or "").casefold()
            if owner_name and owner_name != handle_key:
                continue
            decks.append(
                {
                    "source": "archidekt",
                    "source_id": str(item.get("id")),
                    "name": (item.get("name") or "Untitled deck").strip(),
                    "bracket": item.get("edhBracket"),
                    "color_identity": _colors_to_identity(item.get("colors")),
                    "card_count": item.get("size"),
                    "updated_at": item.get("updatedAt"),
                    "url": _deck_url(item.get("id")),
                }
            )
            if len(decks) >= limit:
                break
        nxt = data.get("next")
        url = nxt.replace("http://", "https://", 1) if isinstance(nxt, str) and nxt else None
    if not decks:
        raise ImportError(f"No public Commander decks found for '{handle}'.")
    return decks


def _card_name(entry: dict[str, Any]) -> str:
    card = entry.get("card") or {}
    oracle = card.get("oracleCard") or {}
    return (oracle.get("name") or card.get("displayName") or "").strip()


def fetch_deck(deck_ref: str) -> ImportedDeck:
    deck_id = _extract_id(deck_ref)
    if not deck_id:
        raise ImportError("Couldn't find an Archidekt deck id in that link.")
    data = _get_json(f"{API_BASE}/decks/{deck_id}/")

    excluded = {
        (cat.get("name") or "").strip().lower()
        for cat in (data.get("categories") or [])
        if cat.get("includedInDeck") is False
    }
    excluded |= _EXCLUDED_CATEGORIES

    commanders: list[str] = []
    cards: list[dict[str, Any]] = []
    for entry in data.get("cards") or []:
        lowered = {str(c).strip().lower() for c in (entry.get("categories") or [])}
        name = _card_name(entry)
        if not name:
            continue
        qty = int(entry.get("quantity") or 1)
        if "commander" in lowered:
            if name not in commanders:
                commanders.append(name)
            continue
        if lowered and lowered.issubset(excluded):
            continue
        cards.append({"name": name, "quantity": qty})

    return ImportedDeck(
        source="archidekt",
        source_id=str(data.get("id") or deck_id),
        url=_deck_url(data.get("id") or deck_id),
        name=(data.get("name") or "Untitled deck").strip(),
        commanders=commanders,
        color_identity=None,  # enriched from the commander via Scryfall
        format="commander" if data.get("deckFormat") == COMMANDER_FORMAT else None,
        bracket=data.get("edhBracket"),
        cards=cards,
    )


__all__ = ["fetch_deck", "list_user_decks", "normalize_username"]
