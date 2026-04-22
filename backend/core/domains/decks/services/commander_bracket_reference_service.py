"""Static Commander Bracket reference data and ruleset loading."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

__all__ = [
    "BRACKET_LABELS",
    "BRACKET_REFERENCE",
    "BRACKET_REFERENCE_BY_LEVEL",
    "BRACKET_RULESET_EPOCH",
    "CEDH_COMMANDERS",
    "CEDH_SIGNATURES",
    "GAME_CHANGERS",
    "MASS_LAND_DENIAL_NAMES",
    "MASS_LAND_DENIAL_PATTERNS",
    "load_bracket_ruleset",
]


BRACKET_LABELS: Dict[int, str] = {
    1: "Exhibition",
    2: "Core",
    3: "Upgraded",
    4: "Optimized",
    5: "cEDH",
}

BRACKET_RULESET_EPOCH = 8

_BACKEND_ROOT = Path(__file__).resolve().parents[4]
BRACKET_RULESET_PATH = _BACKEND_ROOT / "commander-brackets" / "commander_brackets_ruleset.json"


@lru_cache(maxsize=1)
def load_bracket_ruleset() -> Dict[str, Any]:
    try:
        with BRACKET_RULESET_PATH.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


GAME_CHANGERS: Set[str] = {
    "Drannith Magistrate",
    "Humility",
    "Serra's Sanctum",
    "Smothering Tithe",
    "Enlightened Tutor",
    "Teferi's Protection",
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
    "Gamble",
    "Jeska's Will",
    "Underworld Breach",
    "Crop Rotation",
    "Gaea's Cradle",
    "Natural Order",
    "Seedborn Muse",
    "Survival of the Fittest",
    "Worldly Tutor",
    "Aura Shards",
    "Coalition Victory",
    "Grand Arbiter Augustin IV",
    "Notion Thief",
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
    "Winter Orb",
    "Static Orb",
    "Hokori, Dust Drinker",
    "Rising Waters",
    "Mana Breach",
    "Wave of Vitriol",
    "Price of Glory",
}

MASS_LAND_DENIAL_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"destroy all lands", re.IGNORECASE),
    re.compile(r"each player sacrifices [^.!?]*land", re.IGNORECASE),
    re.compile(r"lands? (?:you|they|players?) control (?:don'?t|can't) untap", re.IGNORECASE),
    re.compile(r"players? can't play lands", re.IGNORECASE),
)


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
    "Troll of Khazad-dum",
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
