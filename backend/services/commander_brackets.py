from __future__ import annotations



import json
import os

import math

import re

from collections import Counter, defaultdict

from dataclasses import dataclass, field

from functools import lru_cache

from pathlib import Path

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from urllib.parse import quote_plus
import unicodedata



from services import scryfall_cache as sc
from services.core_role_logic import derive_core_roles
from services.scryfall_cache import ensure_cache_loaded, prints_for_oracle, unique_oracle_by_name



__all__ = [

    "evaluate_commander_bracket",

    "BRACKET_LABELS",

    "BRACKET_REFERENCE",

    "BRACKET_REFERENCE_BY_LEVEL",

    "reload_spellbook_combos",
    "SPELLBOOK_EARLY_COMBOS",
    "SPELLBOOK_LATE_COMBOS",
    "SPELLBOOK_RESULT_LABELS",
    "spellbook_dataset_epoch",
    "BRACKET_RULESET_EPOCH",

]



# ---------------------------------------------------------------------------

# Reference data sourced from Wizards' Commander Brackets beta announcement

# and commonly cited cEDH staple lists (e.g., cEDH Decklist Database card

# frequency snapshots published throughout 2024).

# ---------------------------------------------------------------------------



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


def _infinity_badge(label: str) -> str:
    prefix = "Infinite "
    if label.startswith(prefix):
        label = label[len(prefix):]
    return f"{INFINITY_ICON} {label}"

_CARD_KEY_PATTERN = re.compile(r"[^a-z0-9]+")


