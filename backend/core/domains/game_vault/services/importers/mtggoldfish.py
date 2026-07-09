"""MTGGoldfish importer (no JSON API).

MTGGoldfish exposes a plain-text decklist at ``/deck/download/<id>`` and deck
metadata (name, commander) in the deck page HTML. There is no per-user deck
listing endpoint, so only URL/id import is supported.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any, Optional
from urllib.parse import urlsplit

from shared.http_client import EXTERNAL_SERVICE_TIMEOUT, safe_get

from .base import ImportedDeck, ImportError

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DragonsVault-GameVault/1.0; +https://github.com/JBSmith29/DragonsVault.app)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_QTY_LINE = re.compile(r"^\s*(\d+)\s*x?\s+(.+?)\s*$", re.IGNORECASE)
_SECTION = re.compile(r"^(.+?)\s*\(\d+\)\s*$")
_KNOWN_SECTIONS = {
    "deck", "commander", "commanders", "companion", "sideboard", "maybeboard", "tokens",
}


def _extract_id(deck_ref: str) -> Optional[str]:
    raw = (deck_ref or "").strip()
    if raw.isdigit():
        return raw
    parts = urlsplit(raw)
    match = re.search(r"/deck/(?:view/)?(\d+)", parts.path or "")
    if match:
        return match.group(1)
    match = re.search(r"/deck/(?:view/)?(\d+)", raw)
    return match.group(1) if match else None


def _fetch_text(url: str) -> str:
    try:
        resp = safe_get(url, timeout=EXTERNAL_SERVICE_TIMEOUT, headers=_HEADERS)
    except Exception as exc:
        raise ImportError("Couldn't reach MTGGoldfish. Try again in a moment.") from exc
    if resp.status_code == 404:
        raise ImportError("Deck not found on MTGGoldfish.")
    if resp.status_code != 200:
        raise ImportError(f"MTGGoldfish returned HTTP {resp.status_code}.")
    return resp.text or ""


def _parse_name_and_commander(html: str) -> tuple[Optional[str], Optional[str]]:
    deck_name: Optional[str] = None

    h1 = re.search(
        r"<h1[^>]*class=['\"](?:deck-view-title|title)['\"][^>]*>(.*?)</h1>",
        html, re.IGNORECASE | re.DOTALL,
    )
    if h1:
        inner = re.sub(r"<span[^>]*class=['\"]author['\"][^>]*>.*?</span>", "", h1.group(1),
                       flags=re.IGNORECASE | re.DOTALL)
        inner = re.sub(r"<[^>]+>", "", inner)
        candidate = unescape(inner).strip()
        if candidate and candidate.lower() != "deck":
            deck_name = candidate
    if not deck_name:
        og = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
                       html, re.IGNORECASE)
        if og:
            deck_name = re.sub(r"\s+-\s+MTGGoldfish.*$", "", unescape(og.group(1))).strip() or None
    if not deck_name:
        title = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title:
            deck_name = re.sub(r"\s+-\s+MTGGoldfish.*$", "", unescape(title.group(1))).strip() or None
    if deck_name and deck_name.startswith('"') and deck_name.endswith('"') and len(deck_name) > 1:
        deck_name = deck_name[1:-1].strip()

    commander = None
    hidden = re.search(
        r'name=["\']deck_input\[commander\]["\'][^>]*value=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    )
    if hidden:
        commander = unescape(hidden.group(1)).strip() or None
    return deck_name, commander


def fetch_deck(deck_ref: str) -> ImportedDeck:
    deck_id = _extract_id(deck_ref)
    if not deck_id:
        raise ImportError("Couldn't find an MTGGoldfish deck id in that link.")

    deck_text = _fetch_text(f"https://www.mtggoldfish.com/deck/download/{deck_id}")
    try:
        html = _fetch_text(f"https://www.mtggoldfish.com/deck/{deck_id}")
    except ImportError:
        html = ""

    deck_name, commander = _parse_name_and_commander(html)

    commanders: list[str] = []
    cards: list[dict[str, Any]] = []
    current_section: Optional[str] = None

    for raw in deck_text.splitlines():
        line = raw.strip()
        if not line:
            current_section = None
            continue
        section = _SECTION.match(line)
        if section and not re.match(r"^\d", line):
            current_section = section.group(1).strip().lower()
            continue
        if line.lower() in _KNOWN_SECTIONS:
            current_section = line.lower()
            continue
        m = _QTY_LINE.match(line)
        if not m:
            continue
        qty, name = int(m.group(1)), m.group(2).strip()
        if current_section in ("commander", "commanders"):
            if name not in commanders:
                commanders.append(name)
            continue
        cards.append({"name": name, "quantity": qty})

    # Fall back to the HTML-declared commander and de-dupe it from the mainboard.
    if commander and commander not in commanders:
        commanders.append(commander)
    if commanders:
        commander_set = {c.lower() for c in commanders}
        cards = [c for c in cards if c["name"].lower() not in commander_set]

    if not deck_name:
        deck_name = "MTGGoldfish deck"

    return ImportedDeck(
        source="mtggoldfish",
        source_id=deck_id,
        url=f"https://www.mtggoldfish.com/deck/{deck_id}",
        name=deck_name,
        commanders=commanders,
        color_identity=None,  # enriched from the commander via Scryfall
        format="commander" if commanders else None,
        bracket=None,
        cards=cards,
    )


__all__ = ["fetch_deck"]
