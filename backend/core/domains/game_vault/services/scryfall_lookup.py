"""Tiny, standalone Scryfall lookup used to enrich imported decks.

Deliberately independent of the app's own Scryfall cache: it calls the public
Scryfall API directly and returns only an image URL (served from
``cards.scryfall.io``, which the site CSP allows for <img>) and the card's
color identity. All failures are swallowed — enrichment is best-effort.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional
from urllib.parse import quote

from shared.http_client import SCRYFALL_TIMEOUT, safe_get

_API = "https://api.scryfall.com/cards/named"
_HEADERS = {
    "User-Agent": "DragonsVault-GameVault/1.0 (+https://github.com/JBSmith29/DragonsVault.app)",
    "Accept": "application/json",
}
_COLOR_ORDER = "WUBRG"


def _image_from_card(card: dict) -> Optional[str]:
    uris = card.get("image_uris") or {}
    if not uris:
        faces = card.get("card_faces") or []
        if faces:
            uris = (faces[0] or {}).get("image_uris") or {}
    return uris.get("art_crop") or uris.get("normal") or uris.get("large") or uris.get("small")


def _identity(card: dict) -> Optional[str]:
    letters = {str(c)[:1].upper() for c in (card.get("color_identity") or [])}
    ident = "".join(c for c in _COLOR_ORDER if c in letters)
    return ident or ("C" if card.get("color_identity") == [] else None)


@lru_cache(maxsize=2048)
def lookup_commander(name: str | None) -> tuple[Optional[str], Optional[str]]:
    """Return (image_url, color_identity) for a commander name. Best-effort.

    For partner/background pairs given as "A // B", the first name is used for
    the image and the identities are merged when a second lookup succeeds.
    """
    raw = (name or "").strip()
    if not raw:
        return None, None

    parts = [p.strip() for p in raw.split("//") if p.strip()]
    primary = parts[0] if parts else raw

    image, identity = _fetch(primary)
    if len(parts) > 1:
        _, identity2 = _fetch(parts[1])
        identity = _merge_identity(identity, identity2)
    return image, identity


def _fetch(exact_name: str) -> tuple[Optional[str], Optional[str]]:
    try:
        resp = safe_get(
            f"{_API}?exact={quote(exact_name)}",
            timeout=SCRYFALL_TIMEOUT,
            headers=_HEADERS,
        )
    except Exception:
        return None, None
    if resp.status_code != 200:
        return None, None
    try:
        card = resp.json()
    except ValueError:
        return None, None
    if not isinstance(card, dict):
        return None, None
    return _image_from_card(card), _identity(card)


def _merge_identity(a: Optional[str], b: Optional[str]) -> Optional[str]:
    letters = set((a or "").replace("C", "")) | set((b or "").replace("C", ""))
    ident = "".join(c for c in _COLOR_ORDER if c in letters)
    return ident or None


__all__ = ["lookup_commander"]
