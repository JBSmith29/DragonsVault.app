"""Moxfield importer (public API).

    detail: GET https://api2.moxfield.com/v3/decks/all/<publicId>
    list:   GET https://api2.moxfield.com/v2/users/<username>/decks

Moxfield restructured decks into ``boards`` (v3); we also tolerate the older
flat ``mainboard``/``commanders`` shape. Only public decks are reachable.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from shared.http_client import EXTERNAL_SERVICE_TIMEOUT, safe_get

from .base import ImportedDeck, ImportError

_API = "https://api2.moxfield.com"
_HEADERS = {
    # Moxfield rejects unidentified clients; identify ourselves clearly.
    "User-Agent": "DragonsVault-GameVault/1.0 (+https://github.com/JBSmith29/DragonsVault.app)",
    "Accept": "application/json",
}
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,40}$")
_COLOR_ORDER = "WUBRG"
# Boards that are NOT part of the 100-card Commander deck.
_EXCLUDED_BOARDS = {"sideboard", "maybeboard", "tokens", "attractions", "stickers"}


# Moxfield fronts its API with Cloudflare bot protection, which serves a 403
# (occasionally 503) HTML challenge to any server-side caller. This is a
# deliberate block by Moxfield, not something we can work around without their
# official API agreement — so say so plainly.
_CLOUDFLARE_MSG = (
    "Moxfield blocks automated access (Cloudflare), so username search and link "
    "import aren't available for Moxfield. Use Archidekt or MTGGoldfish, or add "
    "the deck manually."
)


def _looks_like_cloudflare(resp) -> bool:
    headers = getattr(resp, "headers", None) or {}
    ctype = (headers.get("Content-Type") or "").lower()
    if "text/html" in ctype:
        return True
    body = (getattr(resp, "text", "") or "")[:2000].lower()
    return "cloudflare" in body or "attention required" in body or "cf-ray" in body


def _get_json(url: str) -> Any:
    try:
        resp = safe_get(url, timeout=EXTERNAL_SERVICE_TIMEOUT, headers=_HEADERS)
    except Exception as exc:
        raise ImportError("Couldn't reach Moxfield. Try again in a moment.") from exc
    if resp.status_code in (401, 403, 429, 503) or _looks_like_cloudflare(resp):
        raise ImportError(_CLOUDFLARE_MSG)
    if resp.status_code == 404:
        raise ImportError("Deck not found on Moxfield (is it public?).")
    if resp.status_code != 200:
        raise ImportError(f"Moxfield returned HTTP {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise ImportError("Moxfield returned an unexpected response.") from exc


def _extract_public_id(deck_ref: str) -> Optional[str]:
    raw = (deck_ref or "").strip()
    if not raw:
        return None
    if "/" not in raw and _PUBLIC_ID_RE.match(raw):
        return raw
    parts = urlsplit(raw)
    segments = [seg for seg in (parts.path or "").split("/") if seg]
    for key in ("decks", "deck"):
        if key in segments:
            idx = segments.index(key)
            if idx + 1 < len(segments):
                candidate = segments[idx + 1]
                if _PUBLIC_ID_RE.match(candidate):
                    return candidate
    return None


def _identity_from_letters(letters: Any) -> Optional[str]:
    if not isinstance(letters, (list, tuple, set)):
        return None
    present = {str(c)[:1].upper() for c in letters if c}
    ident = "".join(c for c in _COLOR_ORDER if c in present)
    return ident or None


def _iter_board_cards(data: dict[str, Any]):
    """Yield (board_name, entry) across both v3 (boards) and v2 (flat) shapes."""
    boards = data.get("boards")
    if isinstance(boards, dict):
        for board_name, board in boards.items():
            cards = (board or {}).get("cards")
            if isinstance(cards, dict):
                for entry in cards.values():
                    yield board_name.lower(), entry
        return
    # v2 flat fallback
    for board_name in ("commanders", "mainboard", "companions"):
        cards = data.get(board_name)
        if isinstance(cards, dict):
            for entry in cards.values():
                yield board_name.lower(), entry


def fetch_deck(deck_ref: str) -> ImportedDeck:
    public_id = _extract_public_id(deck_ref)
    if not public_id:
        raise ImportError("Couldn't find a Moxfield deck id in that link.")

    data = _get_json(f"{_API}/v3/decks/all/{quote(public_id)}")
    if not isinstance(data, dict):
        raise ImportError("Moxfield returned an unexpected response.")

    commanders: list[str] = []
    cards: list[dict[str, Any]] = []
    identity_letters: set[str] = set()

    for board_name, entry in _iter_board_cards(data):
        if not isinstance(entry, dict):
            continue
        card = entry.get("card") or {}
        name = (card.get("name") or "").strip()
        if not name:
            continue
        qty = int(entry.get("quantity") or 1)
        for c in card.get("color_identity") or []:
            identity_letters.add(str(c)[:1].upper())
        if board_name == "commanders":
            if name not in commanders:
                commanders.append(name)
            continue
        if board_name in _EXCLUDED_BOARDS:
            continue
        cards.append({"name": name, "quantity": qty})

    color_identity = _identity_from_letters(
        data.get("colorIdentity") or data.get("colors")
    ) or _identity_from_letters(identity_letters)

    fmt = (data.get("format") or "").strip().lower() or None

    return ImportedDeck(
        source="moxfield",
        source_id=public_id,
        url=f"https://www.moxfield.com/decks/{public_id}",
        name=(data.get("name") or "Untitled deck").strip(),
        commanders=commanders,
        color_identity=color_identity,
        format=fmt,
        bracket=None,
        cards=cards,
    )


def list_user_decks(username: str | None, *, limit: int = 60) -> list[dict[str, Any]]:
    handle = (username or "").strip().lstrip("@")
    match = re.search(r"moxfield\.com/users/([^/?#]+)", handle, re.IGNORECASE)
    if match:
        handle = match.group(1)
    if not handle or not _USERNAME_RE.match(handle):
        raise ImportError("Enter a valid Moxfield username.")

    url = f"{_API}/v2/users/{quote(handle)}/decks?pageNumber=1&pageSize={min(limit, 100)}"
    data = _get_json(url)
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ImportError(f"No public decks found for '{handle}'.")

    decks: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        public_id = item.get("publicId")
        if not public_id:
            continue
        fmt = (item.get("format") or "").strip().lower()
        decks.append(
            {
                "source": "moxfield",
                "source_id": str(public_id),
                "name": (item.get("name") or "Untitled deck").strip(),
                "bracket": None,
                "color_identity": _identity_from_letters(item.get("colorIdentity") or item.get("colors")),
                "card_count": item.get("mainboardCount") or item.get("totalCardCount"),
                "format": fmt or None,
                "url": f"https://www.moxfield.com/decks/{public_id}",
            }
        )
        if len(decks) >= limit:
            break
    if not decks:
        raise ImportError(f"No public decks found for '{handle}'.")
    return decks


__all__ = ["fetch_deck", "list_user_decks"]
