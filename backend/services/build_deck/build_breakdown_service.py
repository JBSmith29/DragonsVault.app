"""Deck breakdown helpers for Build-A-Deck (expected vs current)."""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import OracleCoreRoleTag, DeckTagCoreRoleSynergy
from services import scryfall_cache as sc
from services.edhrec_cache_service import get_commander_synergy

_LOG = logging.getLogger(__name__)

_CATEGORIES = [
    ("Lands", "land"),
    ("Ramp", "ramp"),
    ("Draw", "draw"),
    ("Removal", "removal"),
    ("Wipes", "wipe"),
    ("Creatures", "creature"),
    ("Other", "other"),
]

_ROLE_PRIORITY = ["ramp", "draw", "removal", "wipe"]


def _preferred_print(oracle_id: str) -> dict | None:
    prints = sc.prints_for_oracle(oracle_id) or ()
    if not prints:
        return None
    for pr in prints:
        if pr.get("digital"):
            continue
        if (pr.get("lang") or "en").lower() == "en":
            return pr
    return prints[0]


def _type_line(oracle_id: str) -> str:
    pr = _preferred_print(oracle_id)
    if not pr:
        return ""
    meta = sc.metadata_from_print(pr)
    return meta.get("type_line") or ""


def _role_map(oracle_ids: list[str]) -> dict[str, set[str]]:
    if not oracle_ids:
        return {}
    try:
        rows = (
            db.session.query(OracleCoreRoleTag.oracle_id, OracleCoreRoleTag.role)
            .filter(OracleCoreRoleTag.oracle_id.in_(oracle_ids))
            .all()
        )
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.warning("Deck breakdown role lookup failed: %s", exc)
        return {}
    out: dict[str, set[str]] = {}
    for oid, role in rows:
        if not oid or not role:
            continue
        out.setdefault(str(oid).strip(), set()).add(str(role).strip())
    return out


def _categorize_card(type_line: str, roles: set[str]) -> str:
    lower = (type_line or "").lower()
    if "land" in lower:
        return "Lands"
    for role in _ROLE_PRIORITY:
        if role in roles:
            return role.title() if role != "wipe" else "Wipes"
    if "creature" in lower:
        return "Creatures"
    return "Other"


def _scale_expected(counts: dict[str, int], target_total: int) -> dict[str, int]:
    total = sum(counts.values()) or 1
    raw = {cat: (counts.get(cat, 0) / total) * target_total for cat, _ in _CATEGORIES}
    floors = {cat: int(raw.get(cat, 0)) for cat, _ in _CATEGORIES}
    remainder = target_total - sum(floors.values())
    if remainder > 0:
        sorted_cats = sorted(
            raw.keys(),
            key=lambda cat: (raw.get(cat, 0) - floors.get(cat, 0)),
            reverse=True,
        )
        for cat in sorted_cats:
            if remainder <= 0:
                break
            floors[cat] = floors.get(cat, 0) + 1
            remainder -= 1
    return floors


def _tag_role_bonuses(tags: list[str]) -> dict[str, int]:
    if not tags:
        return {}
    try:
        rows = (
            db.session.query(DeckTagCoreRoleSynergy.role, func.max(DeckTagCoreRoleSynergy.weight))
            .filter(DeckTagCoreRoleSynergy.deck_tag.in_(tags))
            .group_by(DeckTagCoreRoleSynergy.role)
            .all()
        )
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.warning("Deck breakdown tag weights unavailable: %s", exc)
        return {}
    bonuses: dict[str, int] = {}
    for role, weight in rows:
        if not role or weight is None:
            continue
        bonus = int(round(float(weight)))
        if bonus <= 0:
            continue
        bonuses[role] = min(bonus, 2)
    return bonuses


def expected_breakdown(
    *,
    commander_oracle_id: str,
    tags: list[str] | None = None,
    sample_size: int = 140,
    target_total: int = 100,
) -> dict[str, int]:
    if not commander_oracle_id:
        return {label: 0 for label, _ in _CATEGORIES}
    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for breakdown: %s", exc)
    recs = get_commander_synergy(commander_oracle_id, tags or [])
    if not recs:
        return {label: 0 for label, _ in _CATEGORIES}
    recs = recs[:sample_size]
    oracle_ids = [str(rec.get("oracle_id") or "").strip() for rec in recs if rec.get("oracle_id")]
    role_map = _role_map(oracle_ids)
    counts = defaultdict(int)
    for oid in oracle_ids:
        type_line = _type_line(oid)
        roles = role_map.get(oid, set())
        category = _categorize_card(type_line, roles)
        counts[category] += 1

    expected = _scale_expected(counts, target_total)
    bonuses = _tag_role_bonuses(tags or [])
    if bonuses:
        for role, bonus in bonuses.items():
            label = role.title() if role != "wipe" else "Wipes"
            expected[label] = expected.get(label, 0) + bonus
        overflow = sum(expected.values()) - target_total
        if overflow > 0:
            for label in ["Other", "Creatures", "Lands"]:
                if overflow <= 0:
                    break
                available = expected.get(label, 0)
                if available <= 0:
                    continue
                take = min(available, overflow)
                expected[label] = available - take
                overflow -= take
    return expected


def current_breakdown(deck_cards: list[dict]) -> dict[str, int]:
    if not deck_cards:
        return {label: 0 for label, _ in _CATEGORIES}
    oracle_ids = [str(card.get("oracle_id") or "").strip() for card in deck_cards if card.get("oracle_id")]
    role_map = _role_map(oracle_ids)
    counts = defaultdict(int)
    for card in deck_cards:
        oracle_id = (card.get("oracle_id") or "").strip()
        if not oracle_id:
            continue
        qty = int(card.get("quantity") or 0) or 1
        type_line = card.get("type_line") or ""
        roles = role_map.get(oracle_id, set())
        category = _categorize_card(type_line, roles)
        counts[category] += qty
    return {label: int(counts.get(label, 0)) for label, _ in _CATEGORIES}


def breakdown_comparison(expected: dict[str, int], current: dict[str, int]) -> list[dict]:
    rows: list[dict] = []
    for label, _ in _CATEGORIES:
        exp = int(expected.get(label, 0) or 0)
        cur = int(current.get(label, 0) or 0)
        if exp <= 0:
            status = "ok" if cur == 0 else "warn"
        else:
            ratio = cur / exp if exp else 0
            if 0.85 <= ratio <= 1.15:
                status = "ok"
            elif 0.6 <= ratio <= 1.4:
                status = "warn"
            else:
                status = "bad"
        rows.append(
            {
                "label": label,
                "expected": exp,
                "current": cur,
                "status": status,
                "status_class": _status_class(status),
            }
        )
    return rows


def _status_class(status: str) -> str:
    if status == "ok":
        return "text-bg-success"
    if status == "warn":
        return "text-bg-warning"
    return "text-bg-danger"


__all__ = ["expected_breakdown", "current_breakdown", "breakdown_comparison"]
