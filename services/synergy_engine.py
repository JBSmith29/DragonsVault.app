from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


def _weight_for(key: str, weights: Dict[str, float]) -> float:
    """Return a weight for a given role/sub-role label (case-insensitive)."""
    if not key:
        return 0.0
    candidates = [
        key,
        key.lower(),
        key.upper(),
        key.title(),
        key.replace("_", " "),
        key.replace("_", " ").title(),
    ]
    for cand in candidates:
        if cand in weights:
            try:
                return float(weights[cand])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def commander_synergy(card: Any, commander: Any, synergy_weights: Optional[Dict[str, float]] = None) -> float:
    """
    Returns a numeric synergy bonus based on commander oracle text.
    """
    weights = synergy_weights or {}
    text = str(getattr(commander, "oracle_text", "") or "").lower()
    if not text:
        return 0.0

    bonus = 0.0

    if "draw" in text or "whenever you draw" in text:
        bonus += _weight_for("Card Draw", weights) * 0.3
        bonus += _weight_for("Wheel", weights) * 0.15
        bonus += _weight_for("Cantrip", weights) * 0.1

    if "treasure" in text:
        bonus += _weight_for("Token Generation", weights) * 0.3
        bonus += _weight_for("Treasure Tokens", weights) * 0.25
        bonus += _weight_for("Treasure Ramp", weights) * 0.2
        bonus += _weight_for("Ramp", weights) * 0.2

    if "landfall" in text:
        bonus += _weight_for("Ramp", weights) * 0.3
        bonus += _weight_for("Land Ramp", weights) * 0.25
        bonus += _weight_for("Token Generation", weights) * 0.2
        bonus += _weight_for("Fetchland", weights) * 0.1

    if "sacrifice" in text:
        bonus += _weight_for("Sacrifice Outlet", weights) * 0.3
        bonus += _weight_for("Death Trigger", weights) * 0.25

    if "spells you cast" in text or "cast an instant or sorcery" in text:
        bonus += _weight_for("Card Draw", weights) * 0.15
        bonus += _weight_for("Instant/Sorcery", weights) * 0.2

    if "tokens" in text or "token" in text:
        bonus += _weight_for("Token Generation", weights) * 0.3
        bonus += _weight_for("Creature Tokens", weights) * 0.2

    if "artifact" in text:
        bonus += _weight_for("Mana Rock", weights) * 0.25
        bonus += _weight_for("Artifact Tutor", weights) * 0.2
        bonus += _weight_for("Artifact Synergy", weights) * 0.2

    if "creatures entering" in text or "enter the battlefield" in text:
        bonus += _weight_for("Creature Tokens", weights) * 0.2
        bonus += _weight_for("Creature Tutor", weights) * 0.2

    return bonus


def score_card_synergy(
    card: Any,
    deck_analysis: Dict[str, Any],
    synergy_weights: Dict[str, float],
    commander: Any,
) -> float:
    """
    Score a card against deck roles/themes and commander hooks.
    """
    weights = synergy_weights or {}
    score = 0.0

    roles: Iterable[str] = getattr(card, "roles", None) or getattr(card, "role_labels", None) or []
    subroles: Iterable[str] = getattr(card, "subroles", None) or getattr(card, "subrole_labels", None) or []

    for role in roles:
        score += _weight_for(role, weights)

    for sub in subroles:
        score += _weight_for(sub, weights) * 0.5

    score += commander_synergy(card, commander, weights)

    return score
