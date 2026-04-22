"""Commander bracket evaluation pipeline."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import quote_plus

from .commander_bracket_reference_service import CEDH_COMMANDERS, load_bracket_ruleset
from .commander_spellbook_service import (
    SPELLBOOK_COMBO_GROUPS,
    SPELLBOOK_EARLY_INDEX,
    SPELLBOOK_LATE_INDEX,
    SPELLBOOK_RESULT_LABELS,
    SpellbookCombo,
    combo_piece_count as _combo_piece_count,
    find_spellbook_matches as _find_spellbook_matches,
    format_spellbook_combo_descriptor as _format_spellbook_combo_descriptor,
    infinity_badge as _infinity_badge,
    is_three_card_combo as _is_three_card_combo,
    normalize_card_key as _normalize_card_key,
)
from .core_role_logic import derive_core_roles


def evaluate_commander_bracket(
    deck_cards: Iterable[Dict[str, Any] | object],
    commander: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from . import commander_brackets as legacy

    ensure_cache_loaded = legacy.ensure_cache_loaded
    BracketCard = legacy.BracketCard
    MetricBucket = legacy.MetricBucket
    _resolve_commander = legacy._resolve_commander
    _normalize_face_names = legacy._normalize_face_names
    _is_game_changer = legacy._is_game_changer
    _is_extra_turn_piece = legacy._is_extra_turn_piece
    _is_mass_land_denial = legacy._is_mass_land_denial
    _is_nonland_tutor = legacy._is_nonland_tutor
    _is_land_tutor = legacy._is_land_tutor
    _is_cedh_signature = legacy._is_cedh_signature
    _is_zero_cmc_mana = legacy._is_zero_cmc_mana
    _is_instant_win = legacy._is_instant_win
    _score_piecewise = legacy._score_piecewise
    _score_to_band = legacy._score_to_band
    BRACKET_LABELS = legacy.BRACKET_LABELS
    BRACKET45_SCORE_SPLIT = legacy.BRACKET45_SCORE_SPLIT
    SIGNAL_REASON_LABELS = legacy.SIGNAL_REASON_LABELS
    SCORE_SIGNALS = legacy.SCORE_SIGNALS
    SCORE_OVERVIEW_TEXT = legacy.SCORE_OVERVIEW_TEXT
    SCORE_OVERVIEW_GUIDANCE = legacy.SCORE_OVERVIEW_GUIDANCE
    AVG_CMC_BENEFITS = legacy.AVG_CMC_BENEFITS
    AVG_CMC_PENALTIES = legacy.AVG_CMC_PENALTIES

    ensure_cache_loaded()

    ruleset = load_bracket_ruleset()
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

    advantage_roles = {"draw", "selection", "advantage", "recursion"}
    repeatable_roles = {"engine", "recursion"}
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
    advantage_burst = 0
    advantage_repeatable = 0
    advantage_commander_based = False

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
            if roles & repeatable_roles:
                advantage_repeatable += qty
            else:
                advantage_burst += qty
            if commander_name and card.name.casefold() == commander_name.casefold():
                advantage_commander_based = True
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
    early_matches = _find_spellbook_matches(deck_counts, SPELLBOOK_EARLY_INDEX, spellbook_seen)
    spellbook_late = _find_spellbook_matches(deck_counts, SPELLBOOK_LATE_INDEX, spellbook_seen)

    spellbook_early: List[SpellbookCombo] = []
    spellbook_three_card: List[SpellbookCombo] = []
    for combo in early_matches:
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
            return tag.replace("_", " ")
        return marker

    def _build_spellbook_entry(combo: SpellbookCombo, tier: str) -> Dict[str, Any]:
        card_entries: List[Dict[str, str]] = []
        card_tokens: List[str] = []
        for card_name in combo.cards or ():
            encoded = quote_plus(card_name)
            qty = combo.requirements.get(_normalize_card_key(card_name), 0)
            label = f"{card_name} &times;{qty}" if qty and qty > 1 else card_name
            card_entries.append(
                {
                    "name": card_name,
                    "label": label,
                    "hover": f"https://api.scryfall.com/cards/named?format=image&version=large&exact={encoded}",
                }
            )
            card_tokens.append(label)

        categories = tuple(combo.result_categories or ())
        tags = [_infinity_badge(group_label_lookup[slug]) for slug in categories if slug in group_label_lookup]
        return {
            "id": combo.id,
            "url": combo.url or f"https://commanderspellbook.com/combo/{combo.id}",
            "result_labels": [lbl for tag in combo.result_categories if (lbl := _label_for(tag))],
            "cards": card_entries,
            "cards_line": " + ".join(card_tokens) if card_tokens else combo.id,
            "categories": categories,
            "tags": tags,
            "tier": tier,
            "piece_count": _combo_piece_count(combo),
        }

    spellbook_details_for_view: List[Dict[str, Any]] = []
    spellbook_late_details: List[Dict[str, Any]] = []
    spellbook_three_card_details: List[Dict[str, Any]] = []

    for combo in spellbook_early:
        buckets["spellbook_combos"].add(_format_spellbook_combo_descriptor(combo), 1)
        spellbook_details_for_view.append(_build_spellbook_entry(combo, "scoring"))
    for combo in spellbook_late:
        spellbook_late_details.append(_build_spellbook_entry(combo, "late"))
    for combo in spellbook_three_card:
        spellbook_three_card_details.append(_build_spellbook_entry(combo, "three_card"))

    all_spellbook_entries = spellbook_details_for_view + spellbook_late_details + spellbook_three_card_details
    combo_groups: List[Dict[str, Any]] = []
    for slug, label in SPELLBOOK_COMBO_GROUPS:
        grouped_items = [item for item in all_spellbook_entries if slug in item.get("categories", ())]
        if grouped_items:
            for item in grouped_items:
                item.setdefault("_grouped", set()).add(slug)
            combo_groups.append(
                {
                    "key": slug,
                    "label": _infinity_badge(label),
                    "items": grouped_items,
                }
            )

    leftovers = [item for item in all_spellbook_entries if not item.get("_grouped")]
    for item in all_spellbook_entries:
        if "_grouped" in item:
            del item["_grouped"]
    if leftovers:
        combo_groups.append(
            {
                "key": "other",
                "label": "Other",
                "items": leftovers,
            }
        )

    early_combo_count = len(spellbook_early)
    late_combo_count = len(spellbook_late)
    three_card_combo_count = len(spellbook_three_card)
    avg_cmc = (nonland_cmc_sum / nonland_count) if nonland_count else None

    count = {key: bucket.count for key, bucket in buckets.items()}
    total_spellbook_combos = count["spellbook_combos"]
    instant_win_combo_count = sum(1 for combo in spellbook_early if "instant_win" in combo.result_categories)
    early_instant_combo_count = instant_win_combo_count

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
            applied = [t for t in thresholds if isinstance(t, dict) and metric_count >= int(t.get("min") or 0)]
            applied_for_floor = [t for t in applied if int(t.get("bracket") or 0) <= 4]
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

    wizard_cfg = ruleset.get("wizard_signals") if isinstance(ruleset, dict) else {}
    if not isinstance(wizard_cfg, dict):
        wizard_cfg = {}

    def _threshold_floor(count_value: int, thresholds) -> int:
        floor_val = 1
        for rule in thresholds or []:
            if not isinstance(rule, dict):
                continue
            if count_value >= int(rule.get("min") or 0):
                floor_val = max(floor_val, int(rule.get("bracket") or 0))
        return max(floor_val, 1)

    def _severity_from_floor(floor_val: int, present: bool) -> Optional[str]:
        if not present:
            return None
        if floor_val >= 5:
            return "high"
        if floor_val >= 4:
            return "high"
        if floor_val >= 3:
            return "medium"
        return "low"

    commander_combo_based = False
    if commander_name:
        commander_key = commander_name.casefold()
        for combo in spellbook_early + spellbook_late + spellbook_three_card:
            if any(commander_key == name.casefold() for name in combo.cards):
                commander_combo_based = True
                break

    extra_turns_thresholds = (
        (wizard_cfg.get("extra_turns") or {}).get("thresholds")
        or (ruleset_metrics.get("extra_turn_loops") or {}).get("thresholds")
        or []
    )
    mass_land_thresholds = (
        (wizard_cfg.get("mass_land_destruction") or {}).get("thresholds")
        or (ruleset_metrics.get("mass_land_denial") or {}).get("thresholds")
        or []
    )
    game_changer_thresholds = (
        (wizard_cfg.get("game_changers") or {}).get("thresholds")
        or (ruleset_metrics.get("game_changers") or {}).get("thresholds")
        or []
    )
    combo_thresholds = (
        (wizard_cfg.get("combos") or {}).get("thresholds")
        or (ruleset_metrics.get("two_card_infinite_combos") or {}).get("thresholds")
        or []
    )
    early_game_thresholds = (wizard_cfg.get("early_game") or {}).get("thresholds") or [
        {"min": 1, "bracket": 3},
        {"min": 3, "bracket": 4},
    ]
    late_game_thresholds = (wizard_cfg.get("late_game") or {}).get("thresholds") or [
        {"min": 3, "bracket": 2},
        {"min": 5, "bracket": 3},
        {"min": 7, "bracket": 4},
    ]
    cedh_thresholds = (wizard_cfg.get("cedh_staples") or {}).get("thresholds") or [
        {"min": 2, "bracket": 4},
        {"min": 4, "bracket": 5},
    ]
    combo_cfg = wizard_cfg.get("combos") or {}
    if not isinstance(combo_cfg, dict):
        combo_cfg = {}

    wizard_signals: Dict[str, Dict[str, Any]] = {}

    def _add_wizard_signal(
        key: str,
        count_value: int,
        floor_val: int,
        explanation: str,
        present: Optional[bool] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        is_present = present if present is not None else count_value > 0
        wizard_signals[key] = {
            "present": is_present,
            "severity": _severity_from_floor(floor_val, is_present),
            "bracket_floor": floor_val if is_present else 1,
            "explanation": explanation,
            "count": count_value,
        }
        if details:
            wizard_signals[key]["details"] = details

    extra_turns_count = count.get("extra_turns", 0)
    extra_turns_floor = _threshold_floor(extra_turns_count, extra_turns_thresholds)
    _add_wizard_signal(
        "extra_turns",
        extra_turns_count,
        extra_turns_floor,
        f"{extra_turns_count} extra turn effect{'s' if extra_turns_count != 1 else ''} detected."
        if extra_turns_count
        else "No extra turn effects detected.",
    )

    mass_land_count = count.get("mass_land", 0)
    mass_land_floor = _threshold_floor(mass_land_count, mass_land_thresholds)
    _add_wizard_signal(
        "mass_land_destruction",
        mass_land_count,
        mass_land_floor,
        f"{mass_land_count} mass land denial piece{'s' if mass_land_count != 1 else ''} detected."
        if mass_land_count
        else "No mass land denial pieces detected.",
    )

    game_changer_count = count.get("game_changers", 0)
    game_changer_floor = _threshold_floor(game_changer_count, game_changer_thresholds)
    _add_wizard_signal(
        "game_changers",
        game_changer_count,
        game_changer_floor,
        f"{game_changer_count} Game Changer card{'s' if game_changer_count != 1 else ''} detected."
        if game_changer_count
        else "No Game Changers detected.",
    )

    early_game_count = fast_mana_density
    early_game_floor = _threshold_floor(early_game_count, early_game_thresholds)
    _add_wizard_signal(
        "early_game",
        early_game_count,
        early_game_floor,
        f"{early_game_count} fast mana source{'s' if early_game_count != 1 else ''} detected."
        if early_game_count
        else "No fast mana acceleration detected.",
        details={"fast_mana": early_game_count},
    )

    late_game_count = advantage_repeatable
    late_game_floor = _threshold_floor(late_game_count, late_game_thresholds)
    _add_wizard_signal(
        "late_game",
        late_game_count,
        late_game_floor,
        "Repeatable card advantage and recursion are present."
        if late_game_count
        else "No repeatable card advantage engines detected.",
        details={
            "repeatable": advantage_repeatable,
            "burst": advantage_burst,
            "commander_based": advantage_commander_based,
        },
    )

    cedh_count = count.get("cedh_signatures", 0) + (1 if commander_name and commander_name in CEDH_COMMANDERS else 0)
    cedh_floor = _threshold_floor(cedh_count, cedh_thresholds)
    _add_wizard_signal(
        "cedh_staples",
        cedh_count,
        cedh_floor,
        "cEDH staple density suggests high-power intent." if cedh_count else "No cEDH staple signals detected.",
        details={"commander_based": commander_name in CEDH_COMMANDERS if commander_name else False},
    )

    combo_floor = _threshold_floor(early_combo_count, combo_thresholds)
    instant_floor = int(combo_cfg.get("instant_win_floor") or 5)
    three_card_floor = int(combo_cfg.get("three_card_floor") or 4)
    three_card_min = int(combo_cfg.get("three_card_min") or 2)
    instant_win_with_fast_mana = instant_win_combo_count > 0 and early_game_count > 0
    if instant_win_with_fast_mana:
        combo_floor = max(combo_floor, instant_floor)
    if three_card_combo_count >= three_card_min:
        combo_floor = max(combo_floor, three_card_floor)
    combo_present = (
        early_combo_count > 0
        or instant_win_combo_count > 0
        or three_card_combo_count > 0
        or commander_combo_based
    )
    _add_wizard_signal(
        "combos",
        early_combo_count,
        combo_floor,
        "Commander Spellbook combo lines detected." if combo_present else "No Commander Spellbook combos detected.",
        present=combo_present,
        details={
            "instant_win": instant_win_combo_count,
            "three_card": three_card_combo_count,
            "commander_based": commander_combo_based,
        },
    )

    wizard_bracket_floor = max((signal["bracket_floor"] for signal in wizard_signals.values()), default=1)

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
            "Wizard signals and ruleset metrics set the hard bracket floor; Brackets 4/5 are split by score (15+ = Bracket 5, under 15 = Bracket 4)."
        )

    def add_component(key: str, value: float, reason: str) -> None:
        nonlocal score
        if abs(value) < 1e-6:
            return
        score += value
        score_breakdown.append(
            {
                "key": key,
                "points": round(value, 2),
                "reason": reason,
            }
        )

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
            "curve": [{"count": float(threshold), "points": float(value)} for threshold, value in signal.curve],
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
            detail["notes"] = "Late-game and three-card combos are surfaced in the breakdown but do not add to this score."
            reason_parts: List[str] = []
            components_meta: List[Dict[str, Any]] = []

            base_score = _score_piecewise(metric_count, signal.curve)
            points = base_score
            if metric_count:
                reason_parts.append(f"{metric_count} Commander Spellbook combo{'s' if metric_count != 1 else ''}")
            if base_score:
                components_meta.append({"label": "Combo density", "points": round(base_score, 2)})

            early_combo_bonus = max(0, early_combo_count - early_instant_combo_count) * 0.9
            if early_combo_count:
                reason_parts.append(f"{early_combo_count} early combo{'s' if early_combo_count != 1 else ''}")
            if early_combo_bonus:
                points += early_combo_bonus
                components_meta.append({"label": "Early combo pressure", "points": round(early_combo_bonus, 2)})

            early_instant_bonus = early_instant_combo_count * 1.5
            if early_instant_combo_count:
                reason_parts.append(
                    f"{early_instant_combo_count} early instant-win line{'s' if early_instant_combo_count != 1 else ''}"
                )
            if early_instant_bonus:
                points += early_instant_bonus
                components_meta.append({"label": "Early instant-win lines", "points": round(early_instant_bonus, 2)})

            if points:
                reason_text = ", ".join(reason_parts) if reason_parts else "Commander Spellbook combos detected"
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
        "benefits": [{"threshold": float(threshold), "points": float(value), "comparison": "<="} for threshold, value in AVG_CMC_BENEFITS],
        "penalties": [{"threshold": float(threshold), "points": float(value), "comparison": ">="} for threshold, value in AVG_CMC_PENALTIES],
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
        "description": "Known cEDH commanders receive a safety buffer so competitive shells aren't seeded into low brackets.",
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
    cedh_adjacent = False

    if ruleset_metrics:
        ruleset_floor_val = max(wizard_bracket_floor or 1, ruleset_floor or 1)
        min_signals_for_bump = int(ruleset_reinforcement.get("min_signals_for_bump") or 2)
        min_floor_for_bump = int(ruleset_reinforcement.get("min_floor_for_bump") or 2)
        max_bracket = int(ruleset_reinforcement.get("max_bracket") or 4)
        signals_at_floor = sum(
            1
            for signal in wizard_signals.values()
            if signal.get("present") and signal.get("bracket_floor") == ruleset_floor_val
        )
        ruleset_reinforced = ruleset_floor_val
        if ruleset_floor_val >= min_floor_for_bump and signals_at_floor >= min_signals_for_bump:
            ruleset_reinforced = min(ruleset_floor_val + 1, max_bracket)
        ruleset_reinforced = max(ruleset_reinforced, ruleset_floor_val)
        ruleset_level = ruleset_reinforced

        b5_weights = ruleset_bracket5.get("weights") if isinstance(ruleset_bracket5, dict) else {}
        if not isinstance(b5_weights, dict):
            b5_weights = {}
        b5_thresholds = ruleset_bracket5.get("thresholds") if isinstance(ruleset_bracket5, dict) else {}
        if not isinstance(b5_thresholds, dict):
            b5_thresholds = {}
        remain_at_4 = float(b5_thresholds.get("remain_at_4") or 0)
        min_signals_for_5 = int(b5_thresholds.get("min_signals_for_5") or 2)
        bracket5_score = 0.0
        bracket5_signals = 0
        for key, weight in b5_weights.items():
            metric_value = fast_mana_density if key == "fast_mana_density" else ruleset_metric_counts.get(key, 0)
            if metric_value:
                bracket5_score += float(weight) * float(metric_value)
                bracket5_signals += 1
        if ruleset_floor_val >= 5:
            ruleset_level = 5
        elif remain_at_4 and bracket5_score >= remain_at_4 and bracket5_signals >= min_signals_for_5:
            ruleset_level = max(ruleset_level or 0, 4)
            cedh_adjacent = True

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

    if level in (4, 5):
        level = 5 if score >= BRACKET45_SCORE_SPLIT else 4

    label = BRACKET_LABELS.get(level, "Unknown")

    summary_points: List[str] = []
    if wizard_signals.get("extra_turns", {}).get("present"):
        summary_points.append(f"{extra_turns_count} extra turn effect{'s' if extra_turns_count != 1 else ''}")
    if wizard_signals.get("mass_land_destruction", {}).get("present"):
        summary_points.append(f"{mass_land_count} mass land denial piece{'s' if mass_land_count != 1 else ''}")
    if wizard_signals.get("game_changers", {}).get("present"):
        summary_points.append(f"{game_changer_count} Game Changer card{'s' if game_changer_count != 1 else ''}")
    if wizard_signals.get("early_game", {}).get("present"):
        summary_points.append(f"{early_game_count} fast mana source{'s' if early_game_count != 1 else ''}")
    if wizard_signals.get("late_game", {}).get("present"):
        summary_points.append(f"{late_game_count} repeatable advantage engine{'s' if late_game_count != 1 else ''}")
    if wizard_signals.get("combos", {}).get("present"):
        combo_bits = []
        if instant_win_combo_count:
            combo_bits.append(f"{instant_win_combo_count} instant-win")
        if three_card_combo_count:
            combo_bits.append(f"{three_card_combo_count} three-card")
        if not combo_bits and early_combo_count:
            combo_bits.append(f"{early_combo_count} spellbook")
        combo_summary = ", ".join(combo_bits) if combo_bits else "combo lines"
        summary_points.append(f"Combos: {combo_summary}")
    if wizard_signals.get("cedh_staples", {}).get("present"):
        summary_points.append(f"{cedh_count} cEDH staple signal{'s' if cedh_count != 1 else ''}")
    if mana_base_optimized:
        summary_points.append("Mana base optimized")
    if cedh_adjacent:
        summary_points.append("High-Power (cEDH-adjacent)")

    summary_cards: Dict[str, List[str]] = {key: bucket.names for key, bucket in buckets.items() if bucket.entries}
    if buckets["nonland_tutors"].entries and "tutors" not in summary_cards:
        summary_cards["tutors"] = buckets["nonland_tutors"].names

    summary_tooltip = (
        " | ".join(
            f"{key.replace('_', ' ').title()}: " + ", ".join(names[:6]) + ("..." if len(names) > 6 else "")
            for key, names in summary_cards.items()
        )
        if summary_cards
        else None
    )

    metrics_payload = {
        "extra_turns": count.get("extra_turns", 0),
        "mass_land": count.get("mass_land", 0),
        "game_changers": count.get("game_changers", 0),
        "early_game": early_game_count,
        "late_game": late_game_count,
        "cedh_staples": cedh_count,
        "instant_win_combos": instant_win_combo_count,
        "three_card_combos": three_card_combo_count,
        "spellbook_combos": count.get("spellbook_combos", 0),
        "two_card_infinite_combos": early_combo_count,
        "card_advantage": count.get("card_advantage", 0),
        "efficient_interaction": count.get("efficient_interaction", 0),
        "mana_base_optimization": 1 if mana_base_optimized else 0,
        "tutors": count.get("nonland_tutors", 0),
        "cedh_signatures": count.get("cedh_signatures", 0),
        "instant_win": count.get("instant_win", 0),
        "zero_cmc_mana": count.get("zero_cmc_mana", 0),
        "nonland_tutors": count.get("nonland_tutors", 0),
        "land_tutors": count.get("land_tutors", 0),
    }

    if ruleset_metrics:
        score_methodology["ruleset"] = {
            "version": ruleset.get("version"),
            "floor": ruleset_floor_val,
            "metric_floor": ruleset_floor,
            "reinforced": ruleset_reinforced,
            "wizard_bracket_floor": wizard_bracket_floor,
            "metric_brackets": ruleset_metric_brackets,
            "triggers": ruleset_triggers,
            "mana_base_optimized": mana_base_optimized,
            "nonbasic_lands": nonbasic_land_count,
            "nonbasic_ratio": round(nonbasic_ratio, 3) if land_count else None,
            "bracket5_score": round(bracket5_score, 2) if bracket5_score is not None else None,
            "bracket5_signals": bracket5_signals,
            "cedh_adjacent": cedh_adjacent,
        }

    score_methodology["wizard_signals"] = wizard_signals
    score_methodology["card_advantage_breakdown"] = {
        "burst": advantage_burst,
        "repeatable": advantage_repeatable,
        "commander_based": advantage_commander_based,
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
