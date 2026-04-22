from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Sequence, Tuple

from core.domains.cards.services.scryfall_cache import ensure_cache_loaded
from core.domains.decks.services.commander_bracket_card_service import (
    AVG_CMC_BENEFITS,
    AVG_CMC_PENALTIES,
    BRACKET45_SCORE_SPLIT,
    BracketCard,
    MetricBucket,
    SCORE_OVERVIEW_GUIDANCE,
    SCORE_OVERVIEW_TEXT,
    SCORE_SIGNALS,
    SIGNAL_REASON_LABELS,
    ScoreSignal,
    _card_from_print,
    _is_cedh_signature,
    _is_extra_turn_piece,
    _is_game_changer,
    _is_instant_win,
    _is_land_tutor,
    _is_mass_land_denial,
    _is_nonland_tutor,
    _is_zero_cmc_mana,
    _normalize_face_names,
    _resolve_commander,
)
from core.domains.decks.services.commander_bracket_reference_service import (
    BRACKET_LABELS,
    BRACKET_REFERENCE,
    BRACKET_REFERENCE_BY_LEVEL,
    BRACKET_RULESET_EPOCH,
)
from core.domains.decks.services.commander_spellbook_service import (
    SPELLBOOK_EARLY_COMBOS,
    SPELLBOOK_LATE_COMBOS,
    SPELLBOOK_RESULT_LABELS,
    reload_spellbook_combos,
    spellbook_dataset_epoch,
)

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


SCORE_BANDS: Tuple[Tuple[float, int], ...] = (
    (4.0, 1),
    (8.0, 2),
    (12.0, 3),
    (24.0, 4),
    (math.inf, 5),
)


def _score_to_band(score: float) -> int:
    for threshold, level in SCORE_BANDS:
        if score <= threshold:
            return level
    return SCORE_BANDS[-1][1]


def evaluate_commander_bracket(
    deck_cards: Iterable[Dict[str, Any] | BracketCard],
    commander: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    from . import commander_bracket_evaluation_service

    return commander_bracket_evaluation_service.evaluate_commander_bracket(deck_cards, commander)
