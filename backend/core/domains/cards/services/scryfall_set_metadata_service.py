"""Set-name, release, and normalization helpers for Scryfall cache wrappers."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

_ALIAS_MAP: Dict[str, str] = {
    # example vendor fixups:
    # "plist": "plst",
}


def build_set_name_map(cache: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for card in cache:
        set_code = (card.get("set") or "").lower()
        set_name = card.get("set_name")
        if set_code and set_name and set_code not in names:
            names[set_code] = set_name
    return names


def build_set_release_map(cache: list[dict[str, Any]]) -> dict[str, str]:
    releases: dict[str, str] = {}
    for card in cache:
        set_code = (card.get("set") or "").lower()
        released_at = card.get("released_at")
        if not set_code or not released_at:
            continue
        if set_code not in releases or released_at < releases[set_code]:
            releases[set_code] = released_at
    return releases


def all_set_codes(cache: Iterable[dict[str, Any]]) -> List[str]:
    return sorted(
        {
            (card.get("set") or "").lower()
            for card in cache
            if card.get("set")
        }
    )


def normalize_set_code(code: Optional[str]) -> str:
    normalized = (code or "").strip().lower()
    if not normalized:
        return normalized
    return _ALIAS_MAP.get(normalized, normalized)


__all__ = [
    "all_set_codes",
    "build_set_name_map",
    "build_set_release_map",
    "normalize_set_code",
]
