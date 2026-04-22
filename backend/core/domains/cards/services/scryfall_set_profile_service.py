"""Pure set-profile helpers for Scryfall cache wrappers."""

from __future__ import annotations

from collections import Counter
import random
from typing import Any, Callable, Dict, Iterable, List, Optional

_set_profiles_cache: Optional[Dict[str, Dict[str, Any]]] = None


def clear_cached_set_profiles() -> None:
    global _set_profiles_cache
    _set_profiles_cache = None


def build_set_profiles(
    *,
    cache: List[Dict[str, Any]],
    ensure_cache_loaded_fn: Callable[[], bool],
) -> Dict[str, Dict[str, Any]]:
    global _set_profiles_cache
    if _set_profiles_cache is not None:
        return _set_profiles_cache

    ensure_cache_loaded_fn()
    profiles: Dict[str, Dict[str, Any]] = {}
    skip_layouts = {"token", "double_faced_token", "art_series", "emblem", "vanguard", "scheme", "plane", "planar"}
    wbgr_order = "WUBRG"

    for card in cache:
        set_code = (card.get("set") or "").lower()
        if not set_code:
            continue
        layout = (card.get("layout") or "").lower()
        if layout in skip_layouts:
            continue
        type_line = (card.get("type_line") or "").lower()
        if "token" in type_line or "emblem" in type_line:
            continue
        if "land" in type_line:
            continue

        profile = profiles.setdefault(
            set_code,
            {
                "color_counts": Counter({color: 0 for color in wbgr_order}),
                "nonland_spells": 0,
                "mv_total": 0.0,
                "mv_samples": 0,
                "mono_cards": 0,
                "multicolor_cards": 0,
                "colorless_cards": 0,
            },
        )

        profile["nonland_spells"] += 1

        mana_value = card.get("cmc")
        if mana_value is None:
            mana_value = card.get("mana_value")
        try:
            mv_value = float(mana_value)
        except (TypeError, ValueError):
            mv_value = None
        if mv_value is not None:
            profile["mv_total"] += mv_value
            profile["mv_samples"] += 1

        raw_identity = card.get("color_identity") or card.get("colors") or []
        identity = sorted({str(symbol).upper() for symbol in raw_identity if symbol})
        if not identity:
            profile["colorless_cards"] += 1
        elif len(identity) == 1:
            profile["mono_cards"] += 1
        else:
            profile["multicolor_cards"] += 1
        for symbol in identity:
            if symbol in wbgr_order:
                profile["color_counts"][symbol] += 1

    finalized: Dict[str, Dict[str, Any]] = {}
    for set_code, profile in profiles.items():
        color_counts: Counter = profile["color_counts"]
        palette = [(color, color_counts.get(color, 0)) for color in wbgr_order]
        palette.sort(key=lambda item: (-item[1], wbgr_order.index(item[0])))
        dominant_colors = [color for color, count in palette if count][:3]
        color_presence = [color for color, count in palette if count]

        if profile["mv_samples"]:
            avg_mv = round(profile["mv_total"] / profile["mv_samples"], 2)
        else:
            avg_mv = None

        if avg_mv is None:
            curve_bucket = None
        elif avg_mv <= 3.0:
            curve_bucket = "low"
        elif avg_mv <= 4.5:
            curve_bucket = "mid"
        else:
            curve_bucket = "high"

        if not color_presence:
            color_mode = "colorless"
        elif profile["multicolor_cards"] > 0:
            color_mode = "multi"
        elif len(color_presence) == 1:
            color_mode = "mono"
        else:
            color_mode = "mixed"

        finalized[set_code] = {
            "avg_mv": avg_mv,
            "curve_bucket": curve_bucket,
            "dominant_colors": dominant_colors,
            "color_presence": color_presence,
            "color_mode": color_mode,
            "nonland_spells": profile["nonland_spells"],
            "mono_cards": profile["mono_cards"],
            "multicolor_cards": profile["multicolor_cards"],
            "colorless_cards": profile["colorless_cards"],
            "color_counts": {color: color_counts.get(color, 0) for color in wbgr_order},
        }

    _set_profiles_cache = finalized
    return _set_profiles_cache


def set_profiles(
    set_codes: Optional[Iterable[str]] = None,
    *,
    cache: List[Dict[str, Any]],
    ensure_cache_loaded_fn: Callable[[], bool],
) -> Dict[str, Dict[str, Any]]:
    profiles = build_set_profiles(cache=cache, ensure_cache_loaded_fn=ensure_cache_loaded_fn)
    if set_codes is None:
        return dict(profiles)

    subset: Dict[str, Dict[str, Any]] = {}
    for code in set_codes:
        if not code:
            continue
        subset[code.lower()] = profiles.get(code.lower(), {})
    return subset


def set_image_samples(
    set_code: str,
    *,
    cache: List[Dict[str, Any]],
    image_uris_fn: Callable[[Dict[str, Any]], Dict[str, Optional[str]]],
    per_set: int = 6,
    sample_fn: Optional[Callable[[List[Dict[str, Any]], int], List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    normalized_code = (set_code or "").lower()
    if not normalized_code:
        return []

    candidates: List[Dict[str, Any]] = []
    for card in cache:
        if (card.get("set") or "").lower() != normalized_code:
            continue
        image_uris = image_uris_fn(card)
        if not (image_uris.get("small") or image_uris.get("normal") or image_uris.get("large")):
            continue
        candidates.append(
            {
                "small": image_uris.get("small"),
                "normal": image_uris.get("normal"),
                "large": image_uris.get("large"),
                "name": card.get("name"),
                "collector_number": card.get("collector_number"),
                "lang": card.get("lang"),
                "rarity": card.get("rarity"),
            }
        )

    if not candidates:
        return []
    if len(candidates) > per_set:
        sampler = sample_fn or random.sample
        return list(sampler(candidates, per_set))
    return candidates
