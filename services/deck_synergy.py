from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote_plus

from sqlalchemy import func

from extensions import db
from models import Card, Folder
from services.deck_tags import TAG_CATEGORY_MAP, resolve_deck_tag_from_slug
from services.scryfall_cache import (
    ensure_cache_loaded,
    find_by_set_cn,
    image_for_print,
    prints_for_oracle,
    unique_oracle_by_name,
)
from services.commander_utils import primary_commander_name, primary_commander_oracle_id
from services.edhrec import (
    CardView,
    commander_cardviews,
    ensure_commander_data,
    ensure_theme_data,
    merge_cardviews,
    normalize_card_key,
    slugify_theme,
    theme_cardviews,
)
from services.pricing import format_price_text, prices_for_print
from services.deck_utils import BASIC_LANDS
from services.symbols_cache import render_mana_html

__all__ = ["analyze_deck", "calculate_tag_synergy", "classify_roles", "detect_themes_for_text"]

WUBRG_ORDER = "WUBRG"
BASIC_LAND_SLUGS = {normalize_card_key(name) for name in BASIC_LANDS}
CI_NAME_BY_SET = {
    frozenset(): "Colorless",
    frozenset({"W"}): "White",
    frozenset({"U"}): "Blue",
    frozenset({"B"}): "Black",
    frozenset({"R"}): "Red",
    frozenset({"G"}): "Green",
    frozenset({"W", "U"}): "Azorius",
    frozenset({"U", "B"}): "Dimir",
    frozenset({"U", "R"}): "Izzet",
    frozenset({"B", "R"}): "Rakdos",
    frozenset({"B", "G"}): "Golgari",
    frozenset({"W", "B"}): "Orzhov",
    frozenset({"R", "G"}): "Gruul",
    frozenset({"W", "R"}): "Boros",
    frozenset({"W", "G"}): "Selesnya",
    frozenset({"U", "G"}): "Simic",
    frozenset({"W", "U", "G"}): "Bant",
    frozenset({"W", "U", "B"}): "Esper",
    frozenset({"U", "B", "R"}): "Grixis",
    frozenset({"B", "R", "G"}): "Jund",
    frozenset({"W", "B", "G"}): "Abzan",
    frozenset({"W", "U", "R"}): "Jeskai",
    frozenset({"W", "B", "R"}): "Mardu",
    frozenset({"U", "B", "G"}): "Sultai",
    frozenset({"U", "R", "G"}): "Temur",
    frozenset({"W", "U", "B", "R"}): "Yore",
    frozenset({"U", "B", "R", "G"}): "Glint",
    frozenset({"B", "R", "G", "W"}): "Dune",
    frozenset({"R", "G", "W", "U"}): "Ink",
    frozenset({"G", "W", "U", "B"}): "Witch",
    frozenset({"W", "U", "B", "R", "G"}): "5c",
}

TYPE_BUCKET_ORDER: Tuple[Tuple[str, str], ...] = (
    ("Land", "land"),
    ("Creature", "creature"),
    ("Artifact", "artifact"),
    ("Enchantment", "enchantment"),
    ("Planeswalker", "planeswalker"),
    ("Battle", "battle"),
    ("Instant", "instant"),
    ("Sorcery", "sorcery"),
    ("Vehicle", "vehicle"),
    ("Class", "class"),
    ("Background", "background"),
    ("Dungeon", "dungeon"),
    ("Attraction", "attraction"),
    ("Scheme", "scheme"),
    ("Phenomenon", "phenomenon"),
    ("Plane", "plane"),
)

TYPE_COLOR_FALLBACKS: Dict[str, str] = {
    "Land": "#e98125",
    "Creature": "#64a61f",
    "Artifact": "#634c22",
    "Enchantment": "#d8d138",
    "Planeswalker": "#7b6688",
    "Battle": "#c26dce",
    "Instant": "#2081c1",
    "Sorcery": "#961818",
    "Vehicle": "#9c6f3d",
    "Class": "#3b6fb6",
    "Background": "#ad5ca2",
    "Dungeon": "#4a4a4a",
    "Attraction": "#d36f5f",
    "Scheme": "#8c6239",
    "Phenomenon": "#5fa7c9",
    "Plane": "#4d8c85",
    "Other": "#6c757d",
}

ROLE_CONFIG: Dict[str, Dict[str, Any]] = {
    "ramp": {"label": "Ramp & Acceleration"},
    "mana_fixing": {"label": "Mana Fixing"},
    "card_draw": {"label": "Card Advantage"},
    "card_selection": {"label": "Card Selection"},
    "interaction": {"label": "Targeted Interaction"},
    "board_wipe": {"label": "Board Wipes"},
    "tutor": {"label": "Tutors & Search"},
    "recursion": {"label": "Recursion & Reanimation"},
    "protection": {"label": "Protection & Safeguards"},
    "finisher": {"label": "Finishers & Win Conditions"},
    "stax": {"label": "Tax & Stax"},
}

# Regex helpers compiled once for role detection.
ROLE_PATTERNS = {
    "adds_mana": re.compile(r"add\s+\{[wubrgc\d/]+\}", re.IGNORECASE),
    "adds_any_color": re.compile(
        r"add\b[^.]*\b(?:any combination of colors|mana of any color)", re.IGNORECASE
    ),
    "search_land": re.compile(r"search\b[^.]*\bland", re.IGNORECASE),
    "draw_card": re.compile(r"draw [a-z ]*card", re.IGNORECASE),
    "destroy_target": re.compile(r"destroy target", re.IGNORECASE),
    "exile_target": re.compile(r"exile target", re.IGNORECASE),
    "counter_target": re.compile(r"counter target", re.IGNORECASE),
    "return_target": re.compile(r"return target [^.]*owner", re.IGNORECASE),
    "destroy_all": re.compile(r"destroy all", re.IGNORECASE),
    "exile_all": re.compile(r"exile all", re.IGNORECASE),
    "each_creature": re.compile(r"each creature", re.IGNORECASE),
    "mass_return": re.compile(r"return (all|each)", re.IGNORECASE),
    "sacrifice_all": re.compile(r"sacrifice all", re.IGNORECASE),
    "sacrifices_all": re.compile(r"sacrifices all", re.IGNORECASE),
    "search_library": re.compile(r"search (your|their) library", re.IGNORECASE),
    "reveal_from_library": re.compile(r"reveal [^.]* from your library", re.IGNORECASE),
    "return_from_grave": re.compile(r"return [^.]* from your graveyard", re.IGNORECASE),
    "regrowth_effect": re.compile(r"from your graveyard to (your hand|the battlefield)", re.IGNORECASE),
    "grant_indestructible": re.compile(r"indestructible", re.IGNORECASE),
    "grant_hexproof": re.compile(r"hexproof", re.IGNORECASE),
    "phase_out": re.compile(r"phase out", re.IGNORECASE),
    "prevent_damage": re.compile(r"prevent all damage", re.IGNORECASE),
    "each_opponent_loses": re.compile(r"each opponent loses", re.IGNORECASE),
    "you_win_game": re.compile(r"you win the game", re.IGNORECASE),
    "extra_turn": re.compile(r"take an extra turn", re.IGNORECASE),
    "scry": re.compile(r"\bscry\b", re.IGNORECASE),
    "surveil": re.compile(r"\bsurveil\b", re.IGNORECASE),
    "look_top": re.compile(r"look at the top [^.]* (?:put|may reveal)", re.IGNORECASE),
    "choose_color": re.compile(r"choose a color", re.IGNORECASE),
    "tax_cost": re.compile(r"costs? [^.;]* more to cast", re.IGNORECASE),
    "tax_attack": re.compile(r"can't attack|can't block", re.IGNORECASE),
    "tax_untap": re.compile(r"does(?: not|'t) untap|can't untap", re.IGNORECASE),
    "tax_opponent_cant": re.compile(
        r"opponents? can't (?:cast|play|activate|search|draw|gain life)", re.IGNORECASE
    ),
    "tax_unless_pays": re.compile(r"unless [^.;]* pays? \{", re.IGNORECASE),
}


EDHREC_PRESENT_LIMIT = 15
EDHREC_MISSING_LIMIT = 25
EDHREC_ROLE_LIMIT = 6
EDHREC_THEME_SHOWCASE_LIMIT = 3
EDHREC_THEME_SHOWCASE_CARD_LIMIT = 6


THEME_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "tokens": {
        "label": "Token Production",
        "description": "This deck creates creature or Treasure tokens frequently.",
        "patterns": [re.compile(r"create\b[^.]*\btoken", re.IGNORECASE)],
        "min_score": 3,
    },
    "+1/+1 counters": {
        "label": "+1/+1 Counters",
        "description": "Multiple cards grow creatures with +1/+1 counters.",
        "patterns": [re.compile(r"\+1/\+1 counter", re.IGNORECASE)],
        "min_score": 3,
    },
    "artifacts": {
        "label": "Artifacts Matter",
        "description": "Artifact synergies or high artifact density detected.",
        "patterns": [
            re.compile(r"\bartifact\b.*\byou control", re.IGNORECASE),
            re.compile(r"\bartifact creature\b", re.IGNORECASE),
        ],
        "min_score": 3,
    },
    "sacrifice": {
        "label": "Sacrifice & Aristocrats",
        "description": "Repeated sacrifice outlets or death triggers appear.",
        "patterns": [
            re.compile(r"sacrifice (another|a|target)", re.IGNORECASE),
            re.compile(r"whenever [^.]* dies", re.IGNORECASE),
        ],
        "min_score": 4,
    },
    "graveyard": {
        "label": "Graveyard Recursion",
        "description": "Cards recur from the graveyard or fill it deliberately.",
        "patterns": [
            re.compile(r"return target [^.]* from your graveyard", re.IGNORECASE),
            re.compile(r"mill [a-z ]+\d", re.IGNORECASE),
            re.compile(r"from your graveyard to", re.IGNORECASE),
        ],
        "min_score": 3,
    },
    "spellslinger": {
        "label": "Spellslinger",
        "description": "Lots of instants, sorceries, or payoffs for casting them.",
        "patterns": [
            re.compile(r"instant or sorcery spell", re.IGNORECASE),
            re.compile(r"noncreature spell", re.IGNORECASE),
        ],
        "min_score": 3,
    },
    "lifegain": {
        "label": "Lifegain",
        "description": "Cards reward repeatedly gaining life.",
        "patterns": [
            re.compile(r"gain [\dX]+ life", re.IGNORECASE),
            re.compile(r"whenever you gain life", re.IGNORECASE),
        ],
        "min_score": 3,
    },
    "enchantments": {
        "label": "Enchantments",
        "description": "Aura/Enchantment density or payoffs detected.",
        "patterns": [
            re.compile(r"enchantment spell", re.IGNORECASE),
            re.compile(r"aura spell", re.IGNORECASE),
            re.compile(r"constellation", re.IGNORECASE),
        ],
        "min_score": 3,
    },
    "treasure": {
        "label": "Treasure Tokens",
        "description": "Creates or leverages Treasure tokens frequently.",
        "patterns": [
            re.compile(r"create [^.]*treasure token", re.IGNORECASE),
            re.compile(r"treasure you control", re.IGNORECASE),
        ],
        "min_score": 3,
    },
    "equipment": {
        "label": "Equipment",
        "description": "High density of equipment cards or payoffs.",
        "patterns": [
            re.compile(r"equip\s*\{", re.IGNORECASE),
            re.compile(r"equipment you control", re.IGNORECASE),
        ],
        "min_score": 3,
    },
    "lands_matter": {
        "label": "Lands Matter",
        "description": "Rewards land drops, landfall, or additional land plays.",
        "patterns": [
            re.compile(r"landfall", re.IGNORECASE),
            re.compile(r"land enters the battlefield under your control", re.IGNORECASE),
            re.compile(r"play an additional land", re.IGNORECASE),
        ],
        "min_score": 3,
    },
}


@dataclass(frozen=True)
class Recommendation:
    name: str
    colors: Tuple[str, ...]
    roles: Tuple[str, ...]
    notes: str
    themes: Tuple[str, ...] = ()

    def matches_colors(self, deck_colors: Set[str]) -> bool:
        return set(self.colors).issubset(deck_colors)


@dataclass(frozen=True)
class TagSynergyCard:
    name: str
    colors: Tuple[str, ...] = ()
    weight: int = 1
    note: str = ""
    roles: Tuple[str, ...] = ()
    themes: Tuple[str, ...] = ()

    def matches_colors(self, deck_colors: Set[str]) -> bool:
        if not self.colors:
            return True
        if not deck_colors:
            return False
        return set(self.colors).issubset(deck_colors)


@dataclass(frozen=True)
class TagSynergyPackage:
    tag: str
    label: str
    description: str
    expected_themes: Tuple[str, ...] = ()
    core_cards: Tuple[TagSynergyCard, ...] = ()
    support_cards: Tuple[TagSynergyCard, ...] = ()
    theme_minimums: Dict[str, int] = field(default_factory=dict)
    min_core_hits: int = 2

    def theme_threshold_for(self, theme: str) -> int:
        return self.theme_minimums.get(theme, 2)


