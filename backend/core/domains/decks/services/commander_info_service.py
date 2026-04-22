"""Commander bracket reference and Spellbook combo views."""

from __future__ import annotations

import math
from collections import Counter
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from flask import render_template, request, url_for

from core.domains.cards.services.scryfall_cache import (
    cache_epoch,
    cache_ready,
    ensure_cache_loaded,
    prints_for_oracle,
    unique_oracle_by_name,
)
from core.domains.decks.services.commander_bracket_reference_service import (
    BRACKET_REFERENCE,
    BRACKET_REFERENCE_BY_LEVEL,
    GAME_CHANGERS,
)
from core.domains.decks.services.commander_spellbook_service import (
    SPELLBOOK_EARLY_COMBOS,
    SPELLBOOK_LATE_COMBOS,
    SPELLBOOK_RESULT_LABELS,
)
from core.domains.decks.services.spellbook_sync import (
    EARLY_MANA_VALUE_THRESHOLD,
    LATE_MANA_VALUE_THRESHOLD,
)
from core.shared.utils.symbols_cache import render_mana_html

_MASS_LAND_FEATURED = [
    "Vorinclex, Voice of Hunger",
    "Hall of Gemstone",
    "Contamination",
    "Cataclysm",
    "Dimensional Breach",
    "Epicenter",
    "Global Ruin",
    "Hokori, Dust Drinker",
    "Razia's Purification",
    "Rising Waters",
    "Soulscour",
    "Sunder",
    "Apocalypse",
    "Bearer of the Heavens",
    "Conversion",
    "Glaciers",
    "Pox",
    "Death Cloud",
    "Tangle Wire",
    "Restore Balance",
    "Realm Razer",
    "Spreading Algae",
    "Numot, the Devastator",
    "Giltleaf Archdruid",
    "Kudzu",
    "Demonic Hordes",
    "Urza's Sylex",
    "Infernal Darkness",
    "Trinisphere",
    "Worldfire",
    "Worldslayer",
    "Worldpurge",
    "Stasis",
]

_EXTRA_TURN_CHAINERS = [
    "Time Warp",
    "Temporal Manipulation",
    "Walk the Aeons",
    "Capture of Jingzhou",
    "Expropriate",
    "Time Stretch",
    "Nexus of Fate",
    "Timestream Navigator",
    "Sage of Hours",
    "Lighthouse Chronologist",
    "Time Sieve",
]


@lru_cache(maxsize=1024)
def commander_card_snapshot(name: str, epoch: int) -> Dict[str, Any]:
    """Resolve and cache lightweight Scryfall details for reference cards."""
    _ = epoch  # include the Scryfall cache generation in the LRU key
    try:
        oracle_id = unique_oracle_by_name(name)
    except Exception:
        oracle_id = None

    pr = None
    if oracle_id:
        try:
            prints = prints_for_oracle(oracle_id) or ()
        except Exception:
            prints = ()
        if prints:
            pr = prints[0]

    scryfall_id = None
    scryfall_uri = None
    set_code = None
    set_name = None
    collector_number = None
    thumb = None
    hover = None

    if pr:
        scryfall_id = pr.get("id")
        scryfall_uri = pr.get("scryfall_uri")
        set_code = pr.get("set")
        set_name = pr.get("set_name")
        collector_number = pr.get("collector_number")
        image_uris = pr.get("image_uris") or {}
        hover = image_uris.get("large") or image_uris.get("normal") or image_uris.get("small")
        thumb = image_uris.get("small") or image_uris.get("normal") or hover

    return {
        "name": name,
        "oracle_id": oracle_id,
        "scryfall_id": scryfall_id,
        "scryfall_uri": scryfall_uri,
        "set": set_code,
        "set_name": set_name,
        "collector_number": collector_number,
        "hover": hover,
        "thumb": thumb,
    }


def commander_brackets_info():
    focus_level = request.args.get("focus", type=int)
    if focus_level not in BRACKET_REFERENCE_BY_LEVEL:
        focus_level = None

    if not cache_ready():
        ensure_cache_loaded()
    epoch = cache_epoch()
    game_changers = [dict(commander_card_snapshot(name, epoch)) for name in sorted(GAME_CHANGERS)]
    mass_land_cards = [dict(commander_card_snapshot(name, epoch)) for name in _MASS_LAND_FEATURED]
    extra_turn_cards = [dict(commander_card_snapshot(name, epoch)) for name in _EXTRA_TURN_CHAINERS]

    return render_template(
        "decks/commander_brackets.html",
        brackets=BRACKET_REFERENCE,
        focus_level=focus_level,
        focus_entry=BRACKET_REFERENCE_BY_LEVEL.get(focus_level) if focus_level else None,
        source_url="https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-october-21-2025",
        game_changers=game_changers,
        mass_land_cards=mass_land_cards,
        extra_turn_cards=extra_turn_cards,
    )


