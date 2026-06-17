"""Shared helpers for the unified "core role / evergreen / land type" search
(`role_q`) used by both the collection and Scryfall browsers.

The search must match a term against three sources — derived core-role tags,
derived evergreen/keyword tags, and the card's own type line (covering land
types, creature subtypes, and card types). Matching is done at *word starts* so
a search for "ramp" matches the tag "Ramp" but not "T**ramp**le", and
"advantage" matches "Card Advantage Utility".
"""

from __future__ import annotations

import re


def role_query_tokens(text_value: str | None) -> set[str]:
    """Normalized search tokens: the lower-cased term plus a variant with
    ``-``/``_`` folded to spaces (so "go-tall"/"go_tall" match "Go Tall")."""
    base = (text_value or "").lower().strip()
    if not base:
        return set()
    alt = re.sub(r"[_-]+", " ", base).strip()
    return {token for token in (base, alt) if token}


def role_query_like_patterns(text_value: str | None) -> list[str]:
    """SQL ``ILIKE`` patterns matching a token at the start of any word, e.g.
    ``ramp%`` (start of the value) and ``% ramp%`` (start of a later word)."""
    patterns: list[str] = []
    for token in role_query_tokens(text_value):
        patterns.append(f"{token}%")
        patterns.append(f"% {token}%")
    return patterns


def text_matches_role_tokens(text_value: str | None, tokens: set[str]) -> bool:
    """Python equivalent of :func:`role_query_like_patterns` for filtering
    already-fetched records (e.g. Scryfall results) by their type line."""
    if not tokens:
        return False
    haystack = (text_value or "").lower()
    if not haystack:
        return False
    for token in tokens:
        if haystack.startswith(token) or (" " + token) in haystack:
            return True
    return False


__all__ = [
    "role_query_like_patterns",
    "role_query_tokens",
    "text_matches_role_tokens",
]