TAG_SYNERGY_PACKAGES: Dict[str, TagSynergyPackage] = {
    "Tokens": TagSynergyPackage(
        tag="Tokens",
        label="Token Swarm",
        description="Leans on token doublers and card advantage engines to overwhelm the table.",
        expected_themes=("tokens",),
        theme_minimums={"tokens": 3},
        core_cards=(
            TagSynergyCard("Anointed Procession", colors=("W",), weight=2, note="White token decks want a doubler online quickly."),
            TagSynergyCard("Parallel Lives", colors=("G",), weight=2, note="Green access to the classic token doubling effect."),
            TagSynergyCard("Mondrak, Glory Dominus", colors=("W",), weight=1, note="A resilient doubler that fits any Selesnya token shell."),
            TagSynergyCard("Skullclamp", weight=1, note="Premium draw engine once tokens are flowing.", roles=("card_draw",), themes=("tokens", "sacrifice")),
            TagSynergyCard("Cathars' Crusade", colors=("W",), weight=1, note="Turns every token into a team-wide pump spell.", themes=("+1/+1 counters",)),
        ),
        support_cards=(
            TagSynergyCard("Felidar Retreat", colors=("W",), note="Landfall creates bodies and anthem counters."),
            TagSynergyCard("March of the Multitudes", colors=("W", "G"), note="Instant-speed burst of tokens that scales with board presence."),
            TagSynergyCard("Heroic Intervention", colors=("G",), note="Protects your board and payoff enchantments."),
            TagSynergyCard("Smothering Tithe", colors=("W",), note="Treasure tokens ramp into token payoffs.", roles=("ramp",), themes=("tokens", "artifacts")),
            TagSynergyCard("Chord of Calling", colors=("G",), note="Convoke tutor to find token payoff creatures."),
        ),
    ),
    "+1/+1 Counters": TagSynergyPackage(
        tag="+1/+1 Counters",
        label="Growth Engine",
        description="Stacks counter multipliers and proliferate effects to snowball creatures.",
        expected_themes=("+1/+1 counters",),
        theme_minimums={"+1/+1 counters": 3},
        core_cards=(
            TagSynergyCard("Hardened Scales", colors=("G",), weight=2, note="Cheap multiplier that supercharges every counter placed."),
            TagSynergyCard("Branching Evolution", colors=("G",), weight=2, note="Doubles your counter output turn after turn."),
            TagSynergyCard("Conclave Mentor", colors=("G", "W"), weight=1, note="Adds an extra counter and pads life totals.", themes=("+1/+1 counters", "lifegain")),
            TagSynergyCard("The Ozolith", weight=1, note="Banks counters for redeployment after board wipes."),
            TagSynergyCard("Evolution Sage", colors=("G",), weight=1, note="Proliferates on land drops to scale your board."),
        ),
        support_cards=(
            TagSynergyCard("Agatha's Soul Cauldron", weight=1, note="Turns counters into flexible activated abilities."),
            TagSynergyCard("Inspiring Call", colors=("G",), note="Protects the team while refilling your hand.", roles=("card_draw",)),
            TagSynergyCard("Champion of Lambholt", colors=("G",), note="Grows evasive threats alongside counter engines."),
            TagSynergyCard("Heliod, Sun-Crowned", colors=("W",), note="Combines lifegain with counter placement."),
            TagSynergyCard("Vorinclex, Monstrous Raider", colors=("G",), note="Double counters coming in and slows opponents."),
        ),
    ),
    "Sacrifice": TagSynergyPackage(
        tag="Sacrifice",
        label="Aristocrats Engine",
        description="Loops free sacrifice outlets with death payoffs to drain the table.",
        expected_themes=("sacrifice", "graveyard"),
        theme_minimums={"sacrifice": 3, "graveyard": 2},
        core_cards=(
            TagSynergyCard("Pitiless Plunderer", colors=("B",), weight=2, note="Treasure per death fuels combo turns.", themes=("tokens",)),
            TagSynergyCard("Phyrexian Altar", weight=2, note="Premium mana outlet for sacrifice chains."),
            TagSynergyCard("Ashnod's Altar", weight=1, note="Colorless sac outlet that accelerates the table."),
            TagSynergyCard("Blood Artist", colors=("B",), weight=1, note="Drains every opponent whenever creatures die.", themes=("lifegain",)),
            TagSynergyCard("Viscera Seer", colors=("B",), weight=1, note="One-mana sac outlet that smooths draws."),
        ),
        support_cards=(
            TagSynergyCard("Reassembling Skeleton", colors=("B",), note="Repeatable fodder that keeps engines running."),
            TagSynergyCard("Yawgmoth, Thran Physician", colors=("B",), note="Free sac outlet with card draw and removal.", roles=("card_draw", "interaction")),
            TagSynergyCard("Liliana, Dreadhorde General", colors=("B",), note="Rewards sacrifices with cards and pressure."),
            TagSynergyCard("Skullclamp", note="Draw engine for expendable creatures.", roles=("card_draw",), themes=("sacrifice",)),
            TagSynergyCard("Teysa Karlov", colors=("W", "B"), note="Doubles death triggers and buffs tokens."),
        ),
    ),
    "Spellslinger": TagSynergyPackage(
        tag="Spellslinger",
        label="Instant & Sorcery Storm",
        description="Chaining cheap spells with payoffs that reward volume and velocity.",
        expected_themes=("spellslinger",),
        theme_minimums={"spellslinger": 3},
        core_cards=(
            TagSynergyCard("Storm-Kiln Artist", colors=("R",), weight=2, note="Creates Treasures for every spell to keep the chain going."),
            TagSynergyCard("Thousand-Year Storm", colors=("U", "R"), weight=2, note="Copies every spell for explosive turns."),
            TagSynergyCard("Young Pyromancer", colors=("R",), weight=1, note="Bodies for every instant or sorcery."),
            TagSynergyCard("Talrand, Sky Summoner", colors=("U",), weight=1, note="Drake army payoff for spell volume."),
            TagSynergyCard("Archmage Emeritus", colors=("U",), weight=1, note="Draws a card each time you cast an instant or sorcery.", roles=("card_draw",)),
        ),
        support_cards=(
            TagSynergyCard("Saheeli, Sublime Artificer", colors=("U", "R"), note="Noncreature spells create servo blockers."),
            TagSynergyCard("Ledger Shredder", colors=("U",), note="Connive engine that rewards double-spelling.", roles=("card_draw",)),
            TagSynergyCard("Mizzix's Mastery", colors=("R",), note="Flashback your graveyard for a finisher."),
            TagSynergyCard("Electrostatic Field", colors=("R",), note="Pings each opponent whenever you cast instants or sorceries."),
            TagSynergyCard("Baral, Chief of Compliance", colors=("U",), note="Cost reduction and loot for casting interaction.", roles=("interaction",)),
        ),
    ),
    "Artifacts": TagSynergyPackage(
        tag="Artifacts",
        label="Artifact Value Engine",
        description="Dense artifact packages with recursion and cost reduction payoffs.",
        expected_themes=("artifacts",),
        theme_minimums={"artifacts": 3},
        core_cards=(
            TagSynergyCard("Sai, Master Thopterist", colors=("U",), weight=1, note="Creates thopters for each artifact spell."),
            TagSynergyCard("Emry, Lurker of the Loch", colors=("U",), weight=1, note="Cheap recursion for artifacts each turn."),
            TagSynergyCard("Scrap Trawler", weight=1, note="Value chain whenever artifacts hit the graveyard."),
            TagSynergyCard("Thought Monitor", colors=("U",), weight=1, note="Affinity draw engine for artifact decks.", roles=("card_draw",)),
            TagSynergyCard("Inspiring Statuary", weight=1, note="Improvise turns spare artifacts into mana."),
        ),
        support_cards=(
            TagSynergyCard("Unwinding Clock", weight=1, note="Untaps artifacts each opponent's turn."),
            TagSynergyCard("Darksteel Forge", weight=1, note="Gives your artifacts indestructible."),
            TagSynergyCard("Mystic Forge", weight=1, note="Lets you cast artifacts off the top of your library."),
            TagSynergyCard("Etherium Sculptor", colors=("U",), note="Cost reduction to double-spell artifacts."),
            TagSynergyCard("Losheel, Clockwork Scholar", colors=("W",), note="Card draw and combat protection for artifact creatures.", roles=("card_draw",)),
        ),
    ),
    "Lifegain": TagSynergyPackage(
        tag="Lifegain",
        label="Life Engine",
        description="Rewards repeated life gain with scaling threats and combo finishes.",
        expected_themes=("lifegain",),
        theme_minimums={"lifegain": 3},
        core_cards=(
            TagSynergyCard("Archangel of Thune", colors=("W",), weight=2, note="Turns life gain into permanent team-wide buffs."),
            TagSynergyCard("Aetherflux Reservoir", weight=1, note="Win condition that feeds off chains of spells and life gain."),
            TagSynergyCard("Well of Lost Dreams", weight=1, note="Draw engine tied to big life gain bursts.", roles=("card_draw",)),
            TagSynergyCard("Ajani's Pridemate", colors=("W",), weight=1, note="Classic threat that grows with each trigger."),
            TagSynergyCard("Cleric Class", colors=("W",), weight=1, note="Levels into double life gain and a reanimation burst."),
        ),
        support_cards=(
            TagSynergyCard("Righteous Valkyrie", colors=("W",), note="Pumps the board once you hit high life totals."),
            TagSynergyCard("Boon Reflection", colors=("W",), note="Doubles all life gain for massive swings."),
            TagSynergyCard("Sanguine Bond", colors=("B",), note="Turns gaining life into direct damage."),
            TagSynergyCard("Exquisite Blood", colors=("B",), note="Combines with Bond effects for infinite drain."),
            TagSynergyCard("Vizkopa Guildmage", colors=("W", "B"), note="Activated ability closes the game with big life swings."),
        ),
    ),
}

_TAG_SYNERGY_LOOKUP: Dict[str, str] = {pkg.tag.lower(): pkg.tag for pkg in TAG_SYNERGY_PACKAGES.values()}
_TAG_SYNERGY_LOOKUP.update(
    {
        "token": "Tokens",
        "tokens": "Tokens",
        "treasure": "Tokens",
        "+1/+1 counters": "+1/+1 Counters",
        "counters": "+1/+1 Counters",
        "aristocrats": "Sacrifice",
        "sacrifice": "Sacrifice",
        "spellslinger": "Spellslinger",
        "storm": "Spellslinger",
        "artifacts": "Artifacts",
        "affinity": "Artifacts",
        "lifegain": "Lifegain",
        "lifedrain": "Lifegain",
    }
)
SYNERGY_GRADE_BANDS: Tuple[Dict[str, Any], ...] = (
    {"grade": "S", "min": 90, "description": "Signature package online."},
    {"grade": "A", "min": 75, "description": "Core synergies are mostly assembled."},
    {"grade": "B", "min": 60, "description": "Solid footing with room to sharpen."},
    {"grade": "C", "min": 45, "description": "Developing plan, add more payoffs."},
    {"grade": "D", "min": 30, "description": "Key engines are still missing."},
    {"grade": "F", "min": 0, "description": "Start by adding the tag's staple payoffs."},
)


def _grade_for_synergy_score(score: int) -> Tuple[str, str]:
    for band in SYNERGY_GRADE_BANDS:
        if score >= band["min"]:
            return band["grade"], band["description"]
    # Fallback safeguard; loop should always return.
    fallback = SYNERGY_GRADE_BANDS[-1]
    return fallback["grade"], fallback["description"]