def _normalize_card_key(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _CARD_KEY_PATTERN.sub("", text)
    return text or name.strip().lower()

BRACKET_LABELS: Dict[int, str] = {

    1: "Exhibition",

    2: "Core",

    3: "Upgraded",

    4: "Optimized",

    5: "cEDH",

}



BRACKET_RULESET_EPOCH = 5

BRACKET_RULESET_PATH = Path(__file__).resolve().parents[1] / "commander-brackets" / "commander_brackets_ruleset.json"


@lru_cache(maxsize=1)
def _load_bracket_ruleset() -> Dict[str, Any]:
    try:
        with BRACKET_RULESET_PATH.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


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
    legacy = Path(__file__).resolve().parents[1] / "data" / "spellbook_combos.json"
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


GAME_CHANGERS: Set[str] = {
    # White
    "Drannith Magistrate",
    "Humility",
    "Serra's Sanctum",
    "Smothering Tithe",
    "Enlightened Tutor",
    "Teferi's Protection",
    # Blue
    "Consecrated Sphinx",
    "Cyclonic Rift",
    "Force of Will",
    "Fierce Guardianship",
    "Gifts Ungiven",
    "Intuition",
    "Mystical Tutor",
    "Narset, Parter of Veils",
    "Rhystic Study",
    "Thassa's Oracle",
    # Black
    "Ad Nauseam",
    "Bolas's Citadel",
    "Braids, Cabal Minion",
    "Demonic Tutor",
    "Imperial Seal",
    "Necropotence",
    "Opposition Agent",
    "Orcish Bowmasters",
    "Tergrid, God of Fright",
    "Vampiric Tutor",
    # Red
    "Gamble",
    "Jeska's Will",
    "Underworld Breach",
    # Green
    "Crop Rotation",
    "Gaea's Cradle",
    "Natural Order",
    "Seedborn Muse",
    "Survival of the Fittest",
    "Worldly Tutor",
    # Multicolor
    "Aura Shards",
    "Coalition Victory",
    "Grand Arbiter Augustin IV",
    "Notion Thief",
    # Colorless
    "Ancient Tomb",
    "Chrome Mox",
    "Field of the Dead",
    "Glacial Chasm",
    "Grim Monolith",
    "Lion's Eye Diamond",
    "Mana Vault",
    "Mishra's Workshop",
    "Mox Diamond",
    "Panoptic Mirror",
    "The One Ring",
    "The Tabernacle at Pendrell Vale",
}




# Land denial / destruction or heavy mana suppression pieces typically cited

# alongside the Brackets article guidelines.

MASS_LAND_DENIAL_NAMES: Set[str] = {

    "Armageddon",

    "Ravages of War",

    "Catastrophe",

    "Jokulhaups",

    "Obliterate",

    "Decree of Annihilation",

    "Boom",

    "Bust",

    "Impending Disaster",

    "Devastation",

    "Worldfire",

    "Apocalypse",

    "Fall of the Thran",

    "Wake of Destruction",

    "Ruination",

    "From the Ashes",

    "Desolation Angel",

    "Sunder",

    "Keldon Firebombers",

    "Tectonic Break",

    "Burning of Xinye",

    "Wildfire",

    "Destructive Force",

    "Obliterate",

    "Winter Orb",

    "Static Orb",

    "Hokori, Dust Drinker",

    "Rising Waters",

    "Mana Breach",

    "Sunder",

    "Wave of Vitriol",

    "Price of Glory",

}



MASS_LAND_DENIAL_PATTERNS: Sequence[re.Pattern[str]] = [

    re.compile(r"destroy all lands", re.IGNORECASE),

    re.compile(r"each player sacrifices [^.!?]*land", re.IGNORECASE),

    re.compile(r"lands? (?:you|they|players?) control (?:don'?t|can't) untap", re.IGNORECASE),

    re.compile(r"players? can't play lands", re.IGNORECASE),

]



# cEDH staple highlights (non-exhaustive) cross-referenced from 2024 cEDH

# Decklist Database card prevalence exports & major primer coverage.

CEDH_SIGNATURES: Set[str] = {

    "Dockside Extortionist",

    "Demonic Consultation",

    "Tainted Pact",

    "Doomsday",

    "Gilded Drake",

    "Isochron Scepter",

    "Dramatic Reversal",

    "Silence",

    "Ranger-Captain of Eos",

    "Grand Abolisher",

    "Deflecting Swat",

    "Deadly Rollick",

    "Flusterstorm",

    "Swan Song",

    "Mystic Remora",

    "Carpet of Flowers",

    "Mana Crypt",

    "Jeweled Lotus",

    "Mox Opal",

    "Mox Amber",

    "Jeweled Amulet",

    "Lotus Petal",

    "Lion's Eye Diamond",

    "Mana Vault",

    "Chrome Mox",

    "Mox Diamond",

    "Ad Nauseam",

    "Underworld Breach",

    "Yawgmoth's Will",

    "Necropotence",

    "Notion Thief",

    "Narset, Parter of Veils",

    "Teferi, Time Raveler",

    "Timetwister",

    "Wheel of Fortune",

    "Windfall",

    "Protean Hulk",

    "Food Chain",

    "Sensei's Divining Top",

    "Spellseeker",

    "Dauthi Voidwalker",

    "Opposition Agent",

    "Toxic Deluge",

    "Force of Negation",

    "Gitaxian Probe",

    "Reanimate",

    "Troll of Khazad-dum",  # fast reanimation land cycler

    "Intuition",

}



CEDH_COMMANDERS: Set[str] = {

    "Tymna the Weaver",

    "Thrasios, Triton Hero",

    "Kinnan, Bonder Prodigy",

    "Najeela, the Blade-Blossom",

    "Winota, Joiner of Forces",

    "Rograkh, Son of Rohgahh",

    "Silas Renn, Seeker Adept",

    "Kraum, Ludevic's Opus",

    "Malcolm, Keen-Eyed Navigator",

    "Tivit, Seller of Secrets",

    "Korvold, Fae-Cursed King",

    "Urza, Lord High Artificer",

    "Tevesh Szat, Doom of Fools",

    "Kenrith, the Returned King",

    "Zur the Enchanter",

    "Shorikai, Genesis Engine",

    "Atraxa, Grand Unifier",

    "Yuriko, the Tiger's Shadow",

    "Grand Arbiter Augustin IV",

    "Jetmir, Nexus of Revels",

}



EXTRA_TURN_PATTERN = re.compile(r"\bextra turn\b", re.IGNORECASE)

TUTOR_PATTERN = re.compile(r"search your library", re.IGNORECASE)

LAND_TUTOR_PATTERN = re.compile(r"\b(?:basic )?land card\b", re.IGNORECASE)

LAND_TUTOR_EXT_PATTERN = re.compile(

    r"(?:plains|island|swamp|mountain|forest|gate|desert|locus|cave|urza's|mine|power-plant|tower)\s+card",

    re.IGNORECASE,

)

ADDS_MANA_PATTERN = re.compile(r"\badd\s+\{", re.IGNORECASE)

INSTANT_WIN_PATTERN = re.compile(r"you win the game|lose the game", re.IGNORECASE)





@dataclass

class BracketCard:

    name: str

    type_line: str = ""

    oracle_text: str = ""

    mana_value: Optional[float] = None

    quantity: int = 1

    mana_cost: Optional[str] = None

    produced_mana: Sequence[str] | None = None

    is_game_changer: bool = False





@dataclass

class MetricBucket:

    key: str

    entries: List[Tuple[str, int]] = field(default_factory=list)



    def add(self, name: str, qty: int) -> None:

        if qty <= 0:

            return

        self.entries.append((name, qty))



    @property

    def count(self) -> int:

        return sum(qty for _, qty in self.entries)



    @property

    def names(self) -> List[str]:

        return sorted({name for name, _ in self.entries})





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





SPELLBOOK_RESULT_LABELS: Dict[str, str] = {
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



# Each curve maps a signal count to a score contribution using a

# piecewise-linear approximation tuned against Commander Brackets guidance.

SCORE_CURVES = {

    "game_changers": [

        (0, 0.0),

        (1, 2.3),

        (2, 4.2),

        (3, 5.8),

        (4, 7.1),

        (5, 8.2),

        (6, 9.0),

        (8, 10.0),

    ],

    "extra_turns": [

        (0, 0.0),

        (1, 2.0),

        (2, 3.5),

        (3, 5.0),

        (4, 6.6),

        (5, 8.0),

        (6, 9.0),

        (8, 10.0),

    ],

    "mass_land": [

        (0, 0.0),

        (1, 6.2),

        (2, 8.6),

        (3, 10.0),

    ],

    "zero_cmc_mana": [

        (0, 0.0),

        (1, 2.5),

        (2, 4.5),

        (3, 6.4),

        (4, 7.8),

        (5, 8.9),

        (6, 9.6),

    ],

    "cedh_signatures": [

        (0, 0.0),

        (1, 2.2),

        (2, 4.2),

        (3, 6.2),

        (4, 7.8),

        (5, 9.0),

        (6, 9.8),

    ],

    "instant_win": [

        (0, 0.0),

        (1, 3.4),

        (2, 5.8),

        (3, 7.8),

        (4, 9.2),

    ],

    "spellbook_combos": [

        (0, 0.0),

        (1, 2.4),

        (2, 4.0),

        (3, 5.6),

        (4, 7.1),

        (5, 8.2),

        (6, 9.0),

        (8, 10.0),

    ],

}



AVG_CMC_BENEFITS = [

    (2.0, 3.5),

    (2.25, 2.5),

    (2.5, 1.5),

    (2.8, 0.5),

]



AVG_CMC_PENALTIES = [

    (3.8, -0.5),

    (4.1, -1.0),

]





def _score_piecewise(value: float, curve: Sequence[Tuple[float, float]]) -> float:
    """Piecewise linear interpolation used for scoring curves."""
    if not curve:
        return 0.0
    ordered = sorted(curve, key=lambda item: item[0])
    value = float(value)

    threshold, score = ordered[0]

    if value <= threshold:

        return float(score)

    prev_thr, prev_val = float(threshold), float(score)

    for thr, val in ordered[1:]:

        thr = float(thr)

        val = float(val)

        if value <= thr:

            span = thr - prev_thr

            if span <= 0:

                return val

            ratio = (value - prev_thr) / span

            return float(prev_val + ratio * (val - prev_val))

        prev_thr, prev_val = thr, val

    if len(ordered) == 1:

        return float(ordered[0][1])

    last_thr, last_val = map(float, ordered[-1])

    prev_thr, prev_val = map(float, ordered[-2])

    if last_thr == prev_thr:

        return last_val

    slope = (last_val - prev_val) / (last_thr - prev_thr)
    return float(last_val + slope * (value - last_thr))


def _score_to_band(score: float) -> int:
    for threshold, level in SCORE_BANDS:
        if score <= threshold:
            return level
    return SCORE_BANDS[-1][1]

@dataclass(frozen=True)
class ScoreSignal:
    key: str
    label: str
    description: str
    curve: Tuple[Tuple[float, float], ...]
    guidance: Tuple[str, ...] = ()
    show_if_zero: bool = True


# Each signal uses a diminishing-returns curve tuned against Commander Brackets guidance.
SCORE_SIGNALS: Tuple[ScoreSignal, ...] = (
    ScoreSignal(
        key="game_changers",
        label="Game Changers list cards",
        description="Cards highlighted in Wizards' Game Changers list or flagged staples that warp casual tables.",
        curve=(
            (0, 0.0),
            (1, 2.3),
            (2, 4.2),
            (3, 5.8),
            (4, 7.1),
            (5, 8.2),
            (6, 9.0),
            (8, 10.0),
        ),
        guidance=(
            "0-1: Typical for precon or theme decks",
            "2-3: Signals optimized casual intentions",
            "4+: Usually indicates high-power or cEDH pace",
        ),
    ),
    ScoreSignal(
        key="extra_turns",
        label="Extra turn effects",
        description="Repeatable or chained extra turns compress the game clock and demand resilient answers.",
        curve=(
            (0, 0.0),
            (1, 2.0),
            (2, 3.5),
            (3, 5.0),
            (4, 6.6),
            (5, 8.0),
            (6, 9.0),
            (8, 10.0),
        ),
        guidance=(
            "1: Splashy finisher territory",
            "2-3: Deck leans on turn-lock patterns",
            "4+: Competitive-level time walking",
        ),
    ),
    ScoreSignal(
        key="mass_land",
        label="Mass land denial / lock pieces",
        description="Armageddon-style effects or hard locks that prevent lands from recovering.",
        curve=((0, 0.0), (1, 6.2), (2, 8.6), (3, 10.0)),
        guidance=(
            "0: Table-friendly for casual pods",
            "1: Immediately pushes table to higher brackets",
            "2+: Reserved for stax-heavy or tournament metas",
        ),
    ),
    ScoreSignal(
        key="zero_cmc_mana",
        label="Fast mana sources",
        description="Zero or near-zero cost mana rocks and rituals accelerate explosive openings.",
        curve=(
            (0, 0.0),
            (1, 2.5),
            (2, 4.5),
            (3, 6.4),
            (4, 7.8),
            (5, 8.9),
            (6, 9.6),
        ),
        guidance=(
            "0: Standard casual pacing",
            "1-2: Signals tuned openings",
            "3+: Typical for high-power and cEDH tables",
        ),
    ),
    ScoreSignal(
        key="cedh_signatures",
        label="cEDH signature cards",
        description="Staples tracked from cEDH deck databases that dramatically raise the ceiling.",
        curve=((0, 0.0), (1, 0.0)),
        guidance=(
            "0-1: Casual-focused shell",
            "2-3: High-powered upgrades present",
            "4+: Deck is leaning heavily into cEDH staples",
        ),
        show_if_zero=True,
    ),
    ScoreSignal(
        key="instant_win",
        label="Instant-win enablers",
        description="Single cards that immediately end the game once assembled (e.g., Oracle effects).",
        curve=((0, 0.0), (1, 2.2), (2, 4.2), (3, 6.5), (4, 8.5)),
        guidance=(
            "0: Requires multistep setups",
            "1: Has deterministic lines available",
            "2+: Multiple instant-win redundancies",
        ),
    ),
    ScoreSignal(
        key="spellbook_combos",
        label="Commander Spellbook combos",
        description="Detected combo lines from Commander Spellbook with bonuses for early or instant wins.",
        curve=(
            (0, 0.0),
            (1, 2.4),
            (2, 4.0),
            (3, 5.6),
            (4, 7.1),
            (5, 8.2),
            (6, 9.0),
            (8, 10.0),
        ),
        guidance=(
            "0: No tracked deterministic combos",
            "1-2: Some dedicated lines exist",
            "3+: Multiple reliable combo paths",
        ),
    ),
)

SCORE_SIGNAL_MAP: Dict[str, ScoreSignal] = {signal.key: signal for signal in SCORE_SIGNALS}
SCORE_CURVES: Dict[str, Tuple[Tuple[float, float], ...]] = {
    signal.key: signal.curve for signal in SCORE_SIGNALS
}

SIGNAL_REASON_LABELS: Dict[str, str] = {
    "game_changers": "Game Changers list card",
    "extra_turns": "extra turn effect",
    "mass_land": "mass land denial piece",
    "zero_cmc_mana": "fast mana source",
    "cedh_signatures": "cEDH signature card",
    "instant_win": "instant-win enabler",
}

SCORE_OVERVIEW_TEXT = (
    "We apply hard bracket gates first (Game Changers, mass land denial, extra turns, fast mana, "
    "instant-win lines, and Commander Spellbook combos). The score then places decks into coarse "
    "bands, but the hard gate always wins."
)

SCORE_OVERVIEW_GUIDANCE: Tuple[str, ...] = (
    "0-4: Exhibition — theme-first pods",
    "5-8: Core — low-pressure casual",
    "9-12: Upgraded — tuned synergy",
    "13-24: Optimized — high-power",
    "25+: cEDH — competitive pace",
)

SCORE_BANDS: Tuple[Tuple[float, int], ...] = (
    (4.0, 1),
    (8.0, 2),
    (12.0, 3),
    (24.0, 4),
    (math.inf, 5),
)

AVG_CMC_BENEFITS = [
    (2.0, 3.5),
    (2.25, 2.5),
    (2.5, 1.5),
    (2.8, 0.5),
]

AVG_CMC_PENALTIES = [
    (3.8, -0.5),
    (4.1, -1.0),
]




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
            key = _normalize_card_key(name)
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





def _load_spellbook_combos() -> Dict[str, Any]:
    global SPELLBOOK_DATA_PATH
    data_path = _spellbook_data_path()
    SPELLBOOK_DATA_PATH = data_path

    if not data_path.exists():

        return {

            "early": [],

            "late": [],

            "early_index": {},

            "late_index": {},

        }



    try:

        payload = json.loads(data_path.read_text(encoding="utf-8"))

    except Exception:

        return {

            "early": [],

            "late": [],

            "early_index": {},

            "late_index": {},

        }



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

    """Reload Commander Spellbook combo dataset from disk.



    Returns True when the dataset was replaced. When the new payload is empty

    and an existing dataset is already in memory, the globals remain untouched

    and False is returned.

    """



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





def _find_spellbook_matches(

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



def _combo_piece_count(combo: SpellbookCombo) -> int:
    total = 0
    for qty in combo.requirements.values():
        try:
            value = int(qty)
        except (TypeError, ValueError):
            value = 1
        total += max(1, value)
    return total



def _is_three_card_combo(combo: SpellbookCombo) -> bool:
    return _combo_piece_count(combo) >= 3





def _format_spellbook_combo_descriptor(combo: SpellbookCombo) -> str:

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







def _normalize_face_names(name: str) -> Set[str]:

    """Split adventure/DFC names so comparisons are reliable."""

    parts = re.split(r"\s+//\s+", name or "")

    out: Set[str] = set()

    for part in parts:

        clean = part.strip()

        if not clean:

            continue

        out.add(clean)

        # Remove parenthetical reminders (e.g., "Bust (// Boom)")

        cleaned = re.sub(r"\s*\([^)]*\)", "", clean).strip()

        if cleaned:

            out.add(cleaned)

    return out or {name}





def _card_from_print(pr: Dict[str, Any], fallback_name: str = "") -> BracketCard:

    name = sc.display_name_for_print(pr) if hasattr(sc, "display_name_for_print") else pr.get("name") or fallback_name

    type_line = sc.type_label_for_print(pr) if hasattr(sc, "type_label_for_print") else pr.get("type_line") or ""



    text_parts: List[str] = []

    if pr.get("oracle_text"):

        text_parts.append(pr["oracle_text"])

    for face in pr.get("card_faces") or []:

        ot = (face or {}).get("oracle_text")

        if ot:

            text_parts.append(ot)

    oracle_text = " // ".join(tp for tp in text_parts if tp)



    mana_cost = pr.get("mana_cost")

    produced_mana = pr.get("produced_mana")

    mana_value = pr.get("cmc") if pr.get("cmc") is not None else pr.get("mana_value")

    is_game_changer = bool(pr.get("game_changer"))



    return BracketCard(

        name=name or fallback_name,

        type_line=type_line,

        oracle_text=oracle_text,

        mana_value=mana_value,

        mana_cost=mana_cost,

        produced_mana=produced_mana if isinstance(produced_mana, (list, tuple)) else None,

        is_game_changer=is_game_changer,

    )





def _resolve_commander(commander: Dict[str, Any]) -> Optional[BracketCard]:

    oracle_id = (commander or {}).get("oracle_id")

    name = (commander or {}).get("name")

    ensure_cache_loaded()



    pr: Optional[Dict[str, Any]] = None

    if oracle_id:

        try:

            prints = prints_for_oracle(oracle_id) or []

        except Exception:

            prints = []

        if prints:

            pr = prints[0]

    if not pr and name:

        try:

            oid = unique_oracle_by_name(name)

        except Exception:

            oid = None

        if oid:

            try:

                prints = prints_for_oracle(oid) or []

            except Exception:

                prints = []

            if prints:

                pr = prints[0]

    if not pr and name:

        # Last resort fallback to a minimal record.

        return BracketCard(name=name)

    if not pr:

        return None

    return _card_from_print(pr, fallback_name=name or "")





def _is_zero_cmc_mana(card: BracketCard) -> bool:

    if "Land" in (card.type_line or ""):

        return False

    cmc = card.mana_value

    if cmc is None:

        return False

    if cmc > 0:

        return False

    text = (card.oracle_text or "").lower()

    mana_cost = (card.mana_cost or "").strip()

    produces = card.produced_mana or []

    if produces:

        return True

    if ADDS_MANA_PATTERN.search(text):

        return True

    if mana_cost == "":

        return True

    return False





def _is_mass_land_denial(card: BracketCard) -> bool:

    for name in _normalize_face_names(card.name):

        if name in MASS_LAND_DENIAL_NAMES:

            return True

    text = card.oracle_text

    if not text:

        return False

    return any(p.search(text) for p in MASS_LAND_DENIAL_PATTERNS)





def _is_game_changer(card: BracketCard) -> bool:

    if getattr(card, "is_game_changer", False):

        return True

    return any(face in GAME_CHANGERS for face in _normalize_face_names(card.name))





def _is_cedh_signature(card: BracketCard) -> bool:

    return any(face in CEDH_SIGNATURES for face in _normalize_face_names(card.name))





def _is_extra_turn_piece(card: BracketCard) -> bool:

    return bool(EXTRA_TURN_PATTERN.search(card.oracle_text or ""))





def _is_instant_win(card: BracketCard) -> bool:

    text = card.oracle_text or ""

    if INSTANT_WIN_PATTERN.search(text):

        return True

    return any(

        marker in _normalize_face_names(card.name)

        for marker in ("Laboratory Maniac", "Jace, Wielder of Mysteries", "Thassa's Oracle")

    )





def _is_nonland_tutor(card: BracketCard) -> bool:

    text = card.oracle_text or ""

    type_line = card.type_line or ""

    if "Land" in type_line:

        return False

    if "Creature" in type_line:

        return False

    if not TUTOR_PATTERN.search(text):

        return False

    if (

        LAND_TUTOR_PATTERN.search(text)

        or LAND_TUTOR_EXT_PATTERN.search(text)

        or "land card" in text.lower()

    ) and not re.search(

        r"nonland|creature card|artifact card|enchantment card|instant card|sorcery card|planeswalker card|permanent card|any card|spell|noncreature card",

        text,

        flags=re.IGNORECASE,

    ):

        return False

    return True





def _is_land_tutor(card: BracketCard) -> bool:

    text = card.oracle_text or ""

    if not TUTOR_PATTERN.search(text):

        return False

    if "Land" in (card.type_line or ""):

        return True

    lowered = text.lower()

    if LAND_TUTOR_PATTERN.search(text) or LAND_TUTOR_EXT_PATTERN.search(text):

        return True

    return "land card" in lowered





def evaluate_commander_bracket(

    deck_cards: Iterable[Dict[str, Any] | BracketCard],

    commander: Optional[Dict[str, Any]] = None,

) -> Dict[str, Any]:

    """

    Evaluate the Commander Bracket level for a deck based on heuristics derived

    from Wizards' beta announcement and widespread competitive staples data.

    Returns a dict with `level`, `label`, `score`, `summary_points`, and raw metrics.

    """



    ensure_cache_loaded()

    ruleset = _load_bracket_ruleset()
    ruleset_metrics = ruleset.get("metrics") if isinstance(ruleset, dict) else {}
    if not isinstance(ruleset_metrics, dict):
        ruleset_metrics = {}
    ruleset_modifiers = ruleset.get("modifiers") if isinstance(ruleset, dict) else {}
    if not isinstance(ruleset_modifiers, dict):
        ruleset_modifiers = {}
    ruleset_reinforcement = ruleset.get("reinforcement") if isinstance(ruleset, dict) else {}
    if not isinstance(ruleset_reinforcement, dict):
        ruleset_reinforcement = {}
    ruleset_bracket5 = ruleset.get("bracket5_confidence") if isinstance(ruleset, dict) else {}
    if not isinstance(ruleset_bracket5, dict):
        ruleset_bracket5 = {}

    sources: List[BracketCard] = []

    for item in deck_cards or []:

        if isinstance(item, BracketCard):

            card_obj = item

        else:

            text = (item.get("oracle_text") or "") if isinstance(item, dict) else ""

            card_obj = BracketCard(

                name=str(item.get("name") or ""),

                type_line=str(item.get("type_line") or ""),

                oracle_text=text,

                mana_value=item.get("mana_value") if isinstance(item, dict) else None,

                quantity=int(item.get("quantity") or 0) or 1 if isinstance(item, dict) else 1,

                mana_cost=item.get("mana_cost") if isinstance(item, dict) else None,

                produced_mana=item.get("produced_mana") if isinstance(item, dict) else None,

                is_game_changer=bool(item.get("game_changer")) if isinstance(item, dict) else False,

            )

        if not card_obj.name:

            continue

        sources.append(card_obj)



    commander_card = _resolve_commander(commander or {})

    commander_name = commander_card.name if commander_card else None

    if commander_card:

        already = {card.name.casefold() for card in sources}

        if commander_card.name and commander_card.name.casefold() not in already:

            commander_card.quantity = 1

            sources.append(commander_card)



    advantage_roles = {"draw", "selection", "advantage", "engine", "recursion"}
    interaction_roles = {"removal", "wipe", "counter", "bounce", "tax", "stax", "hate", "protection"}

    buckets: Dict[str, MetricBucket] = {
        "card_advantage": MetricBucket("card_advantage"),
        "efficient_interaction": MetricBucket("efficient_interaction"),
        "game_changers": MetricBucket("game_changers"),
        "extra_turns": MetricBucket("extra_turns"),
        "mass_land": MetricBucket("mass_land"),
        "nonland_tutors": MetricBucket("nonland_tutors"),
        "land_tutors": MetricBucket("land_tutors"),
        "cedh_signatures": MetricBucket("cedh_signatures"),
        "zero_cmc_mana": MetricBucket("zero_cmc_mana"),
        "instant_win": MetricBucket("instant_win"),
        "spellbook_combos": MetricBucket("spellbook_combos"),
    }



    total_cards = 0
    nonland_count = 0
    nonland_cmc_sum = 0.0
    land_count = 0
    basic_land_count = 0
    deck_counts: Dict[str, int] = defaultdict(int)



    for card in sources:

        qty = int(card.quantity or 0) or 1

        total_cards += qty

        if "Land" not in (card.type_line or "") and card.mana_value is not None:

            nonland_count += qty

            nonland_cmc_sum += float(card.mana_value) * qty

        if card.name:
            for face_name in _normalize_face_names(card.name):
                key = _normalize_card_key(face_name)
                if key:
                    deck_counts[key] += qty

        if "Land" in (card.type_line or ""):
            land_count += qty
            if "Basic" in (card.type_line or ""):
                basic_land_count += qty

        roles = derive_core_roles(
            oracle_text=card.oracle_text,
            type_line=card.type_line,
            name=card.name,
        )
        if roles & advantage_roles:
            buckets["card_advantage"].add(card.name, qty)
        if roles & interaction_roles:
            buckets["efficient_interaction"].add(card.name, qty)



        if _is_game_changer(card):

            buckets["game_changers"].add(card.name, qty)

        if _is_extra_turn_piece(card):

            buckets["extra_turns"].add(card.name, qty)

        if _is_mass_land_denial(card):

            buckets["mass_land"].add(card.name, qty)

        if _is_nonland_tutor(card):

            buckets["nonland_tutors"].add(card.name, qty)

        elif _is_land_tutor(card):

            buckets["land_tutors"].add(card.name, qty)

        if _is_cedh_signature(card):

            buckets["cedh_signatures"].add(card.name, qty)

        if _is_zero_cmc_mana(card):

            buckets["zero_cmc_mana"].add(card.name, qty)

        if _is_instant_win(card):

            buckets["instant_win"].add(card.name, qty)



    spellbook_seen: Set[str] = set()

    _early_matches = _find_spellbook_matches(deck_counts, SPELLBOOK_EARLY_INDEX, spellbook_seen)

    spellbook_late = _find_spellbook_matches(deck_counts, SPELLBOOK_LATE_INDEX, spellbook_seen)

    spellbook_early: List[SpellbookCombo] = []

    spellbook_three_card: List[SpellbookCombo] = []

    for combo in _early_matches:
        if _is_three_card_combo(combo):
            spellbook_three_card.append(combo)
        else:
            spellbook_early.append(combo)

    group_label_lookup = {slug: label for slug, label in SPELLBOOK_COMBO_GROUPS}

    def _label_for(tag: str) -> Optional[str]:
        marker = SPELLBOOK_RESULT_LABELS.get(tag, ...)
        if marker is None:
            return None
        if marker is ...:
            return tag.replace('_', ' ')
        return marker

    def _build_spellbook_entry(combo: SpellbookCombo, tier: str) -> Dict[str, Any]:
        card_entries: List[Dict[str, str]] = []
        card_tokens: List[str] = []
        for card_name in combo.cards or ():
            encoded = quote_plus(card_name)
            qty = combo.requirements.get(_normalize_card_key(card_name), 0)
            label = f"{card_name} &times;{qty}" if qty and qty > 1 else card_name
            card_entries.append({
                'name': card_name,
                'label': label,
                'hover': f"https://api.scryfall.com/cards/named?format=image&version=large&exact={encoded}",
            })
            card_tokens.append(label)

        categories = tuple(combo.result_categories or ())
        tags = [
            _infinity_badge(group_label_lookup[slug])
            for slug in categories
            if slug in group_label_lookup
        ]

        return {
            'id': combo.id,
            'url': combo.url or f"https://commanderspellbook.com/combo/{combo.id}",
            'result_labels': [
                lbl for tag in combo.result_categories
                if (lbl := _label_for(tag))
            ],
            'cards': card_entries,
            'cards_line': ' + '.join(card_tokens) if card_tokens else combo.id,
            'categories': categories,
            'tags': tags,
            'tier': tier,
            'piece_count': _combo_piece_count(combo),
        }

    spellbook_details_for_view: List[Dict[str, Any]] = []

    spellbook_late_details: List[Dict[str, Any]] = []

    spellbook_three_card_details: List[Dict[str, Any]] = []

    for combo in spellbook_early:
        buckets['spellbook_combos'].add(_format_spellbook_combo_descriptor(combo), 1)
        spellbook_details_for_view.append(_build_spellbook_entry(combo, 'scoring'))

    for combo in spellbook_late:
        spellbook_late_details.append(_build_spellbook_entry(combo, 'late'))

    for combo in spellbook_three_card:
        spellbook_three_card_details.append(_build_spellbook_entry(combo, 'three_card'))

    all_spellbook_entries = (
        spellbook_details_for_view
        + spellbook_late_details
        + spellbook_three_card_details
    )

    combo_groups: List[Dict[str, Any]] = []
    for slug, label in SPELLBOOK_COMBO_GROUPS:
        grouped_items = [item for item in all_spellbook_entries if slug in item.get("categories", ())]
        if grouped_items:
            for item in grouped_items:
                item.setdefault("_grouped", set()).add(slug)
            combo_groups.append({
                "key": slug,
                "label": _infinity_badge(label),
                "items": grouped_items,
            })

    leftovers = [item for item in all_spellbook_entries if not item.get("_grouped")]
    for item in all_spellbook_entries:
        if "_grouped" in item:
            del item["_grouped"]
    if leftovers:
        combo_groups.append({
            "key": "other",
            "label": "Other",
            "items": leftovers,
        })


    early_combo_count = len(spellbook_early)

    late_combo_count = len(spellbook_late)

    three_card_combo_count = len(spellbook_three_card)

    avg_cmc = (nonland_cmc_sum / nonland_count) if nonland_count else None



    count = {key: bucket.count for key, bucket in buckets.items()}

    total_spellbook_combos = count["spellbook_combos"]

    instant_win_combo_count = sum(1 for combo in spellbook_early if "instant_win" in combo.result_categories)

    early_instant_combo_count = sum(1 for combo in spellbook_early if "instant_win" in combo.result_categories)

    nonbasic_land_count = max(land_count - basic_land_count, 0)
    nonbasic_ratio = (nonbasic_land_count / land_count) if land_count else 0.0
    mana_base_cfg = ruleset_modifiers.get("mana_base_optimization", {}) if isinstance(ruleset_modifiers, dict) else {}
    min_nonbasic = int(mana_base_cfg.get("min_nonbasic") or 0)
    min_ratio = float(mana_base_cfg.get("min_ratio") or 0.0)
    mana_base_optimized = False
    if land_count:
        if min_nonbasic and nonbasic_land_count >= min_nonbasic:
            mana_base_optimized = True
        if min_ratio and nonbasic_ratio >= min_ratio:
            mana_base_optimized = True

    ruleset_metric_counts: Dict[str, int] = {
        "card_advantage": count.get("card_advantage", 0),
        "efficient_interaction": count.get("efficient_interaction", 0),
        "two_card_infinite_combos": early_combo_count,
        "game_changers": count.get("game_changers", 0),
        "mass_land_denial": count.get("mass_land", 0),
        "extra_turn_loops": count.get("extra_turns", 0),
    }
    fast_mana_density = count.get("zero_cmc_mana", 0)
    ruleset_floor: Optional[int] = None
    ruleset_metric_brackets: Dict[str, int] = {}
    ruleset_triggers: List[str] = []
    if ruleset_metrics:
        ruleset_floor = 1
        for key, meta in ruleset_metrics.items():
            if not isinstance(meta, dict):
                continue
            metric_count = ruleset_metric_counts.get(key, 0)
            thresholds = meta.get("thresholds") or []
            applied = [
                t for t in thresholds
                if isinstance(t, dict) and metric_count >= int(t.get("min") or 0)
            ]
            applied_for_floor = [
                t for t in applied
                if int(t.get("bracket") or 0) <= 4
            ]
            metric_bracket = (
                max((int(t.get("bracket") or 0) for t in applied_for_floor), default=1)
                if applied_for_floor
                else 1
            )
            ruleset_metric_brackets[key] = metric_bracket
            ruleset_floor = max(ruleset_floor, metric_bracket)
            if applied:
                top = max(applied, key=lambda t: (int(t.get("bracket") or 0), int(t.get("min") or 0)))
                label = meta.get("label") or key.replace("_", " ").title()
                threshold_val = int(top.get("min") or 0)
                bracket_val = int(top.get("bracket") or 0)
                ruleset_triggers.append(
                    f"{label}: {metric_count} (threshold {threshold_val} -> bracket {bracket_val})"
                )



    score = 0.0
    score_breakdown: List[Dict[str, Any]] = []
    score_methodology: Dict[str, Any] = {
        "overview": SCORE_OVERVIEW_TEXT,
        "guidance": list(SCORE_OVERVIEW_GUIDANCE),
        "signals": [],
        "adjustments": [],
    }
    if ruleset_metrics:
        score_methodology["guidance"].append(
            "Bracket floor uses the ruleset thresholds; score provides additional context."
        )

    def add_component(key: str, value: float, reason: str) -> None:
        nonlocal score
        if abs(value) < 1e-6:
            return
        score += value
        score_breakdown.append({
            "key": key,
            "points": round(value, 2),
            "reason": reason,
        })

    def _format_signal_reason(signal_key: str, count_value: int) -> str:
        label = SIGNAL_REASON_LABELS.get(signal_key, signal_key.replace("_", " "))
        plural = "" if count_value == 1 else "s"
        return f"{count_value} {label}{plural}"

    for signal in SCORE_SIGNALS:
        bucket = buckets.get(signal.key)
        metric_count = count.get(signal.key, 0)
        detail: Dict[str, Any] = {
            "key": signal.key,
            "label": signal.label,
            "description": signal.description,
            "guidance": list(signal.guidance),
            "show_if_zero": signal.show_if_zero,
            "curve": [
                {"count": float(threshold), "points": float(value)}
                for threshold, value in signal.curve
            ],
            "count": metric_count,
            "examples": list(bucket.names[:6]) if bucket and bucket.names else [],
        }

        applied = False
        reason_text: Optional[str] = None
        points = 0.0

        if signal.key == "spellbook_combos":
            metric_count = total_spellbook_combos
            detail["count"] = metric_count
            detail["counts"] = {
                "total": metric_count,
                "early": early_combo_count,
                "early_instant": early_instant_combo_count,
                "late": late_combo_count,
                "three_card": three_card_combo_count,
            }
            detail["notes"] = (
                "Late-game and three-card combos are surfaced in the breakdown but do not add to this score."
            )
            reason_parts: List[str] = []
            components_meta: List[Dict[str, Any]] = []

            base_score = _score_piecewise(metric_count, signal.curve)
            points = base_score
            if metric_count:
                reason_parts.append(
                    f"{metric_count} Commander Spellbook combo{'s' if metric_count != 1 else ''}"
                )
            if base_score:
                components_meta.append({"label": "Combo density", "points": round(base_score, 2)})

            early_combo_bonus = max(0, early_combo_count - early_instant_combo_count) * 0.9
            if early_combo_count:
                reason_parts.append(
                    f"{early_combo_count} early combo{'s' if early_combo_count != 1 else ''}"
                )
            if early_combo_bonus:
                points += early_combo_bonus
                components_meta.append(
                    {"label": "Early combo pressure", "points": round(early_combo_bonus, 2)}
                )

            early_instant_bonus = early_instant_combo_count * 1.5
            if early_instant_combo_count:
                reason_parts.append(
                    f"{early_instant_combo_count} early instant-win line"
                    f"{'s' if early_instant_combo_count != 1 else ''}"
                )
            if early_instant_bonus:
                points += early_instant_bonus
                components_meta.append(
                    {"label": "Early instant-win lines", "points": round(early_instant_bonus, 2)}
                )

            if points:
                reason_text = (
                    ", ".join(reason_parts)
                    if reason_parts
                    else "Commander Spellbook combos detected"
                )
                add_component(signal.key, points, reason_text)
                applied = True
            else:
                reason_text = ", ".join(reason_parts) if reason_parts else None

            if components_meta:
                detail["components"] = components_meta
        elif signal.key == "zero_cmc_mana":
            points = 0.0
            reason_text = None
            applied = False
        else:
            points = _score_piecewise(metric_count, signal.curve)
            if points:
                reason_text = _format_signal_reason(signal.key, metric_count)
                add_component(signal.key, points, reason_text)
                applied = True

        detail["points"] = round(points, 2)
        detail["reason"] = reason_text
        detail["applied"] = applied
        score_methodology["signals"].append(detail)

    cmc_detail: Dict[str, Any] = {
        "key": "avg_cmc",
        "label": "Average mana value (nonland cards)",
        "description": "Lean curves push decks toward competitive tables; heavier curves slow the score down.",
        "value": round(avg_cmc, 2) if avg_cmc is not None else None,
        "benefits": [
            {"threshold": float(threshold), "points": float(value), "comparison": "<="}
            for threshold, value in AVG_CMC_BENEFITS
        ],
        "penalties": [
            {"threshold": float(threshold), "points": float(value), "comparison": ">="}
            for threshold, value in AVG_CMC_PENALTIES
        ],
    }

    if avg_cmc is not None:
        benefit = 0.0
        for threshold, val in sorted(AVG_CMC_BENEFITS, key=lambda x: x[0]):
            if avg_cmc <= threshold:
                benefit = val
                break
        penalty = 0.0
        for threshold, val in sorted(AVG_CMC_PENALTIES, key=lambda x: x[0]):
            if avg_cmc >= threshold:
                penalty = val
        cmc_adjust = benefit + penalty
        reason_parts: List[str] = []
        if benefit:
            reason_parts.append(f"Tight curve (avg MV {avg_cmc:.2f})")
        if penalty:
            reason_parts.append(f"Heavier curve (avg MV {avg_cmc:.2f})")
        reason_text = "; ".join(reason_parts) if reason_parts else None
        cmc_detail["points"] = round(cmc_adjust, 2)
        cmc_detail["reason"] = reason_text
        cmc_detail["applied"] = bool(cmc_adjust)
        if cmc_adjust:
            add_component("avg_cmc", cmc_adjust, reason_text or "Average mana value adjustment")
    else:
        cmc_detail["points"] = 0.0
        cmc_detail["reason"] = None
        cmc_detail["applied"] = False

    score_methodology["adjustments"].append(cmc_detail)

    commander_flag = commander_name in CEDH_COMMANDERS if commander_name else False
    commander_detail: Dict[str, Any] = {
        "key": "commander",
        "label": "Commander pedigree",
        "description": (
            "Known cEDH commanders receive a safety buffer so competitive shells aren't seeded into low brackets."
        ),
        "value": commander_name,
        "points": 2.5 if commander_flag else 0.0,
        "applied": commander_flag,
        "reason": "Commander is a known cEDH staple" if commander_flag else None,
        "guidance": [
            "Applies only to staples that commonly headline cEDH lists.",
            "Used to keep dedicated cEDH shells out of casual tables.",
        ],
    }
    if commander_flag:
        add_component("commander", 2.5, "Commander is a known cEDH staple")
    score_methodology["adjustments"].append(commander_detail)

    score_methodology["total_points"] = round(score, 2)

    effective_game_changers = count["game_changers"]
    bracket1_ok = False
    bracket2_ok = False
    bracket3_ok = False
    ruleset_level: Optional[int] = None
    ruleset_reinforced: Optional[int] = None
    bracket5_score: float | None = None
    bracket5_signals: int | None = None

    if ruleset_metrics:
        ruleset_floor_val = ruleset_floor or 1
        min_signals_for_bump = int(ruleset_reinforcement.get("min_signals_for_bump") or 2)
        min_floor_for_bump = int(ruleset_reinforcement.get("min_floor_for_bump") or 2)
        max_bracket = int(ruleset_reinforcement.get("max_bracket") or 4)
        signals_at_floor = sum(
            1 for key, bracket in ruleset_metric_brackets.items()
            if bracket == ruleset_floor_val and ruleset_metric_counts.get(key, 0) > 0
        )
        ruleset_reinforced = ruleset_floor_val
        if ruleset_floor_val >= min_floor_for_bump and signals_at_floor >= min_signals_for_bump:
            ruleset_reinforced = min(ruleset_floor_val + 1, max_bracket)
        ruleset_level = ruleset_reinforced

        b5_weights = ruleset_bracket5.get("weights") if isinstance(ruleset_bracket5, dict) else {}
        if not isinstance(b5_weights, dict):
            b5_weights = {}
        b5_thresholds = ruleset_bracket5.get("thresholds") if isinstance(ruleset_bracket5, dict) else {}
        if not isinstance(b5_thresholds, dict):
            b5_thresholds = {}
        promote_to_5 = float(b5_thresholds.get("promote_to_5") or 0)
        remain_at_4 = float(b5_thresholds.get("remain_at_4") or 0)
        bracket5_score = 0.0
        bracket5_signals = 0
        for key, weight in b5_weights.items():
            metric_value = fast_mana_density if key == "fast_mana_density" else ruleset_metric_counts.get(key, 0)
            if metric_value:
                bracket5_score += float(weight) * float(metric_value)
                bracket5_signals += 1
        if promote_to_5 and bracket5_score >= promote_to_5 and bracket5_signals >= 2:
            ruleset_level = 5
        elif remain_at_4 and bracket5_score >= remain_at_4 and bracket5_signals >= 2:
            ruleset_level = max(ruleset_level or 0, 4)

        level = ruleset_level or ruleset_floor_val
        bracket1_ok = ruleset_floor_val == 1
        bracket2_ok = ruleset_floor_val <= 2
        bracket3_ok = ruleset_floor_val <= 3
    else:
        bracket1_ok = (
            effective_game_changers == 0
            and count["extra_turns"] == 0
            and count["mass_land"] == 0
            and count["cedh_signatures"] == 0
            and count["zero_cmc_mana"] == 0
            and count["instant_win"] == 0
            and total_spellbook_combos == 0
            and (avg_cmc is None or avg_cmc >= 3.3)
        )

        bracket2_ok = (
            effective_game_changers == 0
            and count["mass_land"] == 0
            and total_spellbook_combos == 0
            and count["extra_turns"] <= 1
        )

        bracket3_ok = (
            effective_game_changers <= 3
            and count["mass_land"] == 0
            and early_combo_count == 0
            and count["extra_turns"] <= 2
        )

        hard_floor = 4
        if bracket1_ok:
            hard_floor = 1
        elif bracket2_ok:
            hard_floor = 2
        elif bracket3_ok:
            hard_floor = 3

        score_band = _score_to_band(score)
        level = max(hard_floor, score_band)

    label = BRACKET_LABELS.get(level, "Unknown")



    summary_points: List[str] = []

    for key in (

        "game_changers",

        "extra_turns",

        "mass_land",

        "zero_cmc_mana",

        "cedh_signatures",

        "spellbook_combos",

    ):

        bucket = buckets[key]

        if not bucket.entries:

            continue

        summary_points.append(f"{bucket.count} {key.replace('_', ' ')}")

    if mana_base_optimized:
        summary_points.append("Mana base optimized")



    summary_cards: Dict[str, List[str]] = {

        key: bucket.names for key, bucket in buckets.items() if bucket.entries

    }

    if buckets["nonland_tutors"].entries and "tutors" not in summary_cards:
        summary_cards["tutors"] = buckets["nonland_tutors"].names



    summary_tooltip = (

        " | ".join(

            f"{key.replace('_', ' ').title()}: "

            + ", ".join(names[:6])

            + ("..." if len(names) > 6 else "")

            for key, names in summary_cards.items()

        )

        if summary_cards

        else None

    )

    metrics_payload = {key: bucket.count for key, bucket in buckets.items()}
    metrics_payload["two_card_infinite_combos"] = early_combo_count
    metrics_payload["mana_base_optimization"] = 1 if mana_base_optimized else 0
    metrics_payload["tutors"] = count.get("nonland_tutors", 0)

    if ruleset_metrics:
        score_methodology["ruleset"] = {
            "version": ruleset.get("version"),
            "floor": ruleset_floor,
            "reinforced": ruleset_reinforced,
            "metric_brackets": ruleset_metric_brackets,
            "triggers": ruleset_triggers,
            "mana_base_optimized": mana_base_optimized,
            "nonbasic_lands": nonbasic_land_count,
            "nonbasic_ratio": round(nonbasic_ratio, 3) if land_count else None,
            "bracket5_score": round(bracket5_score, 2) if bracket5_score is not None else None,
            "bracket5_signals": bracket5_signals,
        }



    return {

        "level": level,

        "label": label,

        "score": round(score, 2),

        "avg_cmc": round(avg_cmc, 2) if avg_cmc is not None else None,

        "total_cards": total_cards,

        "commander": commander_name,

        "summary_points": summary_points,

        "summary_cards": summary_cards,

        "spellbook_details": spellbook_details_for_view,
        "spellbook_late_details": spellbook_late_details,
        "spellbook_three_card_details": spellbook_three_card_details,
        "late_combo_count": late_combo_count,
        "three_card_combo_count": three_card_combo_count,
        "spellbook_combo_groups": combo_groups,

        "summary_tooltip": summary_tooltip,
        "metrics": metrics_payload,
        "score_breakdown": score_breakdown,
        "score_methodology": score_methodology,
        "is_commander_cedh": commander_flag,
        "bracket1_eligible": bracket1_ok,
    }


# ---------------------------------------------------------------------------

# Commander bracket reference data (used for the explainer page)

# ---------------------------------------------------------------------------

BRACKET_REFERENCE: List[Dict[str, Any]] = [
    {
        "level": 1,
        "name": BRACKET_LABELS[1],
        "subtitle": "Theme-first showcase games",
        "experience": (
            "Pods lean into story-driven builds and house rules so everyone can spotlight their creations. "
            "Power takes a back seat to flavorful experiences."
        ),
        "deck_building": [
            "Prioritize a goal, theme, or idea over raw power.",
            "Flex card or commander legality via Rule 0 when the pod agrees.",
            "Choose win conditions because they reinforce the theme, even if they are suboptimal.",
            "Treat the game as a showcase for creative deckbuilding.",
        ],
        "table_contract": [
            "Expect games to last at least nine turns before a win or loss.",
            "No Game Changers (exceptions only for highly thematic cards).",
            "No mass land denial pieces.",
            "No extra-turn spells.",
            "No two-card combos (game-enders, lockouts, or infinites).",
        ],
    },
    {
        "level": 2,
        "name": BRACKET_LABELS[2],
        "subtitle": "Mechanically focused, low-pressure pods",
        "experience": (
            "Decks tighten their mechanics but still leave room for creative or entertaining card choices. "
            "Core is a landing point for low-pressure play rather than a strict precon baseline. "
            "Games are proactive yet considerate so every list can execute its plan."
        ),
        "deck_building": [
            "Keep builds mechanically focused while preserving some room for creativity or spectacle.",
            "Favor incremental, telegraphed, and disruptable win conditions.",
            "Aim for proactive but considerate play that lets each deck showcase its plan.",
        ],
        "table_contract": [
            "Expect games to last at least eight turns before someone wins or loses.",
            "No Game Changers.",
            "No mass land denial pieces.",
            "No chaining extra turns.",
            "No two-card combos (game-enders, lockouts, or infinites).",
        ],
    },
    {
        "level": 3,
        "name": BRACKET_LABELS[3],
        "subtitle": "Upgraded synergy with interaction",
        "experience": (
            "High-quality cards and tuned synergies appear alongside potent disruption. Decks can win from hand in "
            "one turn after building resources, yet still exchange interaction with the table."
        ),
        "deck_building": [
            "Lean on strong synergy, high card quality, and meaningful disruption.",
            "Plan to deploy win conditions from hand in a single turn, often after steady resource growth.",
            "Balance proactive threats with reactive play patterns.",
        ],
        "table_contract": [
            "Expect games to reach at least six turns before a win or loss.",
            "Allow up to three Game Changers.",
            "No mass land denial pieces.",
            "No chaining extra turns.",
            "No two-card combos before turn six (game-enders, lockouts, or infinites).",
        ],
    },
    {
        "level": 4,
        "name": BRACKET_LABELS[4],
        "subtitle": "Optimized high-power pods (non-cEDH)",
        "experience": (
            "Decks are lethal, consistent, and fast without adhering to the cEDH metagame. Explosive threats and "
            "efficient disruption race to end games quickly."
        ),
        "deck_building": [
            "Do not assume opponents are bringing cEDH lists, but be ready for lethal, consistent, and fast decks.",
            "Expect Game Changers to include fast mana, snowballing engines, free disruption, and premium tutors.",
            "Tune win conditions to be efficient and instantaneous once assembled.",
            "Prepare for gameplay that is explosive and powerful, matching threats with equally efficient disruption.",
        ],
        "table_contract": [
            "Expect games to resolve around turn four (or shortly after).",
            "Align on pacing, stax pieces, and combo speed during the pre-game conversation.",
        ],
    },
    {
        "level": 5,
        "name": BRACKET_LABELS[5],
        "subtitle": "cEDH pods and tournament prep",
        "experience": (
            "Lists are meticulously tuned for the cEDH metagame and piloted with tournament precision. "
            "Every slot pursues victory as efficiently as possible."
        ),
        "deck_building": [
            "Build with established cEDH knowledge, tools, and decklists in mind.",
            "Use win conditions that are optimized for efficiency and consistency.",
            "Pack interaction and disruption to fight razor-thin margins at every table.",
        ],
        "table_contract": [
            "Games can end on any turn; mulligans and sequencing matter from the start.",
            "Expect intricate, high-stakes decision-making where everyone is playing to win.",
        ],
    },
]
BRACKET_REFERENCE_BY_LEVEL: Dict[int, Dict[str, Any]] = {

    item["level"]: item for item in BRACKET_REFERENCE

}
