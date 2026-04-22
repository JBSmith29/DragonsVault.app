"""Commander Spellbook dataset loading and combo matching helpers."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.domains.cards.services import scryfall_cache as sc

__all__ = [
    "SPELLBOOK_COMBO_GROUPS",
    "SPELLBOOK_EARLY_COMBOS",
    "SPELLBOOK_EARLY_INDEX",
    "SPELLBOOK_LATE_COMBOS",
    "SPELLBOOK_LATE_INDEX",
    "SPELLBOOK_RESULT_LABELS",
    "SpellbookCombo",
    "combo_piece_count",
    "find_spellbook_matches",
    "format_spellbook_combo_descriptor",
    "infinity_badge",
    "is_three_card_combo",
    "normalize_card_key",
    "reload_spellbook_combos",
    "spellbook_dataset_epoch",
]


SPELLBOOK_COMBO_GROUPS: Tuple[Tuple[str, str], ...] = (
    ("infinite_mana", "Infinite Mana"),
    ("infinite_tokens", "Infinite Tokens"),
    ("infinite_life_gain", "Infinite Life Gain"),
    ("infinite_life_loss", "Infinite Life Loss"),
    ("infinite_damage", "Infinite Damage"),
    ("infinite_turns", "Infinite Turns"),
    ("infinite_draw", "Infinite Card Draw"),
)

INFINITY_ICON = "♾️"
_CARD_KEY_PATTERN = re.compile(r"[^a-z0-9]+")
_BACKEND_ROOT = Path(__file__).resolve().parents[4]
_REPO_ROOT = _BACKEND_ROOT.parent
_LIFE_GAIN_TERMS: Tuple[str, ...] = ("lifegain", "life gain", "gain life", "gains life", "gaining life")
_LIFE_LOSS_TERMS: Tuple[str, ...] = (
    "lifeloss",
    "life loss",
    "lose life",
    "loses life",
    "loss of life",
    "life drain",
    "lifedrain",
    "drain life",
)


def infinity_badge(label: str) -> str:
    prefix = "Infinite "
    if label.startswith(prefix):
        label = label[len(prefix):]
    return f"{INFINITY_ICON} {label}"


def normalize_card_key(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _CARD_KEY_PATTERN.sub("", text)
    return text or name.strip().lower()


@dataclass(frozen=True)
class SpellbookCombo:
    id: str
    cards: Tuple[str, ...]
    requirements: Dict[str, int]
    mana_value_needed: Optional[int]
    mana_needed: Optional[str]
    results: Tuple[str, ...]
    result_categories: Tuple[str, ...]
    bracket_tag: Optional[str]
    url: Optional[str]
    identity: str
    category: str


SPELLBOOK_RESULT_LABELS: Dict[str, str | None] = {
    "instant_win": "instant win",
    "infinite_mana": "infinite mana",
    "infinite_life": None,
    "infinite_life_gain": "infinite life gain",
    "infinite_life_loss": "infinite life loss",
    "infinite_damage": "infinite damage",
    "infinite_draw": "infinite card draw",
    "infinite_tokens": "infinite tokens",
    "infinite_turns": "infinite turns",
}


def _spellbook_data_candidates() -> List[Path]:
    candidates: List[Path] = []
    try:
        data_root = Path(sc.default_cards_path()).parent
        candidates.append(data_root / "spellbook_combos.json")
    except Exception:
        pass
    root_data = Path(os.getenv("SCRYFALL_DATA_DIR", "data")) / "spellbook_combos.json"
    if root_data not in candidates:
        candidates.append(root_data)
    legacy = _REPO_ROOT / "data" / "spellbook_combos.json"
    if legacy not in candidates:
        candidates.append(legacy)
    return candidates


def _spellbook_data_path() -> Path:
    candidates = _spellbook_data_candidates()
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


SPELLBOOK_DATA_PATH = _spellbook_data_path()


def spellbook_dataset_epoch() -> int:
    global SPELLBOOK_DATA_PATH
    data_path = _spellbook_data_path()
    SPELLBOOK_DATA_PATH = data_path
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
        fetched_at = payload.get("fetched_at")
        if fetched_at:
            return hash(fetched_at)
    except Exception:
        pass
    try:
        return int(data_path.stat().st_mtime)
    except FileNotFoundError:
        return 0


def _build_spellbook_combo_collection(
    entries: List[Dict[str, Any]],
    category: str,
) -> Tuple[List[SpellbookCombo], Dict[str, Tuple[SpellbookCombo, ...]]]:
    combos: List[SpellbookCombo] = []
    index: Dict[str, List[SpellbookCombo]] = defaultdict(list)

    for entry in entries:
        combo_id = str(entry.get("id") or "").strip()
        if not combo_id:
            continue

        requirements: Dict[str, int] = {}
        source_reqs = entry.get("requirements") or entry.get("cards") or []
        for item in source_reqs:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            qty = int(item.get("quantity") or 1)
            key = normalize_card_key(name)
            if not key:
                continue
            requirements[key] = requirements.get(key, 0) + qty
        if not requirements:
            continue

        cards = tuple(card.get("name") for card in entry.get("cards", []) if card.get("name"))
        raw_categories = {
            str(tag).strip()
            for tag in entry.get("result_categories", [])
            if isinstance(tag, str) and tag.strip()
        }
        if "infinite_life" in raw_categories:
            raw_categories.update({"infinite_life_gain", "infinite_life_loss"})

        text_chunks = []
        for field in ("results", "produces"):
            for item in entry.get(field, []):
                if isinstance(item, str):
                    text_chunks.append(item)
        for chunk in text_chunks:
            lowered = chunk.casefold()
            if "infinite" in lowered:
                if any(term in lowered for term in _LIFE_GAIN_TERMS):
                    raw_categories.add("infinite_life_gain")
                if any(term in lowered for term in _LIFE_LOSS_TERMS):
                    raw_categories.add("infinite_life_loss")

        identity_value = "".join(
            ch for ch in str(entry.get("identity") or "").strip().upper() if ch in "WUBRGC"
        )
        combo = SpellbookCombo(
            id=combo_id,
            cards=cards,
            requirements=requirements,
            mana_value_needed=entry.get("mana_value_needed"),
            mana_needed=(entry.get("mana_needed") or None),
            results=tuple(entry.get("results", [])),
            result_categories=tuple(sorted(raw_categories)),
            bracket_tag=entry.get("bracket_tag"),
            url=entry.get("url"),
            identity=identity_value,
            category=category,
        )
        combos.append(combo)
        for name in requirements:
            index[name].append(combo)

    return combos, {key: tuple(value) for key, value in index.items()}


def _empty_combo_data() -> Dict[str, Any]:
    return {
        "early": [],
        "late": [],
        "early_index": {},
        "late_index": {},
    }


def _load_spellbook_combos() -> Dict[str, Any]:
    global SPELLBOOK_DATA_PATH
    data_path = _spellbook_data_path()
    SPELLBOOK_DATA_PATH = data_path
    if not data_path.exists():
        return _empty_combo_data()

    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_combo_data()

    early_combos, early_index = _build_spellbook_combo_collection(payload.get("early_game", []), "early")
    late_combos, late_index = _build_spellbook_combo_collection(payload.get("late_game", []), "late")
    return {
        "early": early_combos,
        "late": late_combos,
        "early_index": early_index,
        "late_index": late_index,
    }


_SPELLBOOK_COMBO_DATA = _load_spellbook_combos()
SPELLBOOK_EARLY_COMBOS: List[SpellbookCombo] = _SPELLBOOK_COMBO_DATA.get("early", [])
SPELLBOOK_LATE_COMBOS: List[SpellbookCombo] = _SPELLBOOK_COMBO_DATA.get("late", [])
SPELLBOOK_EARLY_INDEX: Dict[str, Tuple[SpellbookCombo, ...]] = _SPELLBOOK_COMBO_DATA.get("early_index", {})
SPELLBOOK_LATE_INDEX: Dict[str, Tuple[SpellbookCombo, ...]] = _SPELLBOOK_COMBO_DATA.get("late_index", {})


def reload_spellbook_combos() -> bool:
    global _SPELLBOOK_COMBO_DATA
    global SPELLBOOK_EARLY_COMBOS, SPELLBOOK_LATE_COMBOS
    global SPELLBOOK_EARLY_INDEX, SPELLBOOK_LATE_INDEX

    data = _load_spellbook_combos()
    has_payload = bool(data.get("early") or data.get("late"))
    if not has_payload and (SPELLBOOK_EARLY_COMBOS or SPELLBOOK_LATE_COMBOS):
        return False

    _SPELLBOOK_COMBO_DATA = data
    SPELLBOOK_EARLY_COMBOS = data.get("early", [])
    SPELLBOOK_LATE_COMBOS = data.get("late", [])
    SPELLBOOK_EARLY_INDEX = data.get("early_index", {})
    SPELLBOOK_LATE_INDEX = data.get("late_index", {})
    return True


def _combo_requirements_satisfied(combo: SpellbookCombo, deck_counts: Dict[str, int]) -> bool:
    if not combo.requirements:
        return False
    for req_name, req_qty in combo.requirements.items():
        if deck_counts.get(req_name, 0) < req_qty:
            return False
    return True


def find_spellbook_matches(
    deck_counts: Dict[str, int],
    lookup: Dict[str, Tuple[SpellbookCombo, ...]],
    seen_ids: Set[str],
) -> List[SpellbookCombo]:
    matches: List[SpellbookCombo] = []
    for card_name in deck_counts:
        combos = lookup.get(card_name)
        if not combos:
            continue
        for combo in combos:
            if combo.id in seen_ids:
                continue
            if _combo_requirements_satisfied(combo, deck_counts):
                matches.append(combo)
                seen_ids.add(combo.id)
    return matches


def combo_piece_count(combo: SpellbookCombo) -> int:
    total = 0
    for qty in combo.requirements.values():
        try:
            value = int(qty)
        except (TypeError, ValueError):
            value = 1
        total += max(1, value)
    return total


def is_three_card_combo(combo: SpellbookCombo) -> bool:
    return combo_piece_count(combo) >= 3


def format_spellbook_combo_descriptor(combo: SpellbookCombo) -> str:
    pieces = " + ".join(combo.cards) if combo.cards else combo.id
    category_label = combo.category.title() if combo.category else "Combo"

    def _label_for(tag: str) -> Optional[str]:
        marker = SPELLBOOK_RESULT_LABELS.get(tag, ...)
        if marker is None:
            return None
        if marker is ...:
            return tag.replace("_", " ")
        return marker

    result_bits = [lbl for tag in combo.result_categories if (lbl := _label_for(tag))]
    result_text = ", ".join(dict.fromkeys(result_bits)) if result_bits else "engine"
    return f"{category_label} - {result_text}: {pieces}"