def calculate_tag_synergy(
    deck_tag: Optional[str],
    deck_colors: Iterable[str],
    present_names: Iterable[str],
    theme_counts: Optional[Dict[str, int]] = None,
    present_card_ids: Optional[Dict[str, int]] = None,
    *,
    core_limit: int = 6,
    support_limit: int = 6,
) -> Optional[Dict[str, Any]]:
    if not deck_tag:
        return None
    normalized = str(deck_tag).strip().lower()
    if not normalized:
        return None
    deck_color_set = {c.upper() for c in (deck_colors or []) if c}
    present_lower = {str(name).lower() for name in (present_names or []) if name}
    theme_counts = {k: int(v) for k, v in (theme_counts or {}).items()}
    canonical = _TAG_SYNERGY_LOOKUP.get(normalized)
    package = TAG_SYNERGY_PACKAGES.get(canonical) if canonical else None
    if not package:
        return _theme_tag_fallback(
            deck_tag=deck_tag,
            canonical=canonical,
            deck_color_set=deck_color_set,
            present_lower=present_lower,
            theme_counts=theme_counts,
            present_card_ids=present_card_ids,
            core_limit=core_limit,
            support_limit=support_limit,
        )

    eligible_core_cards = [card for card in package.core_cards if card.matches_colors(deck_color_set)]
    total_weight = sum(card.weight for card in eligible_core_cards)
    present_weight = 0
    present_core_entries: List[Dict[str, Any]] = []
    missing_core_entries: List[Dict[str, Any]] = []
    for card in eligible_core_cards:
        in_deck = card.name.lower() in present_lower
        card_id = None
        if present_card_ids:
            card_id = present_card_ids.get(card.name.lower())
        payload = {
            "name": card.name,
            "notes": card.note,
            "weight": card.weight,
            "colors": list(card.colors),
            "roles": list(card.roles),
            "themes": list(card.themes),
            "already_in_deck": in_deck,
            "scryfall_url": _scryfall_search_url(card.name),
            "hover_image": _hover_image_for_card_name(card.name),
        }
        if card_id:
            payload["card_id"] = card_id
        if in_deck:
            present_weight += card.weight
            present_core_entries.append(payload)
        else:
            missing_core_entries.append(payload)

    eligible_core_count = len(eligible_core_cards)
    present_core_count = len(present_core_entries)
    score = int(round(present_core_count * 100 / eligible_core_count)) if eligible_core_count else 0
    grade, status_text = _grade_for_synergy_score(score)

    theme_evaluations: List[Dict[str, Any]] = []
    for theme_key in package.expected_themes:
        threshold = package.theme_threshold_for(theme_key)
        hits = theme_counts.get(theme_key, 0)
        meta = THEME_DEFINITIONS.get(theme_key, {})
        theme_evaluations.append(
            {
                "key": theme_key,
                "label": meta.get("label") or theme_key.title(),
                "hits": hits,
                "expected": threshold,
                "meets": hits >= threshold,
            }
        )

    theme_expectations = len(theme_evaluations)
    theme_alignment = sum(1 for item in theme_evaluations if item["meets"])
    confidence = None
    if theme_expectations:
        confidence = int(round(theme_alignment * 100 / theme_expectations))

    eligible_support_cards = [card for card in package.support_cards if card.matches_colors(deck_color_set)]
    support_recommendations: List[Dict[str, Any]] = []
    for card in eligible_support_cards:
        if card.name.lower() in present_lower:
            continue
        support_recommendations.append(
            {
                "name": card.name,
                "notes": card.note,
                "weight": card.weight,
                "colors": list(card.colors),
                "roles": list(card.roles),
                "themes": list(card.themes),
                "already_in_deck": False,
                "scryfall_url": _scryfall_search_url(card.name),
                "hover_image": _hover_image_for_card_name(card.name),
            }
        )

    support_recommendations = support_recommendations[:support_limit]

    alerts: List[str] = []
    for item in theme_evaluations:
        if not item["meets"] and item["expected"] > 0:
            alerts.append(
                f"Only {item['hits']} cards detected for {item['label'].lower()}; aim for {item['expected']}."
            )
    if missing_core_entries and total_weight:
        alerts.append("Add more of the tag's core payoffs to unlock higher synergy.")

    grade_bands = [dict(band) for band in SYNERGY_GRADE_BANDS]
    explanation = {
        "score": {
            "present_count": present_core_count,
            "eligible_count": eligible_core_count,
            "note": "Score is the share of the tag's core synergy cards currently in your deck.",
        },
        "grade": {
            "current": {"grade": grade, "description": status_text},
            "bands": grade_bands,
        },
        "confidence": {
            "value": confidence,
            "alignment": theme_alignment,
            "expectations": theme_expectations,
            "note": "Confidence reflects how many expected themes reach their minimum counts.",
        },
    }

    return {
        "tag": deck_tag,
        "canonical_tag": package.tag,
        "label": package.label,
        "category": TAG_CATEGORY_MAP.get(package.tag) or TAG_CATEGORY_MAP.get(deck_tag),
        "description": package.description,
        "score": score,
        "grade": grade,
        "status_text": status_text,
        "confidence": confidence,
        "explanation": explanation,
        "core": {
            "present": present_core_entries,
            "missing": missing_core_entries[:core_limit],
            "missing_count": len(missing_core_entries),
            "present_weight": present_weight,
            "total_weight": total_weight,
            "eligible_count": len(eligible_core_cards),
            "present_count": len(present_core_entries),
        },
        "support": {
            "recommendations": support_recommendations,
            "additional_available": max(0, len(eligible_support_cards) - len(support_recommendations)),
        },
        "themes": theme_evaluations,
        "theme_alignment": theme_alignment,
        "theme_expectations": theme_expectations,
        "alerts": alerts,
    }


RECOMMENDATIONS: Tuple[Recommendation, ...] = (
    Recommendation("Arcane Signet", (), ("ramp",), "Two-mana rock that fixes for any commander."),
    Recommendation("Sol Ring", (), ("ramp",), "Premier one-mana ramp for any deck."),
    Recommendation("Fellwar Stone", (), ("ramp",), "Cheap rock that taps for many colors in multiplayer."),
    Recommendation("Chromatic Lantern", (), ("ramp",), "Fixes every land for three colors and beyond."),
    Recommendation("Cultivate", ("G",), ("ramp",), "Classic two-for-one land ramp for green decks.", ("+1/+1 counters",)),
    Recommendation("Kodama's Reach", ("G",), ("ramp",), "Redundant Cultivate to stabilize land drops."),
    Recommendation("Three Visits", ("G",), ("ramp",), "Fetches untapped dual lands at two mana."),
    Recommendation("Nature's Lore", ("G",), ("ramp",), "Finds duals untapped; combos with shock lands."),
    Recommendation("Farseek", ("G",), ("ramp",), "Grabs shock/triome lands to fix colors."),
    Recommendation("Smothering Tithe", ("W",), ("ramp",), "Treasure engine that keeps up with green ramp.", ("tokens", "artifacts")),
    Recommendation("Deep Gnome Terramancer", ("W",), ("ramp",), "Keeps parity against opposing ramp spells."),
    Recommendation("Dockside Extortionist", ("R",), ("ramp",), "Explosive treasure ramp in multiplayer.", ("tokens", "artifacts")),
    Recommendation("Jeska's Will", ("R",), ("ramp", "card_draw"), "Burst mana and impulse draw for spellslinger decks.", ("spellslinger",)),
    Recommendation("Skyshroud Claim", ("G",), ("ramp",), "Untapped duals in four mana for big ramp turns."),
    Recommendation("Solemn Simulacrum", (), ("ramp", "card_draw"), "Ramp plus card draw on death; great in blink shells.", ("artifacts", "sacrifice")),
    Recommendation("Rhystic Study", ("U",), ("card_draw",), "Tax draw engine that pressures the table.", ("spellslinger",)),
    Recommendation("Mystic Remora", ("U",), ("card_draw",), "Cheap cumulative upkeep draw engine.", ("spellslinger",)),
    Recommendation("Esper Sentinel", ("W",), ("card_draw",), "Repeatable draw from opponents' noncreature spells.", ("artifacts", "spellslinger")),
    Recommendation("Archivist of Oghma", ("W",), ("card_draw",), "Capitalizes on tutors and ramp across the table.", ("lifegain",)),
    Recommendation("Toski, Bearer of Secrets", ("G",), ("card_draw",), "Uncounterable draw engine for creature decks.", ("tokens", "+1/+1 counters")),
    Recommendation("Beast Whisperer", ("G",), ("card_draw",), "Turns creature chains into cards."),
    Recommendation("Guardian Project", ("G",), ("card_draw",), "Reward for unique creature builds."),
    Recommendation("Return of the Wildspeaker", ("G",), ("card_draw",), "Flexible draw or finisher in creature decks.", ("+1/+1 counters",)),
    Recommendation("Skullclamp", (), ("card_draw",), "Elite draw engine for tokens or sacrifice loops.", ("tokens", "sacrifice")),
    Recommendation("Tome of Legends", (), ("card_draw",), "Cheap value engine for commander-focused decks.", ("artifacts",)),
    Recommendation("Phyrexian Arena", ("B",), ("card_draw",), "Steady life-for-cards engine."),
    Recommendation("Necropotence", ("B",), ("card_draw",), "Game-ending burst draw for life-based decks.", ("lifegain",)),
    Recommendation("Black Market Connections", ("B",), ("card_draw", "ramp"), "Draw, treasure, or token every upkeep.", ("tokens", "lifegain")),
    Recommendation("Bolas's Citadel", ("B",), ("card_draw",), "Explosive top-deck engine for low curves.", ("sacrifice",)),
    Recommendation("Krosan Grip", ("G",), ("interaction",), "Split second answers problematic permanents."),
    Recommendation("Beast Within", ("G",), ("interaction",), "Hits any permanent at instant speed."),
    Recommendation("Return to Nature", ("G",), ("interaction",), "Modal answer for artifacts, enchantments, or graveyards."),
    Recommendation("Nature's Claim", ("G",), ("interaction",), "Cheap artifact/enchantment answer."),
    Recommendation("Swords to Plowshares", ("W",), ("interaction",), "Efficient creature exile spell."),
    Recommendation("Path to Exile", ("W",), ("interaction",), "One mana instant exile for creatures."),
    Recommendation("Generous Gift", ("W",), ("interaction",), "Answers any permanent at instant speed."),
    Recommendation("Farewell", ("W",), ("board_wipe",), "Flexible sweep that handles graveyards and artifacts."),
    Recommendation("Wrath of God", ("W",), ("board_wipe",), "Classic four-mana creature sweeper."),
    Recommendation("Supreme Verdict", ("W", "U"), ("board_wipe",), "Uncounterable sweep for Azorius shells."),
    Recommendation("Merciless Eviction", ("W", "B"), ("board_wipe",), "Exile-based sweeper with modes."),
    Recommendation("Demonic Tutor", ("B",), ("tutor",), "Two-mana search for any card in your library."),
    Recommendation("Enlightened Tutor", ("W",), ("tutor",), "Instant tutor for artifacts or enchantments.", ("artifacts", "enchantments")),
    Recommendation("Worldly Tutor", ("G",), ("tutor",), "Instant tutor that finds any creature."),
    Recommendation("Fabricate", ("U",), ("tutor",), "Finds the artifact you need at sorcery speed.", ("artifacts",)),
    Recommendation("Eternal Witness", ("G",), ("recursion",), "Staple regrowth on a creature body.", ("graveyard",)),
    Recommendation("Sun Titan", ("W",), ("recursion",), "Recurring value engine for permanents cost three or less.", ("graveyard",)),
    Recommendation("Sevinne's Reclamation", ("W",), ("recursion",), "Flashback recursion for key permanents.", ("graveyard",)),
    Recommendation("Yawgmoth's Will", ("B",), ("recursion",), "One-turn burst replay from the graveyard.", ("graveyard", "spellslinger")),
    Recommendation("Teferi's Protection", ("W",), ("protection",), "Phases you and your permanents out to dodge wipes."),
    Recommendation("Heroic Intervention", ("G",), ("protection",), "Instant hexproof and indestructible for your board.", ("tokens", "+1/+1 counters")),
    Recommendation("Flawless Maneuver", ("W",), ("protection",), "Free protection when your commander is out."),
    Recommendation("Deflecting Swat", ("R",), ("protection",), "Redirects targeted interaction and can be free.", ("combo",)),
    Recommendation("Craterhoof Behemoth", ("G",), ("finisher",), "Ends the game with massive overrun for creature decks.", ("tokens", "+1/+1 counters")),
    Recommendation("Triumph of the Hordes", ("G",), ("finisher",), "Single-turn infect swing to close games.", ("tokens",)),
    Recommendation("Exsanguinate", ("B",), ("finisher",), "Drains every opponent and stabilises your life total.", ("lifegain",)),
    Recommendation("Aetherflux Reservoir", (), ("finisher",), "Combo finisher for spell-heavy decks.", ("spellslinger",)),
    Recommendation("Hero's Downfall", ("B",), ("interaction",), "Instant-speed creature or walker removal."),
    Recommendation("Feed the Swarm", ("B",), ("interaction",), "Black answer to enchantments."),
    Recommendation("Toxic Deluge", ("B",), ("board_wipe",), "Life-based sweeper that dodges indestructible."),
    Recommendation("Damnation", ("B",), ("board_wipe",), "Efficient creature wipe in black."),
    Recommendation("Cyclonic Rift", ("U",), ("board_wipe", "interaction"), "Instant-speed reset for nonlands.", ("spellslinger",)),
    Recommendation("Counterspell", ("U",), ("interaction",), "Baseline counter for blue decks."),
    Recommendation("Swan Song", ("U",), ("interaction",), "One mana counter for problem spells."),
    Recommendation("Mana Drain", ("U",), ("interaction", "ramp"), "Counter that refunds mana next turn."),
    Recommendation("Vandalblast", ("R",), ("interaction", "board_wipe"), "Answers a single artifact or wipes the table."),
    Recommendation("Abrade", ("R",), ("interaction",), "Flexible bolt/artifact removal spell."),
    Recommendation("Chaos Warp", ("R",), ("interaction",), "Universal answer in red."),
    Recommendation("Blasphemous Act", ("R",), ("board_wipe",), "Cheap mass damage in creature metas."),
    Recommendation("Bane of Progress", ("G",), ("board_wipe",), "Dismantles artifact/enchantment boards."),
    Recommendation("All Is Dust", (), ("board_wipe",), "Colorless sweeper for permanent-heavy boards."),
    Recommendation("Nevinyrral's Disk", (), ("board_wipe",), "Reset button for colorless shells.", ("artifacts",)),
    Recommendation("Dictate of Erebos", ("B",), ("sacrifice", "interaction"), "Punishes opponents when your creatures die."),
    Recommendation("Blood Artist", ("B",), ("sacrifice", "lifegain"), "Drains life for each death trigger."),
    Recommendation("Zulaport Cutthroat", ("B",), ("sacrifice", "lifegain"), "Redundant Blood Artist effect."),
    Recommendation("Pitiless Plunderer", ("B",), ("sacrifice", "ramp", "tokens"), "Creates treasure from creature deaths.", ("tokens", "artifacts")),
    Recommendation("Mayhem Devil", ("B", "R"), ("sacrifice", "interaction"), "Ping engine for sacrifice decks."),
    Recommendation("The Ozolith", (), ("+1/+1 counters",), "Stores counters to redeploy value."),
    Recommendation("Hardened Scales", ("G",), ("+1/+1 counters",), "Extra counters for each placement."),
    Recommendation("Conclave Mentor", ("G", "W"), ("+1/+1 counters",), "Counters payoff in Selesnya shells."),
    Recommendation("Winding Constrictor", ("B", "G"), ("+1/+1 counters",), "Doubling counters for Golgari builds."),
    Recommendation("Esper Sentinel", ("W",), ("card_draw",), "Taxes opponents for noncreature spells.", ("spellslinger",)),
    Recommendation("Anointed Procession", ("W",), ("tokens",), "Token doubler for white decks."),
    Recommendation("Parallel Lives", ("G",), ("tokens",), "Token doubler for green shells."),
    Recommendation("Mondrak, Glory Dominus", ("W",), ("tokens",), "Doubles tokens and becomes indestructible."),
    Recommendation("Chatterfang, Squirrel General", ("B", "G"), ("tokens", "sacrifice"), "Turns tokens into sacrifice fodder."),
    Recommendation("Divine Visitation", ("W",), ("tokens",), "Upgrades tokens into 4/4 angels."),
    Recommendation("Academy Manufactor", (), ("tokens", "artifacts"), "Triples Clue/Food/Treasure production."),
    Recommendation("Metallurgic Summonings", ("U",), ("spellslinger", "tokens"), "Creates construct tokens from spells."),
    Recommendation("Archmage Emeritus", ("U",), ("spellslinger", "card_draw"), "Draw engine for casting spells."),
    Recommendation("Thousand-Year Storm", ("U", "R"), ("spellslinger",), "Storm-like payoff for spellslinger decks."),
    Recommendation("Baral, Chief of Compliance", ("U",), ("spellslinger",), "Discount plus loot for counterspells."),
    Recommendation("Young Pyromancer", ("R",), ("spellslinger", "tokens"), "Creates tokens from instants and sorceries."),
    Recommendation("Aetherflux Reservoir", (), ("lifegain", "spellslinger"), "Payoff for chaining spells and lifegain."),
    Recommendation("Heliod, Sun-Crowned", ("W",), ("lifegain", "+1/+1 counters"), "Combines lifegain with counters."),
    Recommendation("Vito, Thorn of the Dusk Rose", ("B",), ("lifegain",), "Turns life gain into life drain."),
    Recommendation("Righteous Valkyrie", ("W",), ("lifegain",), "Rewards life gain with anthem effects."),
    Recommendation("Authority of the Consuls", ("W",), ("lifegain",), "Slows opponents and gains life."),
    Recommendation("Eternal Witness", ("G",), ("graveyard", "card_draw"), "Recurs any card from the graveyard."),
    Recommendation("Life from the Loam", ("G",), ("graveyard", "ramp"), "Dredge engine to recur lands."),
    Recommendation("Meren of Clan Nel Toth", ("B", "G"), ("graveyard", "sacrifice"), "Reanimation engine for creature decks."),
    Recommendation("Victimize", ("B",), ("graveyard",), "Two-creature reanimate spell."),
    Recommendation("Living Death", ("B",), ("graveyard", "board_wipe"), "Mass reanimation plus board reset."),
    Recommendation("Reanimate", ("B",), ("graveyard",), "One mana reanimate for big targets."),
    Recommendation("Sythis, Harvest's Hand", ("G", "W"), ("enchantments", "card_draw"), "Draw engine for enchantment shells."),
    Recommendation("Sram, Senior Edificer", ("W",), ("enchantments", "card_draw"), "Draws off Auras and Equipment."),
    Recommendation("Setessan Champion", ("G",), ("enchantments", "+1/+1 counters"), "Constellation draw engine that grows."),
    Recommendation("Sanctum Weaver", ("G",), ("enchantments", "ramp"), "Scales mana with enchantments."),
    Recommendation("Hall of Heliod's Generosity", ("W",), ("enchantments",), "Recurs enchantments from the yard."),
    Recommendation("Darksteel Forge", (), ("artifacts",), "Protects artifact boards."),
    Recommendation("Urza, Lord High Artificer", ("U",), ("artifacts", "ramp"), "Turns artifacts into mana and threats."),
    Recommendation("Sai, Master Thopterist", ("U",), ("artifacts", "tokens"), "Generates tokens from artifacts."),
    Recommendation("Steel Overseer", (), ("artifacts", "+1/+1 counters"), "Grows artifact creatures every turn."),
    Recommendation("Forsaken Monument", (), ("artifacts", "ramp"), "Supercharges colorless strategies."),
)