def commander_spellbook_combos():
    def _card_entries(combo):
        cards = []
        requirements = getattr(combo, "requirements", {}) or {}
        for name in combo.cards or ():
            key = name.casefold()
            qty = requirements.get(key, 1)
            encoded = quote_plus(name)
            cards.append(
                {
                    "name": name,
                    "quantity": qty if qty > 1 else 1,
                    "thumb": f"https://api.scryfall.com/cards/named?format=image&version=small&exact={encoded}",
                    "hover": f"https://api.scryfall.com/cards/named?format=image&version=large&exact={encoded}",
                    "search_url": f"https://scryfall.com/search?q=%21%22{encoded}%22",
                }
            )
        return cards

    def _serialize_combo(combo):
        categories = list(combo.result_categories or [])
        raw_mana_needed = combo.mana_needed or ""
        mana_icons_html: Optional[str] = None
        mana_note = ""
        identity = (combo.identity or "").strip().upper()
        color_letters = [letter for letter in identity if letter]
        if isinstance(raw_mana_needed, str) and raw_mana_needed.strip():
            mana_lines = [line for line in raw_mana_needed.splitlines()]
            if mana_lines:
                icons_line = mana_lines[0].strip()
                if icons_line:
                    mana_icons_html = render_mana_html(icons_line, use_local=True)
                remaining = [line.strip() for line in mana_lines[1:] if line.strip()]
                mana_note = "\n".join(remaining)
        elif raw_mana_needed:
            mana_note = str(raw_mana_needed)
        return {
            "id": combo.id,
            "cards": _card_entries(combo),
            "results": list(combo.results or []),
            "mana_value": combo.mana_value_needed if combo.mana_value_needed is not None else "-",
            "mana_icons_html": mana_icons_html,
            "mana_note": mana_note,
            "mana_needed": raw_mana_needed,
            "result_labels": [SPELLBOOK_RESULT_LABELS.get(cat, cat.replace("_", " ")) for cat in categories],
            "categories": categories,
            "identity": identity,
            "colors": [letter.lower() for letter in color_letters],
            "url": combo.url or f"https://commanderspellbook.com/combo/{combo.id}",
            "normalized_mana_value": getattr(combo, "normalized_mana_value", None),
        }

    early_serialized = []
    for combo in SPELLBOOK_EARLY_COMBOS:
        payload = _serialize_combo(combo)
        payload["stage_key"] = "early"
        payload["stage_label"] = "Early Game"
        early_serialized.append(payload)

    late_serialized = []
    for combo in SPELLBOOK_LATE_COMBOS:
        payload = _serialize_combo(combo)
        payload["stage_key"] = "late"
        payload["stage_label"] = "Late Game"
        late_serialized.append(payload)

    category_counts = Counter()
    for combo in SPELLBOOK_EARLY_COMBOS + SPELLBOOK_LATE_COMBOS:
        for tag in combo.result_categories or ():
            category_counts[tag] += 1

    categories = [
        {
            "key": key,
            "label": SPELLBOOK_RESULT_LABELS.get(key, key.replace("_", " ")),
            "count": count,
        }
        for key, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    totals = {
        "early": len(early_serialized),
        "late": len(late_serialized),
        "total": len(early_serialized) + len(late_serialized),
    }

    thresholds = {
        "early": EARLY_MANA_VALUE_THRESHOLD,
        "late": LATE_MANA_VALUE_THRESHOLD,
    }

    combos = early_serialized + late_serialized

    search_raw = (request.args.get("q") or "").strip()
    search_term = search_raw.lower()

    selected_stage = (request.args.get("stage") or "").lower()
    if selected_stage not in {"early", "late"}:
        selected_stage = ""

    selected_categories = [value for value in request.args.getlist("category") if value]
    category_filters = [value.lower() for value in selected_categories]

    selected_colors = [
        value.lower()
        for value in request.args.getlist("color")
        if value and value.lower() in {"w", "u", "b", "r", "g", "c"}
    ]
    color_mode = (request.args.get("color_mode") or "contains").lower()
    if color_mode not in {"contains", "exact"}:
        color_mode = "contains"

    filtered_combos: List[Dict[str, Any]] = []
    for combo in combos:
        if selected_stage and combo["stage_key"] != selected_stage:
            continue
        if category_filters:
            combo_category_keys = [cat.lower() for cat in (combo.get("categories") or [])]
            if not any(cat in combo_category_keys for cat in category_filters):
                continue
        if selected_colors:
            combo_colors = [color.lower() for color in (combo.get("colors") or [])]
            if color_mode == "exact":
                if len(combo_colors) != len(selected_colors) or set(combo_colors) != set(selected_colors):
                    continue
            else:
                if not all(color in combo_colors for color in selected_colors):
                    continue
        if search_term:
            haystack_parts: List[str] = []
            haystack_parts.extend(card["name"] for card in combo.get("cards") or [])
            haystack_parts.extend(combo.get("results") or [])
            haystack_parts.extend(combo.get("result_labels") or [])
            haystack_parts.extend(combo.get("categories") or [])
            haystack_parts.append(combo.get("stage_label") or "")
            haystack_parts.append(combo.get("mana_note") or "")
            haystack_parts.append(combo.get("mana_needed") or "")
            haystack = " ".join(part for part in haystack_parts if part).lower()
            if search_term not in haystack:
                continue
        filtered_combos.append(combo)

    filtered_totals = {
        "early": sum(1 for combo in filtered_combos if combo["stage_key"] == "early"),
        "late": sum(1 for combo in filtered_combos if combo["stage_key"] == "late"),
    }
    filtered_totals["total"] = len(filtered_combos)

    sort = request.args.get("sort") or "stage"
    if sort not in {"results", "stage", "mana"}:
        sort = "stage"

    direction = request.args.get("direction") or "asc"
    if direction not in {"asc", "desc"}:
        direction = "asc"
    reverse = direction == "desc"

    stage_order = {"early": 0, "late": 1}

    def _stage_key(combo: Dict[str, Any]) -> Tuple[Any, ...]:
        normalized = combo.get("normalized_mana_value")
        if normalized is None:
            normalized = float("inf")
        return (
            stage_order.get(combo["stage_key"], 99),
            normalized,
            " ".join(combo.get("results") or []).lower(),
        )

    def _results_key(combo: Dict[str, Any]) -> Tuple[Any, ...]:
        key = " ".join(combo.get("results") or []).lower()
        normalized = combo.get("normalized_mana_value")
        if normalized is None:
            normalized = float("inf")
        return (
            key,
            stage_order.get(combo["stage_key"], 99),
            normalized,
        )

    def _mana_key(combo: Dict[str, Any]) -> Tuple[Any, ...]:
        normalized = combo.get("normalized_mana_value")
        if normalized is None:
            normalized = float("inf")
        return (
            normalized,
            stage_order.get(combo["stage_key"], 99),
            " ".join(combo.get("results") or []).lower(),
        )

    sort_key_map = {
        "stage": _stage_key,
        "results": _results_key,
        "mana": _mana_key,
    }

    filtered_combos.sort(key=sort_key_map[sort], reverse=reverse)

    total = filtered_totals["total"]

    per_raw = request.args.get("per") or request.args.get("per_page") or request.args.get("page_size")
    try:
        per = int(per_raw)
    except (TypeError, ValueError):
        per = 25
    per = max(5, min(per, 100))

    page = request.args.get("page", type=int) or 1
    if page < 1:
        page = 1

    pages = max(1, math.ceil(total / per)) if per else 1
    if page > pages:
        page = pages

    start_idx = (page - 1) * per if total else 0
    end_idx = start_idx + per
    page_combos = filtered_combos[start_idx:end_idx]

    display_start = start_idx + 1 if total else 0
    display_end = min(end_idx, total)

    def _build_url(**updates: Any) -> str:
        args = request.args.to_dict(flat=False)
        for key, value in updates.items():
            if value is None:
                args.pop(key, None)
            elif isinstance(value, list):
                args[key] = value
            else:
                args[key] = [value]
        params: Dict[str, Any] = {}
        for key, values in args.items():
            params[key] = values[0] if len(values) == 1 else values
        return url_for("views.commander_spellbook_combos", **params)

    return render_template(
        "decks/spellbook_combos.html",
        combos=page_combos,
        categories=categories,
        totals=totals,
        thresholds=thresholds,
        page=page,
        pages=pages,
        per=per,
        page_start=display_start,
        page_end=display_end,
        filtered_totals=filtered_totals,
        search_query=search_raw,
        selected_stage=selected_stage,
        selected_categories=selected_categories,
        selected_colors=selected_colors,
        color_mode=color_mode,
        sort=sort,
        direction=direction,
        build_url=_build_url,
    )


__all__ = [
    "commander_brackets_info",
    "commander_card_snapshot",
    "commander_spellbook_combos",
]
