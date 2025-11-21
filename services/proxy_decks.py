"""Helpers for importing and managing proxy decks.

These utilities parse simple decklists (one card per line, with optional leading
quantities) and resolve lightweight printing metadata via the local Scryfall
cache so downstream features (deck insights, commander brackets, etc.) continue
to function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Iterable, List, Tuple
from urllib.parse import SplitResult, urlsplit, urlunsplit

import requests

from flask import current_app
from services import scryfall_cache as sc
from services.scryfall_cache import ensure_cache_loaded, prints_for_oracle, unique_oracle_by_name


_LINE_QUANTITY = re.compile(r"^\s*(\d+)\s*x?\s+(.+?)\s*$", flags=re.IGNORECASE)
_TRAILING_QUANTITY = re.compile(r"^\s*(.+?)\s*x\s*(\d+)\s*$", flags=re.IGNORECASE)
_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DragonsVault/1.0; +https://github.com/)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_ALLOWED_GOLDFISH_HOSTS = {"mtggoldfish.com", "www.mtggoldfish.com"}
_ALLOWED_GOLDFISH_PORTS = {None, 80, 443}
_ALLOWED_SCHEMES = {"http", "https"}


@dataclass(slots=True)
class ResolvedCard:
    name: str
    quantity: int
    oracle_id: str | None
    set_code: str
    collector_number: str
    lang: str


def parse_decklist(lines: Iterable[str]) -> List[Tuple[str, int]]:
    """Return list of (card_name, quantity) pairs from raw decklist lines."""
    entries: List[Tuple[str, int]] = []
    for raw in lines:
        line = (raw or "").strip().strip('"').strip("'")
        if not line:
            continue

        qty = 1
        name = line

        leading = _LINE_QUANTITY.match(line)
        trailing = None if leading else _TRAILING_QUANTITY.match(line)
        if leading:
            qty = int(leading.group(1))
            name = leading.group(2)
        elif trailing:
            name = trailing.group(1)
            qty = int(trailing.group(2))

        clean = name.strip()
        if not clean:
            continue

        entries.append((clean, max(qty, 1)))
    return entries


def _pick_preferred_print(printings: List[dict]) -> dict | None:
    if not printings:
        return None
    for pr in printings:
        if pr.get("digital"):
            continue
        if (pr.get("lang") or "en").lower() == "en":
            return pr
    for pr in printings:
        if (pr.get("lang") or "en").lower() == "en":
            return pr
    return printings[0]


def resolve_proxy_cards(deck_lines: Iterable[str]) -> Tuple[List[ResolvedCard], List[str]]:
    """Resolve decklist rows into ResolvedCard objects, capturing any errors."""
    ensure_cache_loaded()

    resolved: List[ResolvedCard] = []
    errors: List[str] = []

    for raw_name, quantity in parse_decklist(deck_lines):
        oracle_id = None
        try:
            oracle_id = unique_oracle_by_name(raw_name)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"Could not resolve '{raw_name}': {exc}")
            oracle_id = None

        printings: List[dict] = []
        if oracle_id:
            try:
                printings = prints_for_oracle(oracle_id) or []
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"Scryfall lookup failed for '{raw_name}': {exc}")
                printings = []

        pr = _pick_preferred_print(printings)
        if not pr:
            # fall back to a synthetic placeholder: ensure downstream lookups do not crash
            resolved.append(
                ResolvedCard(
                    name=raw_name,
                    quantity=quantity,
                    oracle_id=oracle_id,
                    set_code="CSTM",
                    collector_number="P000",
                    lang="en",
                )
            )
            continue

        set_code = (pr.get("set") or "cstm").upper()
        collector_number = pr.get("collector_number") or "0"
        lang = (pr.get("lang") or "en").lower()

        resolved.append(
            ResolvedCard(
                name=pr.get("name") or raw_name,
                quantity=quantity,
                oracle_id=pr.get("oracle_id") or oracle_id,
                set_code=set_code,
                collector_number=str(collector_number),
                lang=lang,
            )
        )

    return resolved, errors


def _normalize_goldfish_url(deck_url: str) -> str:
    """Strip fragments and normalise the deck URL for reliable fetching."""
    raw = (deck_url or "").strip()
    if not raw:
        raw = "https://www.mtggoldfish.com/"

    parts = urlsplit(raw)

    # Handle schemeless URLs like //www.mtggoldfish.com/deck/123
    if not parts.netloc and parts.path.startswith("//"):
        parts = urlsplit(f"https:{parts.path}")

    scheme = (parts.scheme or "https").lower()
    if scheme not in _ALLOWED_SCHEMES:
        scheme = "https"

    hostname = (parts.hostname or "www.mtggoldfish.com").lower()
    port = parts.port
    if port:
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = parts.path or "/"
    query = parts.query

    return urlunsplit((scheme, netloc, path, query, ""))


def _is_allowed_goldfish_location(parts: SplitResult) -> bool:
    hostname = (parts.hostname or "").lower()
    if hostname not in _ALLOWED_GOLDFISH_HOSTS:
        return False
    if parts.port not in _ALLOWED_GOLDFISH_PORTS:
        return False
    scheme = (parts.scheme or "").lower()
    if scheme and scheme not in _ALLOWED_SCHEMES:
        return False
    return True


def fetch_goldfish_deck(deck_url: str) -> Tuple[str | None, str | None, str | None, List[str], List[str]]:
    """
    Fetch deck metadata and list from an MTGGoldfish deck URL.

    Returns (deck_name, owner_name, commander_name, lines, errors).
    """
    errors: List[str] = []
    if not deck_url:
        return None, None, None, [], ["No deck URL provided."]

    deck_url = deck_url.strip()
    cleaned_url = _normalize_goldfish_url(deck_url)
    cleaned_parts = urlsplit(cleaned_url)

    if not _is_allowed_goldfish_location(cleaned_parts):
        host = cleaned_parts.hostname or "(unknown host)"
        port = cleaned_parts.port
        port_text = f":{port}" if port else ""
        errors.append(f"Unsupported MTGGoldfish host {host}{port_text}.")
        return None, None, None, [], errors

    match = re.search(r"/deck/(?:view/)?(\d+)", cleaned_parts.path or "")
    if not match:
        errors.append("Could not find a deck id in the MTGGoldfish URL.")
        return None, None, None, [], errors

    deck_id = match.group(1)
    download_url = f"https://www.mtggoldfish.com/deck/download/{deck_id}"

    deck_text = ""
    try:
        resp = requests.get(download_url, timeout=10, headers=_REQUEST_HEADERS)
        resp.raise_for_status()
        deck_text = resp.text or ""
    except Exception as exc:
        errors.append(f"Failed to download decklist from MTGGoldfish: {exc}")

    deck_name = None
    owner = None
    commander_name = None

    try:
        page_resp = requests.get(cleaned_url, timeout=10, headers=_REQUEST_HEADERS)
        page_resp.raise_for_status()
        html = page_resp.text or ""

        # Deck name via page title (new layout uses class="title")
        h1_match = re.search(r"<h1[^>]*class=['\"](?:deck-view-title|title)['\"][^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
        if h1_match:
            inner = h1_match.group(1)
            # Remove author span from inner HTML
            inner = re.sub(r"<span[^>]*class=['\"]author['\"][^>]*>.*?</span>", "", inner, flags=re.IGNORECASE | re.DOTALL)
            inner = re.sub(r"<[^>]+>", "", inner)
            candidate = unescape(inner).strip()
            if candidate and candidate.lower() != "deck":
                deck_name = candidate

        # Fallback to <title> or og:title
        if not deck_name:
            title_tag = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if title_tag:
                candidate = unescape(title_tag.group(1)).strip()
                candidate = re.sub(r"\s+-\s+MTGGoldfish.*$", "", candidate).strip()
                deck_name = candidate
        if not deck_name:
            og_title = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if og_title:
                candidate = unescape(og_title.group(1)).strip()
                candidate = re.sub(r"\s+Deck$", "", candidate, flags=re.IGNORECASE).strip()
                deck_name = candidate

        if deck_name:
            deck_name = deck_name.strip()
            if deck_name.startswith('"') and deck_name.endswith('"') and len(deck_name) > 1:
                deck_name = deck_name[1:-1].strip()

        # Owner parsing
        owner_match = re.search(r"<span[^>]*class=['\"]author['\"][^>]*>by\s+([^<]+)</span>", html, re.IGNORECASE)
        if owner_match:
            owner = unescape(owner_match.group(1)).strip()
        if not owner:
            owner_meta = re.search(r'<meta\s+name=["\']author["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if owner_meta:
                owner = unescape(owner_meta.group(1)).strip()
        if not owner:
            legacy_owner = re.search(r'By\s*<a[^>]*href="[^"]*/profile/[^"]*"[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL)
            if legacy_owner:
                owner = re.sub(r"\s+", " ", unescape(legacy_owner.group(1))).strip()

        # Commander info from hidden input
        commander_input = re.search(r'name=["\']deck_input\[commander\]["\'][^>]*value=["\']([^"\']*)["\']', html, re.IGNORECASE)
        if commander_input:
            commander_candidate = unescape(commander_input.group(1)).strip()
            if commander_candidate:
                commander_name = commander_candidate
    except Exception as exc:
        current_app.logger.info("Unable to fetch MTGGoldfish deck metadata: %s", exc)
        errors.append(f"Failed to fetch deck metadata from MTGGoldfish: {exc}")

    deck_lines_raw = deck_text.splitlines() if deck_text else []
    filtered_lines: List[str] = []
    current_section: str | None = None

    for raw in deck_lines_raw:
        stripped = (raw or "").strip()
        if not stripped:
            continue

        section_match = re.match(r"^(.+?)\s*\(\d+\)$", stripped)
        if section_match and not re.match(r"^\d+", stripped):
            current_section = section_match.group(1).strip().lower()
            continue

        if not re.match(r"^\d+", stripped):
            continue

        filtered_lines.append(stripped)

        if current_section == "commander" and commander_name is None:
            cmd_match = re.match(r"^\d+\s*x?\s*(.+)$", stripped, flags=re.IGNORECASE)
            if cmd_match:
                commander_name = cmd_match.group(1).strip()

    return deck_name, owner, commander_name, filtered_lines, errors


__all__ = ["ResolvedCard", "parse_decklist", "resolve_proxy_cards", "fetch_goldfish_deck"]