def _color_identity_label(colors: Iterable[str]) -> Tuple[str, str]:
    letters = [c for c in WUBRG_ORDER if c in {ch.upper() for ch in colors}]
    key = frozenset(letters)
    return "".join(letters), CI_NAME_BY_SET.get(key, "".join(letters) or "Colorless")


def _match_type_label(type_line: str) -> Optional[str]:
    lowered = str(type_line or "").lower()
    for label, keyword in TYPE_BUCKET_ORDER:
        if keyword in lowered:
            return label
    if "tribal" in lowered:
        return "Tribal"
    return None


def _primary_type_bucket(
    type_line: str,
    *,
    layout: Optional[str] = None,
    card_faces: Optional[Iterable[Dict[str, Any]]] = None,
) -> str:
    layout_key = (layout or "").lower()
    faces: List[Dict[str, Any]] = list(card_faces or [])

    # Modal DFCs (spell/land cards) should count as lands only when at least one face is a land.
    if layout_key == "modal_dfc":
        front_line = faces[0].get("type_line") if faces else ""
        back_line = faces[1].get("type_line") if len(faces) > 1 else ""
        if _match_type_label(front_line) == "Land" or _match_type_label(back_line) == "Land":
            return "Land"
        # Fallback when face data is missing: inspect the combined type line segments.
        if "//" in str(type_line or ""):
            parts = [p.strip() for p in str(type_line or "").split("//", 1)]
            if any(_match_type_label(p) == "Land" for p in parts):
                return "Land"
            for p in parts:
                lbl = _match_type_label(p)
                if lbl:
                    return lbl
        # If we couldn't classify as land, prefer the first face label.
        label = _match_type_label(front_line)
        if label:
            return label
        label = _match_type_label(back_line)
        if label:
            return label

    # Split the combined type line (e.g., "Instant // Sorcery") and prefer the first face.
    if "//" in str(type_line or ""):
        front_type = str(type_line or "").split("//", 1)[0].strip()
        label = _match_type_label(front_type)
        if label:
            return label

    label = _match_type_label(type_line)
    if label:
        return label

    if "tribal" in str(type_line or "").lower():
        return "Tribal"

    return "Other"


@lru_cache(maxsize=8192)
def _hover_image_for_card_name(name: str) -> Optional[str]:
    if not name:
        return None
    oracle_id = unique_oracle_by_name(name)
    if not oracle_id:
        return None
    try:
        prints = prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    for pr in prints:
        img = image_for_print(pr)
        src = img.get("large") or img.get("normal") or img.get("small")
        if src:
            return src
    return None


@lru_cache(maxsize=8192)
def _card_color_identity_letters(name: str) -> Optional[Set[str]]:
    if not name:
        return None
    oracle_id = unique_oracle_by_name(name)
    if not oracle_id:
        simplified = name.replace("\u2019", "'").replace("\u2013", "-")
        if simplified != name:
            oracle_id = unique_oracle_by_name(simplified)
    if not oracle_id and "//" in name:
        oracle_id = unique_oracle_by_name(name.split("//")[0].strip())
    if not oracle_id:
        alt = name.replace("-", " ")
        if alt != name:
            oracle_id = unique_oracle_by_name(alt)
    if not oracle_id:
        return None
    try:
        prints = prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    for pr in prints:
        colors = pr.get("color_identity")
        if colors:
            return {str(c).upper() for c in colors if isinstance(c, str)}
    return set()


def _view_matches_color_identity(view: CardView, allowed_colors: Set[str]) -> bool:
    identity = _card_color_identity_letters(view.name)
    if identity is None:
        return False
    if not identity:
        return True
    return identity.issubset(allowed_colors)


def _extract_keywords(print_obj: Dict[str, Any]) -> List[str]:
    if not isinstance(print_obj, dict):
        return []
    raw_keywords = print_obj.get("keywords")
    if not isinstance(raw_keywords, (list, tuple)):
        return []
    seen: Set[str] = set()
    collected: List[str] = []
    for item in raw_keywords:
        if not isinstance(item, str):
            continue
        keyword = item.strip()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        collected.append(keyword)
    return collected


TYPE_LINE_SEPARATOR = "\u2014"


