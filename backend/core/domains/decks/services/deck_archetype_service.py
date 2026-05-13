"""Deck archetype classification.

Heuristically labels a deck as one of the familiar archetypes (aggro,
control, midrange, combo, stax, ramp, tribal, tokens) by scoring type
distribution, mana-curve shape, and keyword signals from oracle text.

The classifier is deliberately lightweight and explainable: each archetype
produces a numerical score with the reasons that contributed, so the UI can
surface "why did we call this control?" alongside the label. When no
archetype strongly wins, we return the top two labels with percentages.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from extensions import db
from models import Card, Folder


__all__ = [
    "ArchetypeLabel",
    "ArchetypeReport",
    "classify_deck",
]


# Ordered list of archetypes the classifier knows about. Add new entries in
# ``_score_components`` below; the data structures pick them up automatically.
ARCHETYPES: tuple[str, ...] = (
    "aggro",
    "control",
    "midrange",
    "combo",
    "stax",
    "ramp",
    "tribal",
    "tokens",
)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


_CONTROL_PATTERNS = (
    "counter target",
    "return target",
    "destroy target",
    "exile target",
    "draw three cards",
    "scry",
)
_COMBO_PATTERNS = (
    "infinite",
    "win the game",
    "lose the game",
    "search your library for",
    "if you would draw",
    "each opponent loses the game",
)
_STAX_PATTERNS = (
    "can't untap",
    "doesn't untap",
    "skip your",
    "players can't",
    "each player sacrifices",
    "spells cost {",
    "tap target",
)
_RAMP_PATTERNS = (
    "search your library for a land",
    "add one mana",
    "add {g}",
    "put a land card",
)
_TOKENS_PATTERNS = (
    "create a token",
    "create two",
    "create x",
    "put a +1/+1 counter on each",
    "populate",
)


def _count_matches(text: str, patterns: Iterable[str]) -> int:
    lowered = text.lower()
    return sum(1 for pattern in patterns if pattern in lowered)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ArchetypeLabel:
    name: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 2),
            "reasons": list(self.reasons),
        }


@dataclass
class ArchetypeReport:
    folder_id: int
    primary: ArchetypeLabel
    secondary: ArchetypeLabel | None
    scores: dict[str, float]
    evidence: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_id": self.folder_id,
            "primary": self.primary.to_dict(),
            "secondary": self.secondary.to_dict() if self.secondary else None,
            "scores": {name: round(score, 2) for name, score in self.scores.items()},
            "evidence": {name: list(items) for name, items in self.evidence.items()},
        }


@dataclass
class _DeckSnapshot:
    total_cards: int
    creatures: int
    noncreatures: int
    lands: int
    low_cmc_nonland: int
    high_cmc_nonland: int
    pattern_counts: dict[str, int]
    creature_types: Counter


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def _summarize(cards: list[Card]) -> _DeckSnapshot:
    creatures = 0
    noncreatures = 0
    lands = 0
    low_cmc = 0
    high_cmc = 0
    pattern_counts = {
        "control": 0,
        "combo": 0,
        "stax": 0,
        "ramp": 0,
        "tokens": 0,
    }
    creature_types: Counter[str] = Counter()
    total_cards = 0

    for card in cards:
        qty = max(0, int(card.quantity or 0))
        if qty <= 0:
            continue
        total_cards += qty
        type_line = (card.type_line or "").lower()
        oracle_text = card.oracle_text or ""

        if "land" in type_line:
            lands += qty
        else:
            mv = card.mana_value
            try:
                mv_f = float(mv) if mv is not None else None
            except (TypeError, ValueError):
                mv_f = None
            if mv_f is not None:
                if mv_f <= 2:
                    low_cmc += qty
                elif mv_f >= 5:
                    high_cmc += qty
            if "creature" in type_line:
                creatures += qty
                # Extract creature subtypes from the type line ("Creature — Goblin Scout").
                em_dash = type_line.split("—", 1)
                if len(em_dash) == 2:
                    for subtype in re.split(r"\s+", em_dash[1].strip()):
                        if subtype:
                            creature_types[subtype.title()] += qty
            else:
                noncreatures += qty

        pattern_counts["control"] += _count_matches(oracle_text, _CONTROL_PATTERNS) * qty
        pattern_counts["combo"] += _count_matches(oracle_text, _COMBO_PATTERNS) * qty
        pattern_counts["stax"] += _count_matches(oracle_text, _STAX_PATTERNS) * qty
        pattern_counts["ramp"] += _count_matches(oracle_text, _RAMP_PATTERNS) * qty
        pattern_counts["tokens"] += _count_matches(oracle_text, _TOKENS_PATTERNS) * qty

    return _DeckSnapshot(
        total_cards=total_cards,
        creatures=creatures,
        noncreatures=noncreatures,
        lands=lands,
        low_cmc_nonland=low_cmc,
        high_cmc_nonland=high_cmc,
        pattern_counts=pattern_counts,
        creature_types=creature_types,
    )


def _pct(part: int, whole: int) -> float:
    return (part / whole * 100.0) if whole else 0.0


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_components(snapshot: _DeckSnapshot) -> tuple[dict[str, float], dict[str, list[str]]]:
    scores = {name: 0.0 for name in ARCHETYPES}
    evidence: dict[str, list[str]] = {name: [] for name in ARCHETYPES}
    total = snapshot.total_cards or 1

    creature_pct = _pct(snapshot.creatures, total)
    low_pct = _pct(snapshot.low_cmc_nonland, max(1, snapshot.total_cards - snapshot.lands))
    high_pct = _pct(snapshot.high_cmc_nonland, max(1, snapshot.total_cards - snapshot.lands))

    # Aggro: creature-heavy + cheap curve
    if creature_pct >= 30 and low_pct >= 40:
        scores["aggro"] += creature_pct / 2 + low_pct / 2
        evidence["aggro"].append(
            f"{creature_pct:.0f}% creatures and {low_pct:.0f}% 0-2 CMC non-lands"
        )

    # Control: heavy control-pattern oracle text + low creature count
    control_hits = snapshot.pattern_counts["control"]
    if control_hits and creature_pct < 30:
        scores["control"] += min(60, control_hits * 3)
        evidence["control"].append(
            f"{control_hits} counter/remove/draw clauses in oracle text"
        )

    # Midrange: balanced curve
    if 20 <= creature_pct <= 40 and 20 <= low_pct <= 50 and high_pct < 30:
        scores["midrange"] += 40
        evidence["midrange"].append("Curve and creature count are balanced")

    # Combo: "win the game" or "infinite" + tutors
    combo_hits = snapshot.pattern_counts["combo"]
    if combo_hits >= 2:
        scores["combo"] += min(80, combo_hits * 6)
        evidence["combo"].append(f"{combo_hits} combo-style clauses detected")

    # Stax: prison-style patterns
    stax_hits = snapshot.pattern_counts["stax"]
    if stax_hits >= 3:
        scores["stax"] += min(70, stax_hits * 4)
        evidence["stax"].append(f"{stax_hits} stax/lock clauses detected")

    # Ramp: lots of ramp spells + high-cmc finishers
    ramp_hits = snapshot.pattern_counts["ramp"]
    if ramp_hits >= 4:
        scores["ramp"] += min(60, ramp_hits * 3)
        evidence["ramp"].append(f"{ramp_hits} mana-ramp clauses detected")
    if high_pct >= 15:
        scores["ramp"] += high_pct / 2
        evidence["ramp"].append(f"{high_pct:.0f}% high-CMC finishers")

    # Tribal: a single creature type dominates the creature base.
    # When a single subtype dominates, that's a more specific signal than the
    # generic aggro "lots of cheap creatures" detection, so we weight it
    # heavier to ensure it beats aggro when both apply.
    if snapshot.creatures:
        top_type, top_count = next(
            iter(snapshot.creature_types.most_common(1)), ("", 0)
        )
        if top_count >= 10 and top_count / snapshot.creatures >= 0.5:
            tribal_bonus = 100 + (top_count / snapshot.creatures * 40)
            scores["tribal"] += tribal_bonus
            evidence["tribal"].append(
                f"{top_count} {top_type} creatures ({top_count / snapshot.creatures * 100:.0f}%)"
            )

    # Tokens: "create a token" density
    tokens_hits = snapshot.pattern_counts["tokens"]
    if tokens_hits >= 3:
        scores["tokens"] += min(60, tokens_hits * 4)
        evidence["tokens"].append(f"{tokens_hits} token-creation clauses")

    return scores, evidence


def classify_deck(folder: Folder) -> ArchetypeReport:
    """Classify a folder's deck and return the top-scoring archetype(s)."""
    cards = (
        db.session.query(Card)
        .filter(Card.folder_id == folder.id)
        .all()
    )
    snapshot = _summarize(cards)
    scores, evidence = _score_components(snapshot)

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    primary_name, primary_score = ordered[0]
    secondary_name, secondary_score = ordered[1] if len(ordered) > 1 else (None, 0.0)

    primary_label = ArchetypeLabel(
        name=primary_name,
        score=primary_score,
        reasons=list(evidence.get(primary_name) or []),
    )
    secondary_label: ArchetypeLabel | None = None
    if secondary_name and secondary_score >= 25 and secondary_score >= primary_score * 0.5:
        secondary_label = ArchetypeLabel(
            name=secondary_name,
            score=secondary_score,
            reasons=list(evidence.get(secondary_name) or []),
        )

    return ArchetypeReport(
        folder_id=folder.id,
        primary=primary_label,
        secondary=secondary_label,
        scores=scores,
        evidence=evidence,
    )
