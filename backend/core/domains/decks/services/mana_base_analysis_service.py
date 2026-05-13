"""Mana base health analysis for decks.

Evaluates the land package of a deck by classifying each land into
categories (basic / fetch / dual / shock / tri / utility / etc.), computing
color-source ratios, and producing actionable warnings when the curve or
land count looks off. Results are advisory: the goal is to help the deck
owner decide if more sources of a color are needed, not to block anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from extensions import db
from models import Card, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.deck_utils import BASIC_LANDS
from shared.mtg import _lookup_print_data


__all__ = [
    "ManaBaseReport",
    "analyze_mana_base",
    "LAND_CATEGORIES",
]


# ---------------------------------------------------------------------------
# Land classification helpers
# ---------------------------------------------------------------------------


LAND_CATEGORIES: tuple[str, ...] = (
    "basic",
    "snow_basic",
    "dual",
    "shock",
    "fetch",
    "triland",
    "check",
    "filter",
    "pain",
    "scry",
    "bounce",
    "utility",
    "colorless",
)


_FETCH_PATTERNS = (
    "search your library for a",
    "pay 1 life",  # classic fetch wording includes "pay 1 life, sacrifice"
)
_SHOCK_PATTERNS = (
    "as ~ enters, you may pay 2 life. if you don't",
    "enters tapped unless you pay 2 life",
)
_CHECK_PATTERNS = (
    "enters tapped unless you control a",
)
_FILTER_PATTERNS = (
    "{t}, pay 1 life: add",  # weak pattern, useful for quick filters
    "filter",
)
_TRILAND_PATTERNS = (
    "enters tapped",
    "add one mana of any of three colors",
)


def _snow_basic(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered.startswith("snow-covered ") and lowered.rsplit(" ", 1)[-1].title() in BASIC_LANDS


def _is_basic(name: str) -> bool:
    return name.strip() in BASIC_LANDS


def _produces_colors(oracle_text: str, type_line: str) -> set[str]:
    """Heuristic: return {W,U,B,R,G,C} mana symbols the land can produce."""
    text = (oracle_text or "").upper()
    colors: set[str] = set()
    for match in re.findall(r"\{([^}]+)\}", text):
        if match in {"W", "U", "B", "R", "G", "C"}:
            colors.add(match)
    if "ANY COLOR" in text:
        colors.update({"W", "U", "B", "R", "G"})

    # Basic types on the type line always yield their color.
    lowered_type = type_line.lower()
    for color_name, letter in (
        ("plains", "W"),
        ("island", "U"),
        ("swamp", "B"),
        ("mountain", "R"),
        ("forest", "G"),
    ):
        if color_name in lowered_type:
            colors.add(letter)
    return colors


def _classify_land(name: str, oracle_text: str, type_line: str, colors: set[str]) -> str:
    if _snow_basic(name):
        return "snow_basic"
    if _is_basic(name):
        return "basic"

    text = (oracle_text or "").lower()
    if any(pattern in text for pattern in _FETCH_PATTERNS) and "search your library" in text and "land" in text:
        return "fetch"
    if any(pattern in text for pattern in _SHOCK_PATTERNS):
        return "shock"
    if "triome" in name.lower() or all(tok in text for tok in ("enters tapped", "cycling")):
        return "triland"
    if any(pattern in text for pattern in _CHECK_PATTERNS):
        return "check"
    if "you may pay" in text and "life" in text:
        return "pain"
    if "scry" in text and "enters tapped" in text:
        return "scry"
    if "return" in text and "unless" in text and "land you control" in text:
        return "bounce"
    if len(colors) == 2:
        return "dual"
    if not colors:
        return "colorless"
    return "utility"


def _enters_untapped(oracle_text: str) -> bool:
    text = (oracle_text or "").lower()
    if "enters tapped" not in text:
        return True
    # Conditional ETB-tapped wording means it can enter untapped some of the time.
    conditional_markers = (
        "unless you control",
        "unless you pay",
        "if you control",
        "if you do",
        "unless you have",
    )
    return any(marker in text for marker in conditional_markers)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LandBreakdown:
    name: str
    category: str
    quantity: int
    produces_colors: list[str]
    enters_untapped: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "quantity": self.quantity,
            "produces_colors": list(self.produces_colors),
            "enters_untapped": self.enters_untapped,
        }


@dataclass
class ManaBaseReport:
    folder_id: int
    total_cards: int
    total_lands: int
    land_percent: float | None
    untapped_lands: int
    tapped_lands: int
    color_sources: dict[str, int]
    recommended_color_sources: dict[str, int]
    category_counts: dict[str, int]
    lands: list[LandBreakdown]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_id": self.folder_id,
            "total_cards": self.total_cards,
            "total_lands": self.total_lands,
            "land_percent": self.land_percent,
            "untapped_lands": self.untapped_lands,
            "tapped_lands": self.tapped_lands,
            "color_sources": self.color_sources,
            "recommended_color_sources": self.recommended_color_sources,
            "category_counts": self.category_counts,
            "lands": [land.to_dict() for land in self.lands],
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


#: Frank Karsten-style rule of thumb: 20 sources per color for a 4-drop,
#: scaled down for lower-cost cards. This is an approximation; the dedicated
#: per-pip table would require running Karsten's regression per mana cost.
_COLOR_SOURCE_TARGETS = {"single_color_commander": 37, "two_color": 32, "three_color": 28}


def _target_source_count(color_count: int) -> int:
    if color_count <= 1:
        return _COLOR_SOURCE_TARGETS["single_color_commander"]
    if color_count == 2:
        return _COLOR_SOURCE_TARGETS["two_color"]
    return _COLOR_SOURCE_TARGETS["three_color"]


def analyze_mana_base(folder: Folder) -> ManaBaseReport:
    """Evaluate the land package of ``folder``.

    The caller is responsible for authorization. Scryfall data is read from
    the local cache; when missing we fall back to the card's cached columns
    (``type_line``/``oracle_text``) so analysis still produces a best-effort
    result.
    """
    if not sc.cache_ready():
        sc.ensure_cache_loaded()

    cards = (
        db.session.query(Card)
        .filter(Card.folder_id == folder.id)
        .all()
    )

    lands: list[LandBreakdown] = []
    total_cards = 0
    total_lands = 0
    untapped_lands = 0
    tapped_lands = 0
    color_sources: dict[str, int] = {c: 0 for c in ("W", "U", "B", "R", "G", "C")}
    category_counts: dict[str, int] = {key: 0 for key in LAND_CATEGORIES}

    for card in cards:
        qty = max(0, int(card.quantity or 0))
        if qty <= 0:
            continue
        total_cards += qty
        type_line = (card.type_line or "").strip()
        if not type_line:
            pr = _lookup_print_data(
                card.set_code, card.collector_number, card.name, card.oracle_id
            )
            type_line = str((pr or {}).get("type_line") or "")
        if "land" not in type_line.lower():
            continue
        total_lands += qty

        oracle_text = (card.oracle_text or "").strip()
        if not oracle_text:
            pr = _lookup_print_data(
                card.set_code, card.collector_number, card.name, card.oracle_id
            )
            oracle_text = str((pr or {}).get("oracle_text") or "")

        colors = _produces_colors(oracle_text, type_line)
        category = _classify_land(card.name or "", oracle_text, type_line, colors)
        untapped = _enters_untapped(oracle_text)

        for color in colors:
            color_sources[color] = color_sources.get(color, 0) + qty
        if not colors:
            color_sources["C"] = color_sources.get("C", 0) + qty

        category_counts[category] = category_counts.get(category, 0) + qty
        if untapped:
            untapped_lands += qty
        else:
            tapped_lands += qty

        lands.append(
            LandBreakdown(
                name=card.name,
                category=category,
                quantity=qty,
                produces_colors=sorted(colors),
                enters_untapped=untapped,
            )
        )

    color_identity_letters = _deck_color_identity(folder, cards)
    target_per_color = _target_source_count(len(color_identity_letters) or 1)
    recommended = {letter: target_per_color for letter in color_identity_letters} if color_identity_letters else {}

    warnings: list[str] = []
    if total_lands < 32:
        warnings.append(
            f"Only {total_lands} lands; most 4-player Commander decks want 36–38."
        )
    elif total_lands > 42:
        warnings.append(
            f"{total_lands} lands is higher than usual; double-check the curve."
        )

    if total_lands:
        land_percent = total_lands / max(1, total_cards) * 100
    else:
        land_percent = None

    if tapped_lands and total_lands:
        if (tapped_lands / total_lands) > 0.35:
            warnings.append(
                f"{tapped_lands}/{total_lands} lands enter tapped; that's a lot of slow starts."
            )

    for letter, target in recommended.items():
        have = color_sources.get(letter, 0)
        if have < target:
            warnings.append(
                f"Only {have} sources of {letter}; aim for {target}+ for reliable color access."
            )

    return ManaBaseReport(
        folder_id=folder.id,
        total_cards=total_cards,
        total_lands=total_lands,
        land_percent=land_percent,
        untapped_lands=untapped_lands,
        tapped_lands=tapped_lands,
        color_sources=color_sources,
        recommended_color_sources=recommended,
        category_counts=category_counts,
        lands=sorted(lands, key=lambda land: (-land.quantity, land.name.lower())),
        warnings=warnings,
    )


def _deck_color_identity(folder: Folder, cards: Iterable[Card]) -> list[str]:
    identity: set[str] = set()
    if folder.commander_oracle_id:
        # Commander cards are in the deck; pull their color identity from the cache.
        for card in cards:
            if card.oracle_id and card.oracle_id in folder.commander_oracle_id:
                for ch in (card.color_identity or ""):
                    if ch.isalpha():
                        identity.add(ch.upper())
    if identity:
        return sorted(identity)
    # Fall back to aggregating colors across every card in the deck.
    for card in cards:
        for ch in (card.color_identity or ""):
            if ch.isalpha():
                identity.add(ch.upper())
    return sorted(identity - {"C"})
