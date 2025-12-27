"""Scoring helpers for Build-A-Deck recommendations."""

from __future__ import annotations

from typing import Iterable

EDHREC_SYNERGY_WEIGHT = 1.0
OWNED_BONUS = 12.0
TAG_MATCH_BONUS = 6.0
ROLE_GAP_BONUS = 5.0


def score_edhrec_card(
    *,
    synergy_score: float | None,
    owned_qty: int = 0,
    tag_matches: Iterable[str] | None = None,
    gap_roles: Iterable[str] | None = None,
) -> tuple[float, list[str]]:
    score = float(synergy_score or 0.0) * EDHREC_SYNERGY_WEIGHT
    reasons: list[str] = ["High synergy with commander"]

    if tag_matches:
        matches = [m for m in tag_matches if m]
        if matches:
            score += TAG_MATCH_BONUS
            reasons.append("Matches deck tag: " + ", ".join(matches))

    if owned_qty:
        score += OWNED_BONUS
        reasons.append("Owned in collection")

    if gap_roles:
        gap_list = [role.replace("_", " ").title() for role in gap_roles if role]
        if gap_list:
            score += ROLE_GAP_BONUS
            reasons.append("Fills gap: " + ", ".join(gap_list))

    return score, reasons


def score_app_card(
    *,
    owned_qty: int,
    gap_roles: Iterable[str],
    tag_bonus: float = 0.0,
) -> tuple[float, list[str]]:
    roles = [role for role in gap_roles if role]
    reasons: list[str] = []
    if roles:
        role_labels = [role.replace("_", " ").title() for role in roles]
        if role_labels:
            reasons.append("Fills gap: " + ", ".join(role_labels))
    if owned_qty:
        reasons.append("Owned in collection")
    if tag_bonus:
        reasons.append("Aligns with selected tag")

    score = (float(owned_qty) * 0.5) + (len(roles) * ROLE_GAP_BONUS) + float(tag_bonus or 0.0)
    return score, reasons


__all__ = ["score_edhrec_card", "score_app_card"]
