"""Scoring helpers for Build-A-Deck recommendations."""

from __future__ import annotations

from typing import Iterable, Mapping

from .build_mechanic_service import mechanic_labels
from .build_role_service import role_label

EDHREC_BASELINE_WEIGHT = 6.0
ROLE_ALIGNMENT_WEIGHT = 1.5
MECHANIC_ALIGNMENT_WEIGHT = 1.0
TAG_WEIGHT_MULTIPLIER = 1.25
DECK_GAP_BONUS = 2.5
OWNERSHIP_BONUS = 3.0
MAX_EDHREC_RANK = 200


def _normalize_edhrec_baseline(
    synergy_score: float | None,
    synergy_rank: int | None,
) -> float:
    if synergy_score is not None:
        score = float(synergy_score or 0.0)
        if score > 1.5:
            return min(score / 100.0, 1.0)
        return max(score, 0.0)
    if synergy_rank:
        rank = max(int(synergy_rank), 1)
        return max(0.0, 1.0 - ((rank - 1) / float(MAX_EDHREC_RANK)))
    return 0.0


def _join_labels(values: Iterable[str]) -> str:
    labels = [v for v in values if v]
    return ", ".join(labels)


def _compact_list(values: Iterable[str], limit: int = 2) -> str:
    labels = [v for v in values if v]
    if not labels:
        return ""
    if len(labels) <= limit:
        return ", ".join(labels)
    return f"{', '.join(labels[:limit])} +{len(labels) - limit}"


_ROLE_SHORT_LABELS = {
    "mana_fixing": "Fixing",
    "card_selection": "Selection",
    "board_wipe": "Wipe",
    "sacrifice_outlet": "Sac Outlet",
    "token_producer": "Tokens",
}


def _compact_role_label(role: str) -> str:
    key = (role or "").strip().lower()
    if key in _ROLE_SHORT_LABELS:
        return _ROLE_SHORT_LABELS[key]
    return role_label(role)


def _compact_roles(values: Iterable[str]) -> str:
    return _compact_list((_compact_role_label(v) for v in values), limit=2)


def _compact_mechanics(values: Iterable[str]) -> str:
    return _compact_list(mechanic_labels(values), limit=2)


def _score_card(
    *,
    synergy_score: float | None,
    synergy_rank: int | None,
    roles: Iterable[str],
    mechanics: Iterable[str],
    commander_mechanics: Iterable[str],
    tag_role_weights: Mapping[str, float],
    gap_roles: Iterable[str],
    owned_qty: int = 0,
    include_owned_bonus: bool = False,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0

    baseline = _normalize_edhrec_baseline(synergy_score, synergy_rank)
    if baseline:
        score += baseline * EDHREC_BASELINE_WEIGHT
        reasons.append("EDHREC")

    role_set = {r for r in roles if r and r != "utility"}
    if role_set:
        score += ROLE_ALIGNMENT_WEIGHT * len(role_set)
        reasons.append("Roles " + _compact_roles(sorted(role_set)))

    gap_set = {r for r in gap_roles if r}
    if gap_set:
        score += DECK_GAP_BONUS * len(gap_set)
        reasons.append("Needs " + _compact_roles(sorted(gap_set)))

    tag_matches = [r for r in role_set if tag_role_weights.get(r)]
    if tag_matches:
        tag_bonus = sum(float(tag_role_weights.get(r, 0.0) or 0.0) for r in tag_matches)
        if tag_bonus:
            score += tag_bonus * TAG_WEIGHT_MULTIPLIER
            reasons.append("Tag " + _compact_roles(tag_matches))

    mechanic_set = {m for m in mechanics if m}
    commander_set = {m for m in commander_mechanics if m}
    mechanic_matches = mechanic_set & commander_set
    if mechanic_matches:
        score += MECHANIC_ALIGNMENT_WEIGHT * len(mechanic_matches)
        reasons.append("Cmdr " + _compact_mechanics(mechanic_matches))

    if include_owned_bonus and owned_qty:
        score += OWNERSHIP_BONUS
        reasons.append("Owned")

    if not reasons:
        reasons.append("Needs")

    return score, reasons


def score_edhrec_card(
    *,
    synergy_score: float | None,
    synergy_rank: int | None,
    roles: Iterable[str],
    mechanics: Iterable[str],
    commander_mechanics: Iterable[str],
    tag_role_weights: Mapping[str, float],
    gap_roles: Iterable[str],
) -> tuple[float, list[str]]:
    return _score_card(
        synergy_score=synergy_score,
        synergy_rank=synergy_rank,
        roles=roles,
        mechanics=mechanics,
        commander_mechanics=commander_mechanics,
        tag_role_weights=tag_role_weights,
        gap_roles=gap_roles,
        owned_qty=0,
        include_owned_bonus=False,
    )


def score_app_card(
    *,
    synergy_score: float | None,
    synergy_rank: int | None,
    roles: Iterable[str],
    mechanics: Iterable[str],
    commander_mechanics: Iterable[str],
    tag_role_weights: Mapping[str, float],
    gap_roles: Iterable[str],
    owned_qty: int,
) -> tuple[float, list[str]]:
    return _score_card(
        synergy_score=synergy_score,
        synergy_rank=synergy_rank,
        roles=roles,
        mechanics=mechanics,
        commander_mechanics=commander_mechanics,
        tag_role_weights=tag_role_weights,
        gap_roles=gap_roles,
        owned_qty=owned_qty,
        include_owned_bonus=True,
    )


__all__ = ["score_edhrec_card", "score_app_card"]