def _parse_typal(type_line: str) -> List[str]:
    text = str(type_line or "")
    if not text:
        return []
    leading, sep, trailing = text.partition(TYPE_LINE_SEPARATOR)
    if not sep:
        return []
    heading = leading.lower()
    if "creature" not in heading and "tribal" not in heading:
        return []
    tokens = [token.strip() for token in trailing.replace("/", " ").split() if token.strip()]
    seen: Set[str] = set()
    ordered: List[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _scryfall_search_url(name: str) -> str:
    return f"https://scryfall.com/search?q=%21{quote_plus(name)}"


def _joined_oracle_text(print_obj: Dict[str, Any]) -> str:
    if not print_obj:
        return ""
    texts: List[str] = []
    base = str(print_obj.get("oracle_text") or "")
    if base:
        texts.append(base)
    for face in print_obj.get("card_faces") or []:
        face_text = str(face.get("oracle_text") or "")
        if face_text:
            texts.append(face_text)
    return " ".join(texts)


def _resolve_print(card: Card) -> Dict[str, Any]:
    pr: Dict[str, Any] = {}
    if getattr(card, "oracle_id", None):
        try:
            alts = prints_for_oracle(card.oracle_id) or []
        except Exception:
            alts = []
        if alts:
            pr = alts[0]
            for candidate in alts:
                if (
                    str(candidate.get("set") or "").lower() == str(card.set_code or "").lower()
                    and str(candidate.get("collector_number") or "").lower() == str(card.collector_number or "").lower()
                ):
                    pr = candidate
                    break
    if not pr:
        try:
            pr = find_by_set_cn(card.set_code, card.collector_number, card.name) or {}
        except Exception:
            pr = {}
    return pr or {}


def classify_roles(oracle_text: str, type_line: str) -> Set[str]:
    roles: Set[str] = set()
    txt = oracle_text.lower()
    tline = type_line.lower()
    is_land = "land" in tline
    if (
        ROLE_PATTERNS["destroy_all"].search(txt)
        or ROLE_PATTERNS["exile_all"].search(txt)
        or ROLE_PATTERNS["mass_return"].search(txt)
        or ROLE_PATTERNS["sacrifice_all"].search(txt)
        or ROLE_PATTERNS["sacrifices_all"].search(txt)
        or (ROLE_PATTERNS["each_creature"].search(txt) and any(keyword in txt for keyword in ("damage", "loses", "gets")))
    ):
        roles.add("board_wipe")
        return roles
    adds_any_color = bool(ROLE_PATTERNS["adds_any_color"].search(txt))
    adds_mana = bool(ROLE_PATTERNS["adds_mana"].search(txt)) or adds_any_color
    if adds_mana or (ROLE_PATTERNS["search_land"].search(txt) and "nonland" not in txt):
        if not is_land or "search" in txt or "untap" in txt:
            roles.add("ramp")
    if adds_any_color:
        if "mana of any color" in txt or "combination of colors" in txt or "color identity" in txt:
            roles.add("mana_fixing")
        elif ROLE_PATTERNS["choose_color"].search(txt) and ("mana" in txt or "land" in txt):
            roles.add("mana_fixing")
    if ROLE_PATTERNS["draw_card"].search(txt):
        roles.add("card_draw")
    if ROLE_PATTERNS["scry"].search(txt) or ROLE_PATTERNS["surveil"].search(txt):
        roles.add("card_selection")
    elif ROLE_PATTERNS["look_top"].search(txt) and "draw" not in txt:
        roles.add("card_selection")
    if (
        ROLE_PATTERNS["destroy_target"].search(txt)
        or ROLE_PATTERNS["exile_target"].search(txt)
        or ROLE_PATTERNS["counter_target"].search(txt)
        or ROLE_PATTERNS["return_target"].search(txt)
    ):
        roles.add("interaction")
    if ROLE_PATTERNS["search_library"].search(txt) or ROLE_PATTERNS["reveal_from_library"].search(txt):
        if "basic land" not in txt:
            roles.add("tutor")
    if ROLE_PATTERNS["return_from_grave"].search(txt) or ROLE_PATTERNS["regrowth_effect"].search(txt):
        roles.add("recursion")
    if (
        ROLE_PATTERNS["grant_indestructible"].search(txt)
        or ROLE_PATTERNS["grant_hexproof"].search(txt)
        or ROLE_PATTERNS["phase_out"].search(txt)
        or ROLE_PATTERNS["prevent_damage"].search(txt)
    ):
        roles.add("protection")
    if (
        ROLE_PATTERNS["each_opponent_loses"].search(txt)
        or ROLE_PATTERNS["you_win_game"].search(txt)
        or ROLE_PATTERNS["extra_turn"].search(txt)
    ):
        roles.add("finisher")
    if (
        ROLE_PATTERNS["tax_cost"].search(txt)
        or ROLE_PATTERNS["tax_attack"].search(txt)
        or ROLE_PATTERNS["tax_untap"].search(txt)
        or ROLE_PATTERNS["tax_opponent_cant"].search(txt)
        or ROLE_PATTERNS["tax_unless_pays"].search(txt)
    ):
        roles.add("stax")
    return roles


def detect_themes_for_text(oracle_text: str, type_line: str) -> Set[str]:
    hits: Set[str] = set()
    blob = f"{type_line}\n{oracle_text}".lower()
    for key, meta in THEME_DEFINITIONS.items():
        for pattern in meta["patterns"]:
            if pattern.search(blob):
                hits.add(key)
                break
    return hits


def _sort_cardviews(views: Iterable[CardView]) -> List[CardView]:
    return sorted(
        views,
        key=lambda v: (
            v.synergy is None,
            -(v.synergy or 0.0),
            -(v.inclusion or 0.0),
            v.rank,
        ),
    )


def _prepare_cardviews(views: Iterable[CardView]) -> List[CardView]:
    merged = merge_cardviews(views)
    return _sort_cardviews(merged.values())


def _cardview_to_payload(
    view: CardView,
    *,
    already_in_deck: bool,
    roles: Optional[Iterable[str]] = None,
    deck_card_id: Optional[int] = None,
    deck_colors: Optional[Iterable[str]] = None,
    owned_slugs: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    payload = view.to_dict()
    payload["already_in_deck"] = already_in_deck
    payload["synergy_percent"] = int(round(view.synergy * 100)) if view.synergy is not None else None
    payload["hover_image"] = _hover_image_for_card_name(view.name)
    if deck_card_id:
        payload["card_id"] = deck_card_id
    if roles:
        sorted_roles = sorted({r for r in roles})
        payload["roles"] = sorted_roles
        payload["role_labels"] = [
            ROLE_CONFIG.get(role, {}).get("label") or role.replace("_", " ").title()
            for role in sorted_roles
        ]
    identity = _card_color_identity_letters(view.name)
    if identity is None:
        payload["color_identity_known"] = False
        payload["color_identity"] = []
    else:
        payload["color_identity_known"] = True
        payload["color_identity"] = sorted(identity)
    if identity is None:
        payload["color_identity_html"] = None
    else:
        if identity:
            mana_text = "".join(f"{{{c}}}" for c in sorted(identity))
        else:
            mana_text = "{C}"
        payload["color_identity_html"] = render_mana_html(mana_text, use_local=True)
    if deck_colors is not None:
        commander_colors = {str(c).upper() for c in deck_colors if c}
        matches = identity is not None and identity.issubset(commander_colors)
        payload["matches_deck_colors"] = matches
    slug = normalize_card_key(view.name)
    if slug in BASIC_LAND_SLUGS:
        payload["owned"] = True
    else:
        payload["owned"] = bool(owned_slugs and slug in owned_slugs)
    return payload


def _placeholder_tag_synergy(
    tag_value: Optional[str],
    canonical: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    label_source = canonical or tag_value
    label = (label_source or "Unknown Tag").strip() or "Unknown Tag"
    note = message or f"Curated recommendations for {label} are coming soon."
    bands = [dict(band) for band in SYNERGY_GRADE_BANDS]
    alerts = [note]
    if message and message != note:
        alerts.append(message)
    return {
        "tag": tag_value,
        "canonical_tag": canonical,
        "label": label,
        "category": TAG_CATEGORY_MAP.get(label_source) if label_source else None,
        "description": note,
        "score": 0,
        "grade": "N/A",
        "status_text": note,
        "confidence": None,
        "explanation": {
            "score": {
                "present_count": 0,
                "eligible_count": 0,
                "note": "Score will appear once a curated core card list is defined.",
            },
            "grade": {
                "current": {"grade": "N/A", "description": note},
                "bands": bands,
            },
            "confidence": {
                "value": None,
                "alignment": 0,
                "expectations": 0,
                "note": "Confidence requires theme minimums supplied by a curated package.",
            },
        },
        "core": {
            "present": [],
            "missing": [],
            "missing_count": 0,
            "present_weight": 0,
            "total_weight": 0,
            "eligible_count": 0,
            "present_count": 0,
        },
        "support": {"recommendations": [], "additional_available": 0},
        "themes": [],
        "theme_alignment": 0,
        "theme_expectations": 0,
        "alerts": alerts,
    }


def _theme_tag_fallback(
    *,
    deck_tag: Optional[str],
    canonical: Optional[str],
    deck_color_set: Set[str],
    present_lower: Set[str],
    theme_counts: Dict[str, int],
    present_card_ids: Optional[Dict[str, int]],
    core_limit: int,
    support_limit: int,
) -> Dict[str, Any]:
    payload = None
    error = None
    slug = None
    for candidate in filter(None, [canonical, deck_tag]):
        slug, payload, error = ensure_theme_data(candidate)
        if payload:
            break
    if not payload:
        return _placeholder_tag_synergy(deck_tag, canonical, error)

    views = _prepare_cardviews(theme_cardviews(payload))
    if not views:
        return _placeholder_tag_synergy(deck_tag, canonical, "No EDHREC recommendations available for this tag yet.")

    core_limit = core_limit or 6
    support_limit = support_limit or 6

    def _in_deck(view: CardView) -> bool:
        slug_lower = view.slug or ""
        name_lower = view.name.lower()
        return slug_lower in present_lower or name_lower in present_lower

    filtered_views = [view for view in views if _view_matches_color_identity(view, deck_color_set)]
    if not filtered_views:
        return _placeholder_tag_synergy(
            deck_tag,
            canonical,
            "EDHREC suggestions are currently off-color for this commander.",
        )

    core_views = filtered_views[:core_limit]
    present_core_entries: List[Dict[str, Any]] = []
    missing_core_entries: List[Dict[str, Any]] = []
    present_core_count = 0
    for view in core_views:
        in_deck = _in_deck(view)
        if in_deck:
            present_core_count += 1
        card_id = None
        if present_card_ids:
            card_id = present_card_ids.get(view.slug) or present_card_ids.get(view.name.lower())
        payload_entry = _cardview_to_payload(
            view,
            already_in_deck=in_deck,
            roles=_roles_for_card_name(view.name),
            deck_card_id=card_id,
            deck_colors=deck_color_set,
            owned_slugs=None,
        )
        (present_core_entries if in_deck else missing_core_entries).append(payload_entry)

    remaining_views = filtered_views[core_limit:]
    support_recommendations: List[Dict[str, Any]] = []
    for view in remaining_views:
        if _in_deck(view):
            continue
        card_id = None
        if present_card_ids:
            card_id = present_card_ids.get(view.slug) or present_card_ids.get(view.name.lower())
        support_recommendations.append(
            _cardview_to_payload(
                view,
                already_in_deck=False,
                roles=_roles_for_card_name(view.name),
                deck_card_id=card_id,
                deck_colors=deck_color_set,
                owned_slugs=None,
            )
        )
        if len(support_recommendations) >= support_limit:
            break

    cores_available = len(core_views)
    score = int(round(present_core_count * 100 / cores_available)) if cores_available else 0
    grade, status_text = _grade_for_synergy_score(score)

    label = payload.get("name") or (canonical or deck_tag) or "Unknown Tag"
    fallback_description = (
        payload.get("description")
        or (payload.get("metadata") or {}).get("description")
        or f"Showing top EDHREC picks for {label}."
    )
    alerts = ["Using EDHREC theme data while curated recommendations are prepared."]
    if error:
        alerts.append(error)

    bands = [dict(band) for band in SYNERGY_GRADE_BANDS]
    additional_available = max(
        0,
        len([v for v in remaining_views if not _in_deck(v)]) - len(support_recommendations),
    )

    return {
        "tag": deck_tag,
        "canonical_tag": canonical or slug,
        "label": label,
        "category": TAG_CATEGORY_MAP.get(canonical or deck_tag),
        "description": fallback_description,
        "score": score,
        "grade": grade,
        "status_text": status_text,
        "confidence": None,
        "explanation": {
            "score": {
                "present_count": present_core_count,
                "eligible_count": cores_available,
                "note": "Score is based on how many top EDHREC theme cards are already in your deck.",
            },
            "grade": {
                "current": {"grade": grade, "description": status_text},
                "bands": bands,
            },
            "confidence": {
                "value": None,
                "alignment": 0,
                "expectations": 0,
                "note": "Confidence requires curated theme minimums.",
            },
        },
        "core": {
            "present": present_core_entries,
            "missing": missing_core_entries[:core_limit],
            "missing_count": len(missing_core_entries),
            "present_weight": present_core_count,
            "total_weight": cores_available,
            "eligible_count": cores_available,
            "present_count": present_core_count,
        },
        "support": {
            "recommendations": support_recommendations,
            "additional_available": additional_available,
        },
        "themes": [],
        "theme_alignment": 0,
        "theme_expectations": 0,
        "alerts": alerts,
    }


def _average(values: Iterable[Optional[float]]) -> Optional[float]:
    total = 0.0
    count = 0
    for value in values:
        if value is None:
            continue
        total += value
        count += 1
    if not count:
        return None
    return total / count


@lru_cache(maxsize=8192)
def _roles_for_card_name(name: str) -> Set[str]:
    if not name:
        return set()
    oracle_id = unique_oracle_by_name(name)
    if not oracle_id:
        return set()
    try:
        prints = prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    pr = prints[0] if prints else {}
    oracle_text = _joined_oracle_text(pr)
    type_line = pr.get("type_line") or ""
    return classify_roles(oracle_text, type_line)


def _gather_recommendations(
    role: str,
    deck_colors: Set[str],
    present_names: Set[str],
    present_card_ids: Optional[Dict[str, int]] = None,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    missing: List[Dict[str, Any]] = []
    present: List[Dict[str, Any]] = []
    for rec in RECOMMENDATIONS:
        if role not in rec.roles:
            continue
        if not rec.matches_colors(deck_colors):
            continue
        payload = {
            "name": rec.name,
            "notes": rec.notes,
            "colors": list(rec.colors),
            "themes": list(rec.themes),
            "already_in_deck": rec.name.lower() in present_names,
            "scryfall_url": _scryfall_search_url(rec.name),
            "hover_image": _hover_image_for_card_name(rec.name),
        }
        if present_card_ids:
            card_id = present_card_ids.get(rec.name.lower())
            if card_id:
                payload["card_id"] = card_id
        (present if payload["already_in_deck"] else missing).append(payload)
    result = missing[:limit]
    if len(result) < limit:
        result.extend(present[: limit - len(result)])
    return result


def _recommend_for_theme(
    theme: str,
    deck_colors: Set[str],
    present_names: Set[str],
    present_card_ids: Optional[Dict[str, int]] = None,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    missing: List[Dict[str, Any]] = []
    present: List[Dict[str, Any]] = []
    for rec in RECOMMENDATIONS:
        if theme not in rec.themes:
            continue
        if not rec.matches_colors(deck_colors):
            continue
        payload = {
            "name": rec.name,
            "notes": rec.notes,
            "colors": list(rec.colors),
            "roles": list(rec.roles),
            "already_in_deck": rec.name.lower() in present_names,
            "scryfall_url": _scryfall_search_url(rec.name),
            "hover_image": _hover_image_for_card_name(rec.name),
        }
        if present_card_ids:
            card_id = present_card_ids.get(rec.name.lower())
            if card_id:
                payload["card_id"] = card_id
        (present if payload["already_in_deck"] else missing).append(payload)
    result = missing[:limit]
    if len(result) < limit:
        result.extend(present[: limit - len(result)])
    return result


def _commander_payload(folder: Folder, cards: Iterable[Card]) -> Optional[Dict[str, Any]]:
    raw_id = getattr(folder, "commander_oracle_id", None)
    display_name = getattr(folder, "commander_name", None)
    lookup_name = primary_commander_name(display_name) or display_name
    cmdr_id = primary_commander_oracle_id(raw_id)
    if not cmdr_id and not lookup_name:
        return None
    pr: Dict[str, Any] = {}
    if cmdr_id:
        try:
            alts = prints_for_oracle(cmdr_id) or []
        except Exception:
            alts = []
        if alts:
            pr = alts[0]
    if not pr and lookup_name:
        return {"name": display_name or lookup_name}
    if not pr:
        return None
    images = image_for_print(pr) if pr else {}
    lookup_key = str(pr.get("name") or lookup_name or "").strip().lower()
    commander_card: Optional[Card] = None
    for card in cards:
        if card.name and card.name.strip().lower() == lookup_key:
            commander_card = card
            break
    return {
        "name": display_name or pr.get("name") or lookup_name,
        "type_line": pr.get("type_line"),
        "image": images.get("small") or images.get("normal"),
        "card_id": getattr(commander_card, "id", None),
        "scryfall_url": pr.get("scryfall_uri") or _scryfall_search_url(pr.get("name") or lookup_name or ""),
    }


def analyze_deck(folder_id: int) -> Dict[str, Any]:
    folder = Folder.query.get(folder_id)
    if not folder:
        raise ValueError(f"Folder id {folder_id} not found")

    cards: List[Card] = (
        Card.query.filter(Card.folder_id == folder_id).order_by(func.lower(Card.name)).all()
    )

    ensure_cache_loaded()

    deck_colors: Set[str] = set()
    total_cards = 0
    role_counts: Counter[str] = Counter()
    theme_tallies: Dict[str, List[str]] = defaultdict(list)
    deck_type_counts: Dict[str, int] = defaultdict(int)
    present_names = {c.name.lower() for c in cards}
    present_slugs = {normalize_card_key(c.name) for c in cards if getattr(c, "name", None)}
    analysis_cards: List[Dict[str, Any]] = []

    for card in cards:
        qty = int(card.quantity or 0) or 1
        total_cards += qty
        pr = _resolve_print(card)
        layout = str((pr or {}).get("layout") or "")
        card_faces = (pr or {}).get("card_faces") or []
        type_line = str(pr.get("type_line") or "")
        oracle_text = _joined_oracle_text(pr)
        images = image_for_print(pr) if pr else {}
        hover_image = images.get("large") or images.get("normal") or images.get("small")
        if not hover_image:
            hover_image = _hover_image_for_card_name(card.name)
        colors = pr.get("color_identity") or pr.get("colors") or []
        for c in colors:
            uc = str(c).upper()
            if uc in WUBRG_ORDER:
                deck_colors.add(uc)

        primary_type = _primary_type_bucket(type_line, layout=layout, card_faces=card_faces)
        deck_type_counts[primary_type] += qty

        roles = classify_roles(oracle_text, type_line)
        for role in roles:
            role_counts[role] += qty

        themes = detect_themes_for_text(oracle_text, type_line)
        for theme in themes:
            theme_tallies[theme].append(card.name)

        prices = prices_for_print(pr)
        price_text = format_price_text(prices)

        mana_cost = pr.get("mana_cost")
        if not mana_cost and card_faces:
            mana_cost = card_faces[0].get("mana_cost")
        mana_cost_str = str(mana_cost).strip() if mana_cost else None
        mana_cost_html = (
            render_mana_html(mana_cost_str, use_local=True) if mana_cost_str else None
        )

        raw_mana_value = pr.get("cmc")
        if raw_mana_value is None:
            raw_mana_value = pr.get("mana_value")
        try:
            mana_value = float(raw_mana_value)
        except (TypeError, ValueError):
            mana_value = None

        analysis_cards.append(
            {
                "name": card.name,
                "card_id": getattr(card, "id", None),
                "quantity": qty,
                "primary_type": primary_type,
                "layout": layout,
                "type_faces": [
                    {
                        "name": face.get("name"),
                        "type_line": face.get("type_line"),
                        "mana_cost": face.get("mana_cost"),
                        "oracle_text": face.get("oracle_text"),
                    }
                    for face in card_faces
                ]
                if card_faces
                else None,
                "roles": sorted(roles),
                "themes": sorted(themes),
                "type_line": type_line,
                "oracle_text": oracle_text,
                "hover_image": hover_image,
                "keywords": _extract_keywords(pr),
                "typal": _parse_typal(type_line),
                "prices": prices,
                "price_text": price_text,
                "mana_cost": mana_cost_str,
                "mana_cost_html": mana_cost_html,
                "mana_value": mana_value,
            }
        )

    analysis_card_map = {
        entry["name"].lower(): entry
        for entry in analysis_cards
        if entry.get("name")
    }

    owned_card_slugs: Set[str] = {
        normalize_card_key(name)
        for (name,) in db.session.query(Card.name).distinct()
        if name
    }
    basic_slugs = {normalize_card_key(name) for name in BASIC_LANDS}
    owned_card_slugs.update(basic_slugs)

    color_letters, color_label = _color_identity_label(deck_colors)
    color_html = None
    if color_letters:
        mana_text = "".join(f"{{{c}}}" for c in color_letters)
        color_html = render_mana_html(mana_text, use_local=True)
    elif not deck_colors:
        color_html = render_mana_html("{C}", use_local=True)
    theme_counts = {key: len(values) for key, values in theme_tallies.items()}
    present_card_ids_map = {
        card.name.lower(): card.id
        for card in cards
        if getattr(card, "id", None) is not None
    }
    tag_synergy = calculate_tag_synergy(
        getattr(folder, "deck_tag", None),
        deck_colors,
        present_names,
        theme_counts,
        present_card_ids=present_card_ids_map,
    )
    tag_value = getattr(folder, "deck_tag", None)
    tag_category = TAG_CATEGORY_MAP.get(tag_value) if tag_value else None
    tag_label = f"{tag_category}: {tag_value}" if tag_category else tag_value
    theme_slug: Optional[str] = slugify_theme(tag_value) if tag_value else None

    role_summaries: List[Dict[str, Any]] = []
    for role_key, meta in ROLE_CONFIG.items():
        current = int(role_counts.get(role_key, 0))
        role_summaries.append(
            {
                "key": role_key,
                "label": meta["label"],
                "current": current,
                "target": None,
                "status": None,
                "recommendations": _gather_recommendations(role_key, deck_colors, present_names, present_card_ids_map),
                "edhrec_recommendations": [],
            }
        )

    analysis_card_lookup: Dict[int, Dict[str, Any]] = {}
    role_card_map: Dict[str, List[Dict[str, Any]]] = {}
    for entry in analysis_cards:
        card_id = entry.get("card_id")
        if not card_id:
            continue
        try:
            card_id_int = int(card_id)
        except (TypeError, ValueError):
            continue
        analysis_card_lookup[card_id_int] = entry
        role_keys = [str(role_key) for role_key in (entry.get("roles") or []) if role_key]
        if not role_keys:
            continue

        role_labels_display: List[str] = []
        for key in role_keys:
            label = ROLE_CONFIG.get(key, {}).get("label") or key.replace("_", " ").title()
            if label and label not in role_labels_display:
                role_labels_display.append(label)

        theme_labels_display: List[str] = []
        theme_keys = entry.get("themes") or []
        for theme_key in theme_keys:
            meta = THEME_DEFINITIONS.get(theme_key, {})
            theme_label = meta.get("label") or str(theme_key).replace("_", " ").title()
            if theme_label and theme_label not in theme_labels_display:
                theme_labels_display.append(theme_label)

        primary_type_label = entry.get("primary_type")
        type_line = (
            entry.get("type_line")
            or entry.get("oracle_type_line")
            or entry.get("printed_type_line")
            or primary_type_label
        )
        keywords = list(entry.get("keywords") or [])
        typal = list(entry.get("typal") or [])

        categories: List[str] = []
        categories.extend(role_labels_display)
        for label in theme_labels_display:
            if label not in categories:
                categories.append(label)
        if not role_labels_display and not theme_labels_display:
            for keyword in keywords[:3]:
                if keyword and keyword not in categories:
                    categories.append(keyword)
            for tribe in typal[:3]:
                if tribe and tribe not in categories:
                    categories.append(tribe)
        if primary_type_label and primary_type_label not in categories:
            categories.append(primary_type_label)

        for role_key in role_keys:
            payload: Dict[str, Any] = {
                "id": card_id_int,
                "name": entry.get("name"),
                "quantity": int(entry.get("quantity") or 0) or 1,
                "hover_image": entry.get("hover_image"),
                "edhrec": entry.get("edhrec") or {},
                "type_line": type_line,
                "role_labels": list(role_labels_display),
                "theme_labels": list(theme_labels_display),
                "categories": categories,
                "color_identity_html": entry.get("color_identity_html"),
                "color_identity": entry.get("color_identity"),
                "prices": entry.get("prices"),
                "price_text": entry.get("price_text"),
            }
            best_synergy = entry.get("best_synergy")
            try:
                payload["best_synergy_percent"] = (
                    int(round(float(best_synergy))) if best_synergy is not None else None
                )
            except (TypeError, ValueError):
                payload["best_synergy_percent"] = None
            role_card_map.setdefault(role_key, []).append(payload)

    for role_entry in role_summaries:
        role_cards = role_card_map.get(role_entry["key"], [])
        role_cards.sort(key=lambda item: ((item.get("name") or "").lower(), item.get("id") or 0))
        role_entry["cards"] = role_cards

    edhrec_errors: List[str] = []
    theme_views: List[CardView] = []
    theme_payload: Optional[Dict[str, Any]] = None
    theme_error: Optional[str] = None
    if tag_value:
        ensured_slug, theme_payload, theme_error = ensure_theme_data(tag_value)
        if ensured_slug:
            theme_slug = ensured_slug
        if theme_error and not theme_payload:
            edhrec_errors.append(theme_error)
        if theme_payload:
            theme_views = theme_cardviews(theme_payload)
    else:
        theme_error = None

    commander_name = getattr(folder, "commander_name", None)
    if not commander_name and getattr(folder, "commander_oracle_id", None):
        primary_oid = primary_commander_oracle_id(folder.commander_oracle_id)
        commander_candidates = []
        if primary_oid:
            try:
                commander_candidates = prints_for_oracle(primary_oid) or []
            except Exception:
                commander_candidates = []
        if commander_candidates:
            commander_name = commander_candidates[0].get("name")
    commander_slug: Optional[str] = None
    commander_payload: Optional[Dict[str, Any]] = None
    commander_error: Optional[str] = None
    commander_views: List[CardView] = []
    if commander_name:
        commander_slug, commander_payload, commander_error = ensure_commander_data(
            commander_name,
            theme_slug=theme_slug,
        )
        if not commander_payload and commander_error and theme_slug:
            fallback_slug, fallback_payload, fallback_error = ensure_commander_data(commander_name, theme_slug=None)
            if fallback_payload:
                display_tag = tag_label or tag_value or "selected tag"
                commander_slug = fallback_slug
                commander_payload = fallback_payload
                commander_error = None
                edhrec_errors.append(
                    f"Could not load EDHREC data filtered by '{display_tag}'; showing default commander data instead."
                )
            else:
                commander_error = commander_error or fallback_error
        if commander_payload and "theme_options" not in commander_payload:
            refresh_theme = (commander_payload.get("applied_theme") or theme_slug) or None
            refreshed_slug, refreshed_payload, refreshed_error = ensure_commander_data(
                commander_name,
                theme_slug=refresh_theme,
                force_refresh=True,
            )
            if refreshed_payload:
                commander_payload = refreshed_payload
                commander_slug = refreshed_slug or commander_slug
                if refreshed_error:
                    edhrec_errors.append(refreshed_error)
            elif refreshed_error:
                edhrec_errors.append(refreshed_error)
        if commander_error and not commander_payload:
            edhrec_errors.append(commander_error)
        if commander_payload:
            commander_views = commander_cardviews(commander_payload)
    else:
        commander_error = "Commander not set for this deck."

    def _within_deck_colors(view: CardView) -> bool:
        return _view_matches_color_identity(view, deck_colors)

    commander_present_views = _prepare_cardviews([view for view in commander_views if view.slug in present_slugs])
    commander_missing_candidates: List[CardView] = []
    commander_missing_filtered: List[CardView] = []
    for view in commander_views:
        if view.slug in present_slugs:
            continue
        commander_missing_candidates.append(view)
        if _within_deck_colors(view):
            commander_missing_filtered.append(view)
    commander_missing_views = _prepare_cardviews(commander_missing_filtered)

    theme_present_views = _prepare_cardviews([view for view in theme_views if view.slug in present_slugs])
    theme_missing_candidates: List[CardView] = []
    theme_missing_filtered: List[CardView] = []
    for view in theme_views:
        if view.slug in present_slugs:
            continue
        theme_missing_candidates.append(view)
        if _within_deck_colors(view):
            theme_missing_filtered.append(view)
    theme_missing_views = _prepare_cardviews(theme_missing_filtered)

    combined_lookup = merge_cardviews(commander_views, theme_views)
    combined_present_views = _sort_cardviews(
        [view for view in combined_lookup.values() if view.slug in present_slugs]
    )
    combined_missing_candidates = [view for view in combined_lookup.values() if view.slug not in present_slugs]
    combined_missing_filtered = [view for view in combined_missing_candidates if _within_deck_colors(view)]
    combined_missing_views = _sort_cardviews(combined_missing_filtered)

    commander_considered = len({view.slug for view in commander_missing_candidates})
    commander_filtered_out = max(0, commander_considered - len({view.slug for view in commander_missing_filtered}))
    theme_considered = len({view.slug for view in theme_missing_candidates})
    theme_filtered_out = max(0, theme_considered - len({view.slug for view in theme_missing_filtered}))
    combined_considered = len(combined_missing_candidates)
    combined_filtered_out = max(0, combined_considered - len(combined_missing_filtered))

    color_filter_stats = {
        "commander": {"considered": commander_considered, "filtered_out": commander_filtered_out},
        "theme": {"considered": theme_considered, "filtered_out": theme_filtered_out},
        "combined": {"considered": combined_considered, "filtered_out": combined_filtered_out},
    }

    deck_card_lookup = {
        normalize_card_key(item["name"]): item.get("card_id")
        for item in analysis_cards
        if item.get("card_id")
    }

    deck_role_index = {
        normalize_card_key(item["name"]): set(item.get("roles") or [])
        for item in analysis_cards
    }
    commander_lookup = {view.slug: view for view in commander_views}
    theme_lookup = {view.slug: view for view in theme_views}

    combined_lookup_map = dict(combined_lookup)
    for card_payload in analysis_cards:
        slug = normalize_card_key(card_payload["name"])
        roles_for_card = deck_role_index.get(slug)
        commander_view = commander_lookup.get(slug)
        theme_view = theme_lookup.get(slug)
        sources: Dict[str, Dict[str, Any]] = {}
        if commander_view:
            sources["commander"] = _cardview_to_payload(
                commander_view,
                already_in_deck=True,
                roles=roles_for_card,
                deck_card_id=deck_card_lookup.get(slug),
                deck_colors=deck_colors,
                owned_slugs=owned_card_slugs,
            )
        if theme_view:
            sources["theme"] = _cardview_to_payload(
                theme_view,
                already_in_deck=True,
                roles=roles_for_card,
                deck_card_id=deck_card_lookup.get(slug),
                deck_colors=deck_colors,
                owned_slugs=owned_card_slugs,
            )
        if sources:
            card_payload["edhrec_sources"] = sources
        view = combined_lookup_map.get(slug)
        if view:
            card_payload["edhrec"] = _cardview_to_payload(
                view,
                already_in_deck=True,
                roles=roles_for_card,
                deck_card_id=deck_card_lookup.get(slug),
                deck_colors=deck_colors,
                owned_slugs=owned_card_slugs,
            )

    def _synergy_sort_value(value: Optional[int]) -> int:
        if value is None:
            return -101
        return int(value)

    deck_card_rows: List[Dict[str, Any]] = []
    role_category_counter: Counter[str] = Counter()
    theme_category_counter: Counter[str] = Counter()
    fallback_category_counter: Counter[str] = Counter()
    cards_without_specific_category: List[str] = []

    for card_payload in analysis_cards:
        combined_payload = card_payload.get("edhrec") or {}
        sources_map = card_payload.get("edhrec_sources") or {}
        theme_source = sources_map.get("theme")
        commander_source = sources_map.get("commander")
        best_synergy = combined_payload.get("synergy_percent") if combined_payload else None
        quantity = int(card_payload.get("quantity") or 0) or 1

        role_keys = card_payload.get("roles") or []
        role_labels: List[str] = []
        for role_key in role_keys:
            label = ROLE_CONFIG.get(role_key, {}).get("label") or role_key.replace("_", " ").title()
            if label not in role_labels:
                role_labels.append(label)
                role_category_counter[label] += quantity

        theme_keys = card_payload.get("themes") or []
        theme_labels: List[str] = []
        for theme_key in theme_keys:
            meta = THEME_DEFINITIONS.get(theme_key, {})
            theme_label = meta.get("label") or theme_key.replace("_", " ").title()
            if theme_label not in theme_labels:
                theme_labels.append(theme_label)
                theme_category_counter[theme_label] += quantity

        categories: List[str] = []
        categories.extend(role_labels)
        for label in theme_labels:
            if label not in categories:
                categories.append(label)

        primary_type_label = card_payload.get("primary_type") or "Other"
        keywords = list(card_payload.get("keywords") or [])
        typal = list(card_payload.get("typal") or [])
        if not role_labels and not theme_labels:
            for keyword in keywords[:3]:
                if keyword and keyword not in categories:
                    categories.append(keyword)
            for tribe in typal[:3]:
                if tribe and tribe not in categories:
                    categories.append(tribe)
        if primary_type_label and primary_type_label not in categories:
            categories.append(primary_type_label)

        if not role_labels and not theme_labels:
            fallback_category_counter[primary_type_label] += quantity
            cards_without_specific_category.append(card_payload["name"])

        type_line = (
            card_payload.get("type_line")
            or card_payload.get("oracle_type_line")
            or card_payload.get("printed_type_line")
            or card_payload.get("type")
        )
        row = {
            "name": card_payload["name"],
            "quantity": card_payload.get("quantity"),
            "primary_type": card_payload.get("primary_type"),
            "card_id": card_payload.get("card_id"),
            "hover_image": card_payload.get("hover_image"),
            "layout": card_payload.get("layout"),
            "type_line": type_line,
            "color_identity_html": card_payload.get("color_identity_html"),
            "color_identity": card_payload.get("color_identity"),
            "roles": role_keys,
            "role_labels": role_labels,
            "theme_labels": theme_labels,
            "categories": categories,
            "keywords": list(card_payload.get("keywords") or []),
            "typal": list(card_payload.get("typal") or []),
            "edhrec": combined_payload or None,
            "theme_source": theme_source,
            "commander_source": commander_source,
            "best_synergy": best_synergy,
            "prices": card_payload.get("prices"),
            "price_text": card_payload.get("price_text"),
            "mana_cost": card_payload.get("mana_cost"),
            "mana_cost_html": card_payload.get("mana_cost_html"),
            "mana_value": card_payload.get("mana_value"),
        }
        deck_card_rows.append(row)
    deck_card_rows.sort(
        key=lambda row: (
            _synergy_sort_value((row["theme_source"] or {}).get("synergy_percent") if row["theme_source"] else None),
            _synergy_sort_value((row["commander_source"] or {}).get("synergy_percent") if row["commander_source"] else None),
            _synergy_sort_value(row["best_synergy"]),
            row["name"].lower(),
        ),
        reverse=True,
    )

    deck_category_summary = {
        "roles": [{"label": label, "count": count} for label, count in role_category_counter.most_common()],
        "themes": [{"label": label, "count": count} for label, count in theme_category_counter.most_common()],
        "fallback_types": [{"label": label, "count": count} for label, count in fallback_category_counter.most_common()],
        "cards_without_roles_or_themes": cards_without_specific_category,
    }

    present_synergy_values = [view.synergy for view in combined_present_views if view.synergy is not None]
    average_synergy = _average(present_synergy_values)

    roles_cache: Dict[str, Set[str]] = {}

    def _roles_for_view(view: CardView) -> Set[str]:
        slug = view.slug
        if slug in deck_role_index:
            return deck_role_index[slug]
        if slug not in roles_cache:
            roles_cache[slug] = _roles_for_card_name(view.name)
        return roles_cache[slug]

    commander_present_payload = [
        _cardview_to_payload(
            view,
            already_in_deck=True,
            roles=_roles_for_view(view),
            deck_card_id=deck_card_lookup.get(view.slug),
            deck_colors=deck_colors,
            owned_slugs=owned_card_slugs,
        )
        for view in commander_present_views[:EDHREC_PRESENT_LIMIT]
    ]
    commander_missing_payload = [
        _cardview_to_payload(
            view,
            already_in_deck=False,
            roles=_roles_for_view(view),
            deck_card_id=deck_card_lookup.get(view.slug),
            deck_colors=deck_colors,
            owned_slugs=owned_card_slugs,
        )
        for view in commander_missing_views[:EDHREC_MISSING_LIMIT]
    ]
    theme_present_payload = [
        _cardview_to_payload(
            view,
            already_in_deck=True,
            roles=_roles_for_view(view),
            deck_card_id=deck_card_lookup.get(view.slug),
            deck_colors=deck_colors,
            owned_slugs=owned_card_slugs,
        )
        for view in theme_present_views[:EDHREC_PRESENT_LIMIT]
    ]
    theme_missing_payload = [
        _cardview_to_payload(
            view,
            already_in_deck=False,
            roles=_roles_for_view(view),
            deck_card_id=deck_card_lookup.get(view.slug),
            deck_colors=deck_colors,
            owned_slugs=owned_card_slugs,
        )
        for view in theme_missing_views[:EDHREC_MISSING_LIMIT]
    ]
    combined_present_payload = [
        _cardview_to_payload(
            view,
            already_in_deck=True,
            roles=_roles_for_view(view),
            deck_card_id=deck_card_lookup.get(view.slug),
            deck_colors=deck_colors,
            owned_slugs=owned_card_slugs,
        )
        for view in combined_present_views[:EDHREC_PRESENT_LIMIT]
    ]
    combined_missing_payload = [
        _cardview_to_payload(
            view,
            already_in_deck=False,
            roles=_roles_for_view(view),
            deck_card_id=deck_card_lookup.get(view.slug),
            deck_colors=deck_colors,
            owned_slugs=owned_card_slugs,
        )
        for view in combined_missing_views[:EDHREC_MISSING_LIMIT]
    ]

    category_view_map: Dict[str, Dict[str, CardView]] = defaultdict(dict)
    commander_has_game_changers = any(
        (view.category or "").strip().lower() == "game changers" for view in commander_views
    )
    for view in commander_views:
        if not view.category:
            continue
        bucket = category_view_map[view.category]
        existing = bucket.get(view.slug)
        if existing is None or (view.synergy or 0) > (existing.synergy or 0):
            bucket[view.slug] = view
    for view in theme_views:
        if not view.category:
            continue
        if commander_has_game_changers and view.category.strip().lower() == "game changers":
            continue
        bucket = category_view_map[view.category]
        existing = bucket.get(view.slug)
        if existing is None or (view.synergy or 0) > (existing.synergy or 0):
            bucket[view.slug] = view

    category_payload_map: Dict[str, List[Dict[str, Any]]] = {}
    for category_name, view_lookup in category_view_map.items():
        sorted_views = _sort_cardviews(view_lookup.values())
        category_payload_map[category_name] = [
            _cardview_to_payload(
                view,
                already_in_deck=view.slug in present_slugs,
                roles=_roles_for_view(view),
                deck_card_id=deck_card_lookup.get(view.slug),
                deck_colors=deck_colors,
                owned_slugs=owned_card_slugs,
            )
            for view in sorted_views
        ]

    role_recommendations_map: Dict[str, List[Dict[str, Any]]] = {}
    for role in role_summaries:
        suggestions: List[Dict[str, Any]] = []
        for view in combined_missing_views:
            view_roles = _roles_for_view(view)
            if role["key"] not in view_roles:
                continue
            suggestions.append(
                _cardview_to_payload(
                    view,
                    already_in_deck=False,
                    roles=view_roles,
                    deck_card_id=deck_card_lookup.get(view.slug),
                    deck_colors=deck_colors,
                    owned_slugs=owned_card_slugs,
                )
            )
            if len(suggestions) >= EDHREC_ROLE_LIMIT:
                break
        role["edhrec_recommendations"] = suggestions
        role_recommendations_map[role["key"]] = suggestions

    commander_theme_options: List[Dict[str, str]] = []
    commander_theme_highlights: List[Dict[str, Any]] = []
    if commander_payload and commander_name:
        raw_theme_options = commander_payload.get("theme_options") or []
        seen_theme_slugs: Set[str] = set()
        seen_deck_tags: Set[str] = set()
        for option in raw_theme_options:
            if not isinstance(option, dict):
                continue
            opt_slug = str(option.get("slug") or "").strip().lower()
            if not opt_slug or opt_slug in seen_theme_slugs:
                continue
            deck_tag_label = resolve_deck_tag_from_slug(opt_slug)
            if not deck_tag_label or deck_tag_label in seen_deck_tags:
                continue
            seen_theme_slugs.add(opt_slug)
            seen_deck_tags.add(deck_tag_label)
            commander_theme_options.append({"slug": opt_slug, "label": deck_tag_label, "deck_tag": deck_tag_label})

        applied_theme_slug = str((commander_payload.get("applied_theme") or "").strip().lower())
        canonical_theme_slug = slugify_theme(tag_value).strip().lower() if tag_value else None
        highlight_exclusions = {slug for slug in (applied_theme_slug, canonical_theme_slug) if slug}

        for option in commander_theme_options:
            opt_slug = option["slug"]
            if opt_slug in highlight_exclusions:
                continue
            if len(commander_theme_highlights) >= EDHREC_THEME_SHOWCASE_LIMIT:
                break

            highlight_payload: Optional[Dict[str, Any]] = None
            highlight_cards: List[Dict[str, Any]] = []
            highlight_error: Optional[str] = None
            _, themed_payload, themed_error = ensure_commander_data(
                commander_name,
                theme_slug=opt_slug,
            )
            if themed_payload:
                highlight_payload = themed_payload
                highlight_views = commander_cardviews(themed_payload, categories=("High Synergy Cards",))
                if not highlight_views:
                    highlight_views = commander_cardviews(themed_payload, categories=("Top Cards",))
                highlight_views = _sort_cardviews(highlight_views)
                for view in highlight_views:
                    if not _within_deck_colors(view):
                        continue
                    highlight_cards.append(
                        _cardview_to_payload(
                            view,
                            already_in_deck=view.slug in present_slugs,
                            roles=_roles_for_view(view),
                            deck_card_id=deck_card_lookup.get(view.slug),
                            deck_colors=deck_colors,
                            owned_slugs=owned_card_slugs,
                        )
                    )
                    if len(highlight_cards) >= EDHREC_THEME_SHOWCASE_CARD_LIMIT:
                        break
                if themed_error:
                    highlight_error = themed_error
            else:
                highlight_error = themed_error or "Unable to load EDHREC data for this theme."

            if highlight_error and not highlight_cards:
                edhrec_errors.append(f"Could not load EDHREC theme '{option['label']}': {highlight_error}")

            commander_theme_highlights.append(
                {
                    "slug": opt_slug,
                    "label": option["label"],
                    "cards": highlight_cards,
                    "source_url": (highlight_payload or {}).get("source_url"),
                    "fetched_at": (highlight_payload or {}).get("fetched_at"),
                    "error": highlight_error if highlight_error else None,
                }
            )

    def _extract_type_chart(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not payload:
            return None
        panels = payload.get("panels") or {}
        piechart = panels.get("piechart") if isinstance(panels, dict) else None
        if not piechart:
            return None
        content = piechart.get("content")
        if not isinstance(content, list):
            return None
        normalized: List[Dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            value = item.get("value")
            if label is None or value is None:
                continue
            try:
                numeric_value = int(round(float(value)))
            except (TypeError, ValueError):
                continue
            entry = {
                "label": label,
                "value": numeric_value,
            }
            item_color = item.get("color")
            if not item_color:
                item_color = TYPE_COLOR_FALLBACKS.get(label) or TYPE_COLOR_FALLBACKS.get("Other")
            if item_color:
                entry["color"] = item_color
            normalized.append(entry)
        if not normalized:
            return None
        return {
            "title": piechart.get("title"),
            "content": normalized,
        }

    commander_type_chart = _extract_type_chart(commander_payload)
    theme_type_chart = _extract_type_chart(theme_payload)
    edhrec_type_chart = theme_type_chart or commander_type_chart

    edhrec_label_order: List[str] = []
    for chart in (theme_type_chart, commander_type_chart):
        if not chart:
            continue
        for item in chart.get("content") or []:
            label = item.get("label")
            if label and label not in edhrec_label_order:
                edhrec_label_order.append(label)

    color_lookup: Dict[str, str] = {}
    reference_chart_for_colors = theme_type_chart or commander_type_chart
    if reference_chart_for_colors and isinstance(reference_chart_for_colors.get("content"), list):
        for item in reference_chart_for_colors["content"]:
            label = item.get("label")
            color = item.get("color")
            if label and color:
                color_lookup[label] = color

    deck_type_chart: Optional[Dict[str, Any]] = None
    deck_type_chart_content: List[Dict[str, Any]] = []
    ordered_labels: List[str] = []
    ordered_labels.extend(edhrec_label_order)
    for label, _ in TYPE_BUCKET_ORDER:
        if label not in ordered_labels:
            ordered_labels.append(label)
    if "Other" not in ordered_labels:
        ordered_labels.append("Other")
    total_type_cards = sum(int(v) for v in deck_type_counts.values())
    for label in ordered_labels:
        if label == "Other":
            continue
        count = int(deck_type_counts.get(label, 0))
        if not count:
            continue
        deck_type_chart_content.append(
            {
                "label": label,
                "value": count,
                "percent": int(round(count * 100 / total_type_cards)) if total_type_cards else None,
                "color": color_lookup.get(label) or TYPE_COLOR_FALLBACKS.get(label) or TYPE_COLOR_FALLBACKS["Other"],
            }
        )
    other_count = int(deck_type_counts.get("Other", 0))
    if other_count:
        deck_type_chart_content.append(
            {
                "label": "Other",
                "value": other_count,
                "percent": int(round(other_count * 100 / total_type_cards)) if total_type_cards else None,
                "color": color_lookup.get("Other") or TYPE_COLOR_FALLBACKS.get("Other"),
            }
        )
    if deck_type_chart_content:
        deck_type_chart = {
            "title": "Current Deck Type Distribution",
            "total": total_type_cards,
            "content": deck_type_chart_content,
        }

    edhrec_summary = {
        "commander": {
            "name": commander_name,
            "slug": commander_slug,
            "available": bool(commander_payload),
            "source_url": (commander_payload or {}).get("source_url"),
            "fetched_at": (commander_payload or {}).get("fetched_at"),
            "applied_theme": (commander_payload or {}).get("applied_theme"),
            "error": commander_error if commander_name and (commander_error and not commander_payload) else None,
            "skip_reason": None if commander_name else "Commander not set for this deck.",
            "present": commander_present_payload,
            "missing": commander_missing_payload,
            "theme_options": commander_theme_options,
            "filters": color_filter_stats["commander"],
        },
        "theme": {
            "tag": tag_value,
            "tag_label": tag_label,
            "slug": theme_slug,
            "available": bool(theme_payload),
            "source_url": (theme_payload or {}).get("source_url"),
            "fetched_at": (theme_payload or {}).get("fetched_at"),
            "description": ((theme_payload or {}).get("metadata") or {}).get("description"),
            "error": theme_error if tag_value and (theme_error and not theme_payload) else None,
            "skip_reason": None if tag_value else "No deck tag assigned.",
            "present": theme_present_payload if theme_payload else [],
            "missing": theme_missing_payload if theme_payload else [],
            "filters": color_filter_stats["theme"],
        },
        "combined": {
            "present": combined_present_payload,
            "missing": combined_missing_payload,
            "filters": color_filter_stats["combined"],
        },
        "coverage": {
            "present_count": len(combined_present_views),
            "present_with_synergy": len(present_synergy_values),
            "present_average_synergy": average_synergy,
            "present_average_synergy_percent": int(round(average_synergy * 100)) if average_synergy is not None else None,
            "missing_candidates": len(combined_missing_views),
        },
        "role_recommendations": role_recommendations_map,
        "charts": {
            "type_distribution": edhrec_type_chart,
            "commander_type": commander_type_chart,
            "theme_type": theme_type_chart,
            "deck_current": deck_type_chart,
        },
        "errors": [err for err in edhrec_errors if err],
        "theme_showcase": commander_theme_highlights,
        "categories": category_payload_map,
    }

    theme_insights: List[Dict[str, Any]] = []
    for key, cards_for_theme in sorted(theme_tallies.items(), key=lambda item: len(item[1]), reverse=True):
        meta = THEME_DEFINITIONS.get(key)
        if not meta:
            continue
        score = len(cards_for_theme)
        if score < meta["min_score"]:
            continue
        keywords_counter: Counter[str] = Counter()
        typal_counter: Counter[str] = Counter()
        for card_name in cards_for_theme:
            card_info = analysis_card_map.get(card_name.lower())
            if not card_info:
                continue
            for keyword in card_info.get("keywords") or []:
                keywords_counter[keyword] += 1
            for typal in card_info.get("typal") or []:
                typal_counter[typal] += 1

        sample_names = sorted(cards_for_theme)[:6]
        sample_cards: List[Dict[str, Any]] = []
        for sample_name in sample_names:
            lowered = sample_name.lower()
            card_info = analysis_card_map.get(lowered)
            sample_cards.append(
                {
                    "name": sample_name,
                    "card_id": present_card_ids_map.get(lowered),
                    "hover_image": (card_info or {}).get("hover_image") or _hover_image_for_card_name(sample_name),
                    "keywords": list((card_info or {}).get("keywords") or []),
                    "typal": list((card_info or {}).get("typal") or []),
                    "primary_type": (card_info or {}).get("primary_type"),
                }
            )

        theme_keywords = [item for item, _ in keywords_counter.most_common(6)]
        theme_typal = [item for item, _ in typal_counter.most_common(6)]

        theme_insights.append(
            {
                "key": key,
                "label": meta["label"],
                "description": meta["description"],
                "score": score,
                "sample_cards": sample_cards,
                "recommendations": _recommend_for_theme(key, deck_colors, present_names, present_card_ids_map),
                "keywords": theme_keywords,
                "typal": theme_typal,
            }
        )

    tag_key_lower = str(tag_value).strip().lower() if tag_value else None
    existing_theme_keys = {item["key"].lower() for item in theme_insights}
    if tag_value and tag_key_lower not in existing_theme_keys:
        tag_description = None
        metadata = (theme_payload or {}).get("metadata") or {}
        if isinstance(metadata, dict):
            tag_description = metadata.get("description")
        if not tag_description and tag_synergy:
            tag_description = tag_synergy.get("description")
        sample_names: List[str] = []
        if tag_synergy:
            present_core = tag_synergy.get("core", {}).get("present", [])
            missing_core = tag_synergy.get("core", {}).get("missing", [])
            sample_names = [item.get("name") for item in present_core if item.get("name")][:6]
            if len(sample_names) < 6:
                sample_names.extend(
                    item.get("name") for item in missing_core if item.get("name")
                )
                sample_names = sample_names[:6]
        sample_card_entries: List[Dict[str, Any]] = []
        tag_keywords_counter: Counter[str] = Counter()
        tag_typal_counter: Counter[str] = Counter()
        for sample_name in sample_names:
            if not sample_name:
                continue
            lowered = sample_name.lower()
            card_info = analysis_card_map.get(lowered)
            keywords_list = list((card_info or {}).get("keywords") or [])
            typal_list = list((card_info or {}).get("typal") or [])
            for keyword in keywords_list:
                tag_keywords_counter[keyword] += 1
            for typal in typal_list:
                tag_typal_counter[typal] += 1
            sample_card_entries.append(
                {
                    "name": sample_name,
                    "card_id": present_card_ids_map.get(lowered),
                    "hover_image": (card_info or {}).get("hover_image") or _hover_image_for_card_name(sample_name),
                    "keywords": keywords_list,
                    "typal": typal_list,
                    "primary_type": (card_info or {}).get("primary_type"),
                }
            )
        recommendations_list: List[Dict[str, Any]] = []
        if tag_synergy:
            recommendations_list = [
                dict(rec) for rec in tag_synergy.get("support", {}).get("recommendations", [])[:5]
            ]
        theme_insights.insert(
            0,
            {
                "key": str(tag_value),
                "label": tag_label or tag_value,
                "description": tag_description or "Deck tag focus from EDHREC.",
                "score": (tag_synergy or {}).get("score") or theme_counts.get(tag_key_lower, 0),
                "sample_cards": sample_card_entries,
                "recommendations": recommendations_list,
                "keywords": [item for item, _ in tag_keywords_counter.most_common(6)],
                "typal": [item for item, _ in tag_typal_counter.most_common(6)],
            },
        )

    analysis_cards.sort(key=lambda c: c["name"].lower())

    mdfc_cards = []
    for card in analysis_cards:
        if str(card.get("layout") or "").lower() != "modal_dfc":
            continue
        faces = card.get("type_faces") or []
        face_names = [str(face.get("name") or "").strip() for face in faces if face.get("name")]
        face_summary = " // ".join(face_names) if face_names else None
        mdfc_cards.append(
            {
                "name": card["name"],
                "card_id": card.get("card_id"),
                "quantity": card.get("quantity"),
                "primary_type": card.get("primary_type"),
                "type_line": card.get("type_line"),
                "type_faces": faces or None,
                "hover_image": card.get("hover_image"),
                "face_summary": face_summary,
            }
        )

    return {
        "deck": {
            "id": folder.id,
            "name": folder.name,
            "commander": _commander_payload(folder, cards),
            "color_identity": {"letters": color_letters, "label": color_label, "html": color_html},
            "unique_cards": len(cards),
            "total_cards": total_cards,
            "tag": tag_value,
            "tag_category": tag_category,
            "tag_label": tag_label,
        },
        "roles": role_summaries,
        "themes": theme_insights,
        "edhrec": edhrec_summary,
        "deck_type_chart": deck_type_chart,
        "deck_card_rows": deck_card_rows,
        "deck_category_summary": deck_category_summary,
        "analysis_cards": analysis_cards,
        "mdfc_cards": mdfc_cards,
        "present_names": sorted({c.name for c in cards}),
        "tag_synergy": tag_synergy,
    }
