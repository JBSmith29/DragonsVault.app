"""Commander bracket card models and classifier helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import ensure_cache_loaded, prints_for_oracle, unique_oracle_by_name
from core.domains.decks.services.commander_bracket_reference_service import (
    CEDH_SIGNATURES,
    GAME_CHANGERS,
    MASS_LAND_DENIAL_NAMES,
    MASS_LAND_DENIAL_PATTERNS,
)

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
class ScoreSignal:
    key: str
    label: str
    description: str
    curve: Tuple[Tuple[float, float], ...]
    guidance: Tuple[str, ...] = ()
    show_if_zero: bool = True


SCORE_SIGNALS: Tuple[ScoreSignal, ...] = (
    ScoreSignal(
        key="game_changers",
        label="Game Changers list cards",
        description="Cards highlighted in Wizards' Game Changers list or flagged staples that warp casual tables.",
        curve=((0, 0.0), (1, 2.3), (2, 4.2), (3, 5.8), (4, 7.1), (5, 8.2), (6, 9.0), (8, 10.0)),
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
        curve=((0, 0.0), (1, 2.0), (2, 3.5), (3, 5.0), (4, 6.6), (5, 8.0), (6, 9.0), (8, 10.0)),
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
        curve=((0, 0.0), (1, 2.5), (2, 4.5), (3, 6.4), (4, 7.8), (5, 8.9), (6, 9.6)),
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
        curve=((0, 0.0), (1, 2.4), (2, 4.0), (3, 5.6), (4, 7.1), (5, 8.2), (6, 9.0), (8, 10.0)),
        guidance=(
            "0: No tracked deterministic combos",
            "1-2: Some dedicated lines exist",
            "3+: Multiple reliable combo paths",
        ),
    ),
)

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
    "bands and applies a final Bracket 4/5 split (15+ = Bracket 5, under 15 = Bracket 4)."
)

SCORE_OVERVIEW_GUIDANCE: Tuple[str, ...] = (
    "0-4: Exhibition — theme-first pods",
    "5-8: Core — low-pressure casual",
    "9-12: Upgraded — tuned synergy",
    "13-24: Optimized — high-power",
    "25+: cEDH — competitive pace",
)

BRACKET45_SCORE_SPLIT = 15.0

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


def _normalize_face_names(name: str) -> Set[str]:
    """Split adventure/DFC names so comparisons are reliable."""
    parts = re.split(r"\s+//\s+", name or "")
    out: Set[str] = set()
    for part in parts:
        clean = part.strip()
        if not clean:
            continue
        out.add(clean)
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
        oracle_text = (face or {}).get("oracle_text")
        if oracle_text:
            text_parts.append(oracle_text)
    combined_text = " // ".join(tp for tp in text_parts if tp)

    mana_cost = pr.get("mana_cost")
    produced_mana = pr.get("produced_mana")
    mana_value = pr.get("cmc") if pr.get("cmc") is not None else pr.get("mana_value")
    is_game_changer = bool(pr.get("game_changer"))

    return BracketCard(
        name=name or fallback_name,
        type_line=type_line,
        oracle_text=combined_text,
        mana_value=mana_value,
        mana_cost=mana_cost,
        produced_mana=produced_mana if isinstance(produced_mana, (list, tuple)) else None,
        is_game_changer=is_game_changer,
    )


def _resolve_commander(commander: Dict[str, Any]) -> Optional[BracketCard]:
    oracle_id = (commander or {}).get("oracle_id")
    name = (commander or {}).get("name")
    ensure_cache_loaded()

    print_payload: Optional[Dict[str, Any]] = None
    if oracle_id:
        try:
            prints = prints_for_oracle(oracle_id) or []
        except Exception:
            prints = []
        if prints:
            print_payload = prints[0]
    if not print_payload and name:
        try:
            resolved_oracle_id = unique_oracle_by_name(name)
        except Exception:
            resolved_oracle_id = None
        if resolved_oracle_id:
            try:
                prints = prints_for_oracle(resolved_oracle_id) or []
            except Exception:
                prints = []
            if prints:
                print_payload = prints[0]
    if not print_payload and name:
        return BracketCard(name=name)
    if not print_payload:
        return None
    return _card_from_print(print_payload, fallback_name=name or "")


def _is_zero_cmc_mana(card: BracketCard) -> bool:
    if "Land" in (card.type_line or ""):
        return False
    if card.mana_value is None or card.mana_value > 0:
        return False
    text = (card.oracle_text or "").lower()
    mana_cost = (card.mana_cost or "").strip()
    produces = card.produced_mana or []
    if produces:
        return True
    if ADDS_MANA_PATTERN.search(text):
        return True
    return mana_cost == ""


def _is_mass_land_denial(card: BracketCard) -> bool:
    for name in _normalize_face_names(card.name):
        if name in MASS_LAND_DENIAL_NAMES:
            return True
    text = card.oracle_text
    if not text:
        return False
    return any(pattern.search(text) for pattern in MASS_LAND_DENIAL_PATTERNS)


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
    if "Land" in type_line or "Creature" in type_line:
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
