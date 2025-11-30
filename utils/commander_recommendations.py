"""Commander recommendation helpers."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from roles.role_engine import get_roles_for_card
from services.deck_synergy import detect_themes_for_text
from services.scryfall_cache import ensure_cache_loaded, find_by_set_cn, image_for_print, prints_for_oracle
from services.deck_utils import BASIC_LANDS

# Keep color ordering consistent with the rest of the app
WUBRG_ORDER: Tuple[str, ...] = ("W", "U", "B", "R", "G")
COLOR_BIT_MAP = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}
MANA_SYMBOL_RE = re.compile(r"\{([WUBRG]|C)\}")

# Simple mechanical hooks we can match against the collection
MECHANIC_KEYWORDS: Dict[str, Sequence[str]] = {
    "tokens": ["create a token", "tokens you control", "treasure token", "clue token", "food token"],
    "counters": ["+1/+1 counter", "proliferate", "put a +1/+1", "distribute", "bolster"],
    "graveyard": ["graveyard", "dies", "return target", "reanimate", "flashback", "jump-start", "escape"],
    "artifacts": ["artifact you control", "artifact spell", "equipment", "vehicles"],
    "spellslinger": ["instant or sorcery", "noncreature spell", "prowess", "cast an instant", "cast a sorcery"],
    "lifegain": ["gain life", "life you gain", "whenever you gain life"],
    "sacrifice": ["sacrifice a creature", "sacrifice another", "whenever a creature dies"],
    "discard": ["discard a card", "madness", "cycling", "connive"],
    "draw": ["draw a card", "whenever you draw", "investigate"],
    "landfall": ["landfall", "whenever a land enters", "play an additional land"],
    "treasures": ["treasure token", "treasures you control"],
    "spell_copy": ["copy target spell", "copy that spell"],
    "dragons": ["dragon", "dragons you control"],
}

THEME_KEYS: Tuple[str, ...] = (
    "tokens",
    "artifacts",
    "graveyard",
    "lifegain",
    "reanimator",
    "spellslinger",
    "vehicle",
    "stax",
    "control",
    "dragons",
    "equipment",
    "wheels",
    "counters",
)


@dataclass
class CardProfile:
    """Lightweight snapshot of a card + derived metadata."""

    card: Any
    type_line: str
    oracle_text: str
    color_identity: List[str]
    mana_value: Optional[float]
    roles: List[str]
    themes: Set[str]
    tribes: Set[str]
    produced_colors: Set[str]
    image_url: Optional[str]
    quantity: int


@dataclass
class CollectionProfile:
    cards: List[CardProfile]
    color_support: Counter
    role_counts: Counter
    theme_counts: Counter
    mana_curve: List[float]
    prints_map: Dict[int, dict]


def _colors_from_mask(mask: Optional[int]) -> List[str]:
    if mask is None:
        return []
    seen = []
    for letter in WUBRG_ORDER:
        if mask & COLOR_BIT_MAP.get(letter, 0):
            seen.append(letter)
    return seen


def _normalize_colors(colors: Iterable[str]) -> List[str]:
    s = {str(c).upper() for c in colors if str(c).strip()}
    return [c for c in WUBRG_ORDER if c in s]


def _extract_faces_text(print_obj: Dict[str, Any]) -> str:
    faces = print_obj.get("card_faces") or []
    texts: List[str] = []
    for face in faces:
        t = face.get("oracle_text") or ""
        if t:
            texts.append(str(t))
    return "\n".join(texts)


def _oracle_text(print_obj: Dict[str, Any]) -> str:
    raw = print_obj.get("oracle_text") or ""
    if raw:
        return str(raw)
    return _extract_faces_text(print_obj)


def _type_line(print_obj: Dict[str, Any]) -> str:
    raw = print_obj.get("type_line") or ""
    if raw:
        return str(raw)
    faces = print_obj.get("card_faces") or []
    for face in faces:
        tl = face.get("type_line") or ""
        if tl:
            return str(tl)
    return ""


def _mana_value(print_obj: Dict[str, Any]) -> Optional[float]:
    mv_raw = print_obj.get("mana_value", print_obj.get("cmc"))
    try:
        return float(mv_raw)
    except (TypeError, ValueError):
        return None


def _color_identity_for_card(card: Any, print_obj: Dict[str, Any]) -> List[str]:
    ci = print_obj.get("color_identity") or print_obj.get("colors") or []
    if not ci:
        ci_mask = getattr(card, "color_identity_mask", None)
        if ci_mask is not None:
            ci = _colors_from_mask(ci_mask)
    return _normalize_colors(ci)


def _image_for_card(print_obj: Dict[str, Any]) -> Optional[str]:
    if not print_obj:
        return None
    img = image_for_print(print_obj)
    return img.get("normal") or img.get("large") or img.get("small")


def _tribes_from_type_line(type_line: str) -> Set[str]:
    if not type_line:
        return set()
    parts = type_line.split("—")
    if len(parts) < 2:
        return set()
    tribe_part = parts[-1]
    tokens = [tok.strip() for tok in tribe_part.replace("and", ",").replace("/", " ").split() if tok.strip()]
    return {t for t in tokens if t and t[0].isupper()}


def _produced_colors_from_text(text: str) -> Set[str]:
    hits = set()
    for sym in MANA_SYMBOL_RE.findall(text or ""):
        letter = sym.upper()
        if letter in WUBRG_ORDER:
            hits.add(letter)
    return hits


def _build_print_map(cards: Sequence[Any], prints_map: Optional[Dict[int, dict]] = None) -> Dict[int, dict]:
    """Resolve Scryfall prints for each card, reusing the provided map when present."""
    if prints_map:
        return dict(prints_map)

    ensure_cache_loaded()
    out: Dict[int, dict] = {}
    by_oracle: Dict[str, dict] = {}
    by_key: Dict[Tuple[str, str], dict] = {}

    for card in cards:
        cid = getattr(card, "id", None)
        if cid is None:
            continue
        scode = getattr(card, "set_code", None)
        cn = getattr(card, "collector_number", None)
        name = getattr(card, "name", None)
        oracle_id = getattr(card, "oracle_id", None)

        pr: dict = {}
        key = (str(scode or "").lower(), str(cn or "").lower())
        if key in by_key:
            pr = by_key[key]
        else:
            try:
                pr = find_by_set_cn(scode, cn, name) or {}
            except Exception:
                pr = {}
            if pr:
                by_key[key] = pr

        if not pr and oracle_id:
            if oracle_id in by_oracle:
                pr = by_oracle[oracle_id]
            else:
                try:
                    prints = prints_for_oracle(oracle_id) or []
                    pr = prints[0] if prints else {}
                except Exception:
                    pr = {}
                if pr:
                    by_oracle[oracle_id] = pr

        out[cid] = pr or {}
    return out


def _profile_for_card(card: Any, print_obj: Dict[str, Any]) -> CardProfile:
    oracle_text = str(getattr(card, "oracle_text", "") or _oracle_text(print_obj) or "")
    type_line = str(getattr(card, "type_line", "") or _type_line(print_obj) or "")
    color_identity = _color_identity_for_card(card, print_obj)
    mv = _mana_value(print_obj)
    themes = detect_themes_for_text(oracle_text, type_line)
    tribes = _tribes_from_type_line(type_line)
    produced = _produced_colors_from_text(oracle_text)
    quantity = int(getattr(card, "quantity", 1) or 1)
    roles = get_roles_for_card(
        {
            "name": getattr(card, "name", ""),
            "type_line": type_line,
            "oracle_text": oracle_text,
        }
    )
    img = _image_for_card(print_obj)
    return CardProfile(
        card=card,
        type_line=type_line,
        oracle_text=oracle_text,
        color_identity=color_identity,
        mana_value=mv,
        roles=roles,
        themes=themes,
        tribes=tribes,
        produced_colors=produced,
        image_url=img,
        quantity=quantity,
    )


def _is_commander_candidate(profile: CardProfile, print_obj: Dict[str, Any]) -> bool:
    tl = profile.type_line.lower()
    text = profile.oracle_text.lower()
    border = (print_obj.get("border_color") or "").lower()
    set_type = (print_obj.get("set_type") or "").lower()

    if border == "silver" or set_type == "funny":
        return False
    if "token" in tl or set_type == "token":
        return False

    if "legendary creature" in tl:
        return True
    if "can be your commander" in text:
        return True
    if "legendary enchantment" in tl and "background" in tl:
        return True
    if "planeswalker" in tl and "can be your commander" in text:
        return True
    if "choose a background" in text or "partner" in text:
        return True
    return False


def _matches_commander_colors(card_colors: Iterable[str], commander_colors: Set[str]) -> bool:
    colors = {c.upper() for c in card_colors if c}
    if not colors:
        return True
    return colors.issubset(commander_colors or set())


def _build_collection_profile(cards: Sequence[Any], prints_map: Dict[int, dict]) -> CollectionProfile:
    color_support: Counter = Counter()
    role_counts: Counter = Counter()
    theme_counts: Counter = Counter()
    mana_curve: List[float] = []
    profiles: List[CardProfile] = []

    for card in cards:
        pr = prints_map.get(getattr(card, "id", None), {})
        profile = _profile_for_card(card, pr)
        profiles.append(profile)

        qty = max(profile.quantity, 1)
        for c in profile.color_identity:
            color_support[c] += qty

        is_land = "land" in profile.type_line.lower()
        is_rock = "artifact" in profile.type_line.lower() and ("add " in profile.oracle_text.lower())
        if is_land or is_rock:
            for c in profile.produced_colors:
                color_support[c] += qty * (2 if is_land else 1)

        for role in profile.roles:
            role_counts[role] += qty
        for theme in profile.themes:
            theme_counts[theme] += qty
        if profile.mana_value is not None:
            mana_curve.extend([profile.mana_value] * qty)

    return CollectionProfile(
        cards=profiles,
        color_support=color_support,
        role_counts=role_counts,
        theme_counts=theme_counts,
        mana_curve=mana_curve,
        prints_map=prints_map,
    )


def _score_synergy(commander: CardProfile, eligible_cards: Sequence[CardProfile]) -> Tuple[float, List[Dict[str, Any]], Set[str]]:
    synergy_hits = 0.0
    synergy_cards: List[Tuple[float, CardProfile, str]] = []
    commander_text = commander.oracle_text.lower()
    commander_tribes = {t.lower() for t in commander.tribes}
    commander_themes = {t.lower() for t in commander.themes}
    commander_colors = set(commander.color_identity)

    for profile in eligible_cards:
        reason_parts: List[str] = []
        weight = 0.0
        lower_text = profile.oracle_text.lower()
        lower_type = profile.type_line.lower()

        # Tribal hooks
        if commander_tribes and commander_tribes.intersection({t.lower() for t in profile.tribes}):
            weight += 2.5
            reason_parts.append("Tribal synergy")

        # Mechanic matches
        for key, kws in MECHANIC_KEYWORDS.items():
            if key == "dragons" and "dragon" not in commander_text:
                continue
            for kw in kws:
                if kw in commander_text and kw in lower_text:
                    weight += 1.2
                    reason_parts.append(key.title())
                    break

        # Theme overlaps
        if commander_themes and commander_themes.intersection({t.lower() for t in profile.themes}):
            weight += 1.5
            reason_parts.append("Theme match")

        # Role support hints
        if {"ramp", "draw", "removal", "recursion", "protection", "stax"}.intersection(
            {r.lower() for r in profile.roles}
        ):
            weight += 0.4

        if weight > 0:
            synergy_hits += weight
            synergy_cards.append((weight, profile, "; ".join(reason_parts) or "Supports gameplan"))

    synergy_cards.sort(key=lambda tup: tup[0], reverse=True)
    top_cards = [
        {
            "name": getattr(p.card, "name", "Card"),
            "image": p.image_url,
            "roles": p.roles,
            "reason": reason,
        }
        for weight, p, reason in synergy_cards[:10]
    ]

    max_possible = max(1.0, len(eligible_cards) * 1.2)
    score = min(40.0, (synergy_hits / max_possible) * 40.0)
    return score, top_cards, commander_themes


def _score_color_support(commander: CardProfile, color_support: Counter) -> float:
    if not commander.color_identity:
        return 18.0  # colorless commanders are easier to support
    scores = []
    for c in commander.color_identity:
        support = color_support.get(c, 0)
        target = 18 if len(commander.color_identity) >= 3 else 12
        scores.append(min(1.0, support / float(target or 1)))
    if not scores:
        return 8.0
    return min(20.0, (sum(scores) / len(scores)) * 20.0)


def _score_roles(
    eligible_cards: Sequence[CardProfile],
) -> Tuple[float, Dict[str, float], List[str]]:
    buckets = {
        "ramp": {"ramp"},
        "draw": {"draw"},
        "removal": {"removal"},
        "interaction": {"counterspells", "protection", "stax"},
        "recursion": {"recursion"},
        "wincons": {"finisher", "combat"},
        "synergy": {"tokens", "lifegain", "utility", "sacrifice outlet"},
    }
    counts: Dict[str, int] = defaultdict(int)
    warnings: List[str] = []

    for profile in eligible_cards:
        role_set = {r.lower() for r in profile.roles}
        for bucket, needle in buckets.items():
            if role_set.intersection(needle):
                counts[bucket] += profile.quantity

    strengths: Dict[str, float] = {}
    total_score = 0.0
    for bucket, total in counts.items():
        target = 8 if bucket in {"ramp", "draw", "removal"} else 6
        val = min(1.0, total / float(target or 1))
        strengths[bucket] = round(val * 10, 2)
        total_score += val

    # Normalize across buckets to a 0–20 scale
    if buckets:
        normalized = (total_score / len(buckets)) * 20.0
    else:
        normalized = 0.0

    if strengths.get("draw", 0) < 4:
        warnings.append("Insufficient card draw")
    if strengths.get("removal", 0) < 4:
        warnings.append("Low removal support")
    if strengths.get("ramp", 0) < 4:
        warnings.append("Weak mana acceleration")

    return min(20.0, normalized), strengths, warnings


def _score_themes(
    commander: CardProfile, eligible_cards: Sequence[CardProfile], commander_themes: Set[str]
) -> Tuple[float, List[str]]:
    if not commander_themes:
        commander_themes = commander.themes
    theme_hits: Counter = Counter()

    for profile in eligible_cards:
        for theme in profile.themes:
            if commander_themes and theme.lower() in {t.lower() for t in commander_themes}:
                theme_hits[theme] += profile.quantity
            elif not commander_themes and theme in THEME_KEYS:
                theme_hits[theme] += profile.quantity

    if not theme_hits:
        return 4.0, []

    strongest = theme_hits.most_common(4)
    scores = []
    for _, count in strongest:
        scores.append(min(1.0, count / 12.0))
    theme_score = (sum(scores) / max(1, len(scores))) * 10.0
    dominant = [t for t, _ in strongest]
    return min(10.0, theme_score), dominant


def _score_curve(eligible_cards: Sequence[CardProfile]) -> float:
    if not eligible_cards:
        return 6.0
    lows = meds = highs = 0
    for profile in eligible_cards:
        mv = profile.mana_value
        if mv is None:
            continue
        if mv <= 2.0:
            lows += profile.quantity
        elif mv <= 4.0:
            meds += profile.quantity
        else:
            highs += profile.quantity
    total = lows + meds + highs
    if total == 0:
        return 6.0
    low_ratio = lows / total
    med_ratio = meds / total
    balance = (min(low_ratio / 0.25, 1.2) + min(med_ratio / 0.45, 1.1)) / 2.0
    efficiency = min(1.0, (lows + meds) / max(1.0, highs + meds))
    score = ((balance + efficiency) / 2.0) * 10.0
    return max(0.0, min(10.0, score))


def compute_commander_score(commander: CardProfile, collection: CollectionProfile) -> Dict[str, Any]:
    commander_colors = set(commander.color_identity)
    eligible_cards = [
        c for c in collection.cards if _matches_commander_colors(c.color_identity, commander_colors)
    ]

    sy_score, synergy_cards, commander_themes = _score_synergy(commander, eligible_cards)
    ci_score = _score_color_support(commander, collection.color_support)
    role_score, role_strengths, warnings = _score_roles(eligible_cards)
    theme_score, dominant_themes = _score_themes(commander, eligible_cards, commander_themes)
    curve_score = _score_curve(eligible_cards)

    total = round(
        sy_score * 0.40
        + ci_score * 0.20
        + role_score * 0.20
        + theme_score * 0.10
        + curve_score * 0.10,
        2,
    )

    warnings = list(dict.fromkeys(warnings))  # de-dupe while preserving order

    return {
        "card": commander.card,
        "card_id": getattr(commander.card, "id", None),
        "name": getattr(commander.card, "name", "Commander"),
        "type_line": commander.type_line,
        "oracle_text": commander.oracle_text,
        "image_url": commander.image_url,
        "color_identity": commander.color_identity,
        "synergy_score": float(total),
        "component_scores": {
            "synergy": round(sy_score, 2),
            "color_identity": round(ci_score, 2),
            "roles": round(role_score, 2),
            "themes": round(theme_score, 2),
            "curve": round(curve_score, 2),
        },
        "role_strengths": role_strengths,
        "dominant_themes": dominant_themes or list(commander_themes) or [],
        "top_synergy_cards": synergy_cards,
        "warnings": warnings,
        "themes": list(commander_themes),
        "supporting_cards": [c["name"] for c in synergy_cards],
        "color_support": commander.color_identity,
        "role_labels": list(role_strengths.keys()),
    }


def recommend_commanders(user_cards: Sequence[Any], prints_map: Optional[Dict[int, dict]] = None) -> List[Dict[str, Any]]:
    """
    Build commander recommendations from the user's owned cards.
    Returns a list of payload dicts sorted by synergy score (desc).
    """
    cards = list(user_cards or [])
    if not cards:
        return []

    pm = _build_print_map(cards, prints_map)
    collection_profile = _build_collection_profile(cards, pm)

    commanders: List[Dict[str, Any]] = []
    seen_keys: Set[Tuple[str, str]] = set()

    for profile in collection_profile.cards:
        pr = pm.get(getattr(profile.card, "id", None), {})
        if not _is_commander_candidate(profile, pr):
            continue
        key = ((getattr(profile.card, "oracle_id", "") or "").lower(), (profile.card.name or "").lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)

        commander_payload = compute_commander_score(profile, collection_profile)
        commanders.append(commander_payload)

    commanders.sort(key=lambda c: c.get("synergy_score", 0), reverse=True)
    return commanders
def _basic_for_color(color: str) -> str:
    mapping = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
    return mapping.get(color.upper(), "Wastes")


def recommend_deck_for_commander(
    commander_payload: dict,
    user_cards: Sequence[Any],
    prints_map: Optional[Dict[int, dict]] = None,
    deck_size: int = 100,
) -> List[Dict[str, Any]]:
    """
    Build a 100-card suggestion (commander + 99) from the user's collection for a given commander payload.
    Returns a list of card entries with quantities and print metadata.
    """
    cards = list(user_cards or [])
    if not cards or not commander_payload:
        return []

    pm = _build_print_map(cards, prints_map)
    collection_profile = _build_collection_profile(cards, pm)

    commander_name = (commander_payload.get("name") or "").strip().lower()
    commander_profile = None
    commander_print = {}
    for card in cards:
        if (card.name or "").strip().lower() == commander_name:
            commander_print = pm.get(getattr(card, "id", None), {}) or {}
            commander_profile = _profile_for_card(card, commander_print)
            break
    if commander_profile is None:
        return []

    commander_colors = set(commander_profile.color_identity)
    commander_text = commander_profile.oracle_text.lower()
    commander_themes = commander_profile.themes

    eligible_cards: List[CardProfile] = []
    for profile in collection_profile.cards:
        if profile.card is commander_profile.card:
            continue
        if not _matches_commander_colors(profile.color_identity, commander_colors):
            continue
        eligible_cards.append(profile)

    def _synergy_weight(profile: CardProfile) -> float:
        weight = 0.0
        text = profile.oracle_text.lower()
        if commander_themes and commander_themes.intersection(profile.themes):
            weight += 1.5
        for key, kws in MECHANIC_KEYWORDS.items():
            for kw in kws:
                if kw in commander_text and kw in text:
                    weight += 1.1
                    break
        if commander_profile.tribes and commander_profile.tribes.intersection(profile.tribes):
            weight += 2.0
        core_roles = {"ramp", "draw", "removal", "interaction", "recursion"}
        if core_roles.intersection({r.lower() for r in profile.roles}):
            weight += 0.6
        if "land" in profile.type_line.lower():
            weight += 0.2
        return weight

    nonlands: List[tuple[float, CardProfile]] = []
    lands: List[tuple[float, CardProfile]] = []
    for profile in eligible_cards:
        wt = _synergy_weight(profile)
        if "land" in profile.type_line.lower():
            lands.append((wt, profile))
        else:
            nonlands.append((wt, profile))

    # Land target based on colors
    color_count = max(1, len(commander_colors))
    land_target = 37 + (1 if color_count >= 3 else 0) - (2 if color_count == 1 else 0)
    nonland_target = max(0, deck_size - 1 - land_target)  # commander counts as 1

    # Sort lands by produced color coverage then weight
    def _land_score(profile: CardProfile) -> float:
        coverage = len(commander_colors.intersection(profile.produced_colors))
        return coverage + profile.quantity * 0.01  # minor tie-breaker

    lands_sorted = sorted(lands, key=lambda tup: (_land_score(tup[1]), tup[0]), reverse=True)
    nonlands_sorted = sorted(nonlands, key=lambda tup: (tup[0], -(tup[1].mana_value or 0)), reverse=True)

    def _is_basic_name(name: str, type_line: str) -> bool:
        if not name:
            return False
        lower_tl = (type_line or "").lower()
        if "basic land" in lower_tl:
            return True
        return name.strip() in BASIC_LANDS

    def _card_entry(profile: CardProfile, qty: int) -> Dict[str, Any]:
        src = profile.card
        is_basic = _is_basic_name(getattr(src, "name", ""), profile.type_line)
        return {
            "name": getattr(src, "name", ""),
            "set_code": getattr(src, "set_code", "") or (pm.get(getattr(src, "id", None), {}) or {}).get("set", ""),
            "collector_number": getattr(src, "collector_number", "") or (pm.get(getattr(src, "id", None), {}) or {}).get("collector_number", ""),
            "lang": getattr(src, "lang", "en"),
            "oracle_id": getattr(src, "oracle_id", None),
            "quantity": max(qty, 1),
            "is_basic": is_basic,
        }

    deck_entries: List[Dict[str, Any]] = []
    seen_nonbasic: Set[str] = set()

    # Lands first
    remaining_lands = land_target
    for wt, profile in lands_sorted:
        if remaining_lands <= 0:
            break
        is_basic = _is_basic_name(profile.card.name, profile.type_line)
        name_key = (profile.card.name or "").strip().lower()
        if not is_basic and name_key in seen_nonbasic:
            continue
        use_qty = min(profile.quantity if is_basic else 1, remaining_lands)
        if use_qty <= 0:
            continue
        deck_entries.append(_card_entry(profile, use_qty))
        remaining_lands -= use_qty
        if not is_basic and name_key:
            seen_nonbasic.add(name_key)

    # Fill missing lands with basics matching colors
    while remaining_lands > 0:
        for color in commander_colors or {"C"}:
            if remaining_lands <= 0:
                break
            basic_name = _basic_for_color(color)
            deck_entries.append(
                {
                    "name": basic_name,
                    "set_code": "BAS",
                    "collector_number": basic_name,
                    "lang": "en",
                    "oracle_id": None,
                    "quantity": 1,
                    "is_basic": True,
                }
            )
            remaining_lands -= 1

    # Nonlands
    remaining_nonlands = nonland_target
    for wt, profile in nonlands_sorted:
        if remaining_nonlands <= 0:
            break
        is_basic = _is_basic_name(profile.card.name, profile.type_line)
        name_key = (profile.card.name or "").strip().lower()
        if not is_basic and name_key in seen_nonbasic:
            continue
        qty = min(profile.quantity if is_basic else 1, remaining_nonlands)
        if qty <= 0:
            continue
        deck_entries.append(_card_entry(profile, qty))
        remaining_nonlands -= qty
        if not is_basic and name_key:
            seen_nonbasic.add(name_key)

    # If still short, top off with basics
    basic_cycle = list(commander_colors) or ["C"]
    basic_idx = 0
    while remaining_nonlands > 0:
        color = basic_cycle[basic_idx % len(basic_cycle)]
        basic_idx += 1
        deck_entries.append(
            {
                "name": _basic_for_color(color),
                "set_code": "BAS",
                "collector_number": "001",
                "lang": "en",
                "oracle_id": None,
                "quantity": 1,
                "is_basic": True,
            }
        )
        remaining_nonlands -= 1

    # Commander entry first
    commander_entry = {
        "name": commander_payload.get("name"),
        "set_code": (commander_payload.get("set_code") or commander_print.get("set") or ""),
        "collector_number": commander_payload.get("collector_number") or commander_print.get("collector_number") or "",
        "lang": commander_payload.get("lang") or "en",
        "oracle_id": commander_payload.get("oracle_id") or commander_print.get("oracle_id"),
        "quantity": 1,
        "is_basic": False,
    }
    final_list = [commander_entry] + deck_entries

    def _total_cards(entries: List[Dict[str, Any]]) -> int:
        return sum(int(item.get("quantity") or 0) for item in entries)

    total = _total_cards(final_list)
    # Trim extras (prefer trimming from the end, decrementing quantity where possible)
    idx = len(final_list) - 1
    while total > deck_size and idx >= 1:  # keep commander intact at index 0
        entry = final_list[idx]
        qty = int(entry.get("quantity") or 0)
        if qty > 1:
            entry["quantity"] = qty - 1
        else:
            final_list.pop(idx)
        total = _total_cards(final_list)
        idx -= 1

    # Fill shortfalls with basics in commander colors
    basic_cycle = list(commander_colors) or ["C"]
    basic_idx = 0
    while _total_cards(final_list) < deck_size:
        color = basic_cycle[basic_idx % len(basic_cycle)]
        basic_idx += 1
        final_list.append(
            {
                "name": _basic_for_color(color),
                "set_code": "BAS",
                "collector_number": "001",
                "lang": "en",
                "oracle_id": None,
                "quantity": 1,
                "is_basic": True,
            }
        )

    return final_list
