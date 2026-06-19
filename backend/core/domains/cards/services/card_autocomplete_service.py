"""Fast card-name autocomplete backed by the local Scryfall cache.

Powers the EDHREC-style "as you type" suggestions on the app's search fields.
A distinct, alphabetically-sorted name index is built once per cache epoch so
each keystroke is a cheap bisect (prefix matches) plus a bounded substring
scan — no per-request rescan of the full ~25k-print catalog.
"""

from __future__ import annotations

import bisect
from functools import lru_cache

from core.domains.cards.services import scryfall_cache as sc

MAX_LIMIT = 20
MIN_QUERY_LEN = 2


@lru_cache(maxsize=2)
def _name_index(_epoch: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(lower_keys, display_names)`` aligned and sorted by lower key.

    Cached on the cache epoch so it rebuilds only when the catalog changes.
    """
    sc.ensure_cache_loaded()
    prints = sc.get_all_prints() or []
    seen: dict[str, str] = {}
    for print_obj in prints:
        name = (print_obj.get("name") or "").strip()
        if not name:
            continue
        # Collapse reversible-card data artifacts ("Sol Ring // Sol Ring") to the
        # single front-face name; keep genuine double-faced names ("Delver of
        # Secrets // Insectile Aberration") intact.
        if " // " in name:
            front, _, back = name.partition(" // ")
            if front.strip().casefold() == back.strip().casefold():
                name = front.strip()
        key = name.lower()
        # First display form wins; names are identical across printings anyway.
        seen.setdefault(key, name)
    ordered = sorted(seen.items())
    keys = tuple(item[0] for item in ordered)
    names = tuple(item[1] for item in ordered)
    return keys, names


def autocomplete_card_names(query: str | None, limit: int = 10) -> list[str]:
    """Return up to ``limit`` distinct card names matching ``query``.

    Names that *start with* the query rank first (the common case), followed by
    names that contain it elsewhere. Returns ``[]`` for very short queries.
    """
    text = (query or "").strip().lower()
    if len(text) < MIN_QUERY_LEN:
        return []
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, MAX_LIMIT))

    try:
        keys, names = _name_index(sc.cache_epoch())
    except Exception:
        return []
    if not keys:
        return []

    results: list[str] = []
    seen_idx: set[int] = set()

    # Prefix matches via bisect (keys are sorted, so they're contiguous).
    start = bisect.bisect_left(keys, text)
    idx = start
    while idx < len(keys) and keys[idx].startswith(text) and len(results) < limit:
        results.append(names[idx])
        seen_idx.add(idx)
        idx += 1

    # Substring fallback to fill the remaining slots ("bolt" -> "Galvanic Bolt").
    if len(results) < limit:
        for i, key in enumerate(keys):
            if len(results) >= limit:
                break
            if i in seen_idx:
                continue
            if text in key:
                results.append(names[i])
    return results


__all__ = ["autocomplete_card_names"]
