"""Mechanic hook derivation for Build-A-Deck recommendations."""

from __future__ import annotations

import logging
import re
from typing import Iterable

from extensions import db
from models import CardMechanic
from services import scryfall_cache as sc

_LOG = logging.getLogger(__name__)

MECHANIC_LABELS = {
    "draw_hook": "Draw",
    "death_hook": "Death",
    "spellcast_hook": "Spellcasting",
    "artifact_hook": "Artifacts",
    "token_hook": "Tokens",
    "discard_hook": "Discard",
    "counter_hook": "Counters",
    "landfall_hook": "Landfall",
}

_DRAW_RE = re.compile(r"\bdraw\b.*\bcard\b|\bwhenever you draw\b", re.IGNORECASE)
_DEATH_RE = re.compile(r"\bdies\b|\bgraveyard\b", re.IGNORECASE)
_SPELLCAST_RE = re.compile(r"\bcast\b.*\bspell\b|\bwhenever you cast\b", re.IGNORECASE)
_ARTIFACT_RE = re.compile(r"\bartifact\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\btoken\b", re.IGNORECASE)
_DISCARD_RE = re.compile(r"\bdiscard\b", re.IGNORECASE)
_COUNTER_RE = re.compile(
    r"\+\d+/\+\d+\s+counter\b|\bcounter(s)? on\b|\bproliferate\b|\b(loyalty|charge|quest|poison|shield|time|stun|fate) counter\b",
    re.IGNORECASE,
)
_COUNTER_TARGET_RE = re.compile(r"\bcounter target\b", re.IGNORECASE)
_LANDFALL_RE = re.compile(r"\blandfall\b|\bland enters the battlefield\b", re.IGNORECASE)


def ensure_tables() -> None:
    try:
        db.metadata.create_all(db.engine, tables=[CardMechanic.__table__])
    except Exception as exc:
        _LOG.error("Failed to ensure card mechanics table: %s", exc)


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


def _oracle_text(oracle_id: str) -> str:
    pr = _preferred_print(oracle_id)
    if not pr:
        return ""
    meta = sc.metadata_from_print(pr)
    return meta.get("oracle_text") or ""


def _derive_mechanics(text: str) -> set[str]:
    normalized = (text or "").strip()
    if not normalized:
        return set()
    mechanics: set[str] = set()
    if _DRAW_RE.search(normalized):
        mechanics.add("draw_hook")
    if _DEATH_RE.search(normalized):
        mechanics.add("death_hook")
    if _SPELLCAST_RE.search(normalized):
        mechanics.add("spellcast_hook")
    if _ARTIFACT_RE.search(normalized):
        mechanics.add("artifact_hook")
    if _TOKEN_RE.search(normalized):
        mechanics.add("token_hook")
    if _DISCARD_RE.search(normalized):
        mechanics.add("discard_hook")
    if _COUNTER_RE.search(normalized) and not _COUNTER_TARGET_RE.search(normalized):
        mechanics.add("counter_hook")
    if _LANDFALL_RE.search(normalized):
        mechanics.add("landfall_hook")
    return mechanics


def _existing_mechanics(oracle_ids: list[str]) -> dict[str, set[str]]:
    if not oracle_ids:
        return {}
    rows = (
        db.session.query(CardMechanic.oracle_id, CardMechanic.mechanic)
        .filter(CardMechanic.oracle_id.in_(oracle_ids))
        .all()
    )
    mech_map: dict[str, set[str]] = {}
    for oid, mech in rows:
        if not oid or not mech:
            continue
        mech_map.setdefault(str(oid).strip(), set()).add(str(mech).strip())
    return mech_map


def get_mechanics_for_oracles(oracle_ids: Iterable[str], *, persist: bool = True) -> dict[str, set[str]]:
    ids = sorted({str(oid).strip() for oid in oracle_ids if oid})
    if not ids:
        return {}
    ensure_tables()
    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for mechanic derivation: %s", exc)

    mech_map = _existing_mechanics(ids)
    missing = [oid for oid in ids if oid not in mech_map]
    new_rows: list[CardMechanic] = []

    for oid in missing:
        text = _oracle_text(oid)
        mechanics = _derive_mechanics(text)
        mech_map[oid] = mechanics
        if persist:
            for mech in sorted(mechanics):
                new_rows.append(CardMechanic(oracle_id=oid, mechanic=mech))

    if persist and new_rows:
        try:
            db.session.bulk_save_objects(new_rows)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            _LOG.warning("Failed to persist card mechanics: %s", exc)

    return mech_map


def mechanic_labels(mechanics: Iterable[str]) -> list[str]:
    labels: list[str] = []
    for mech in mechanics:
        label = MECHANIC_LABELS.get(mech)
        if label:
            labels.append(label)
    return labels


__all__ = ["get_mechanics_for_oracles", "mechanic_labels", "MECHANIC_LABELS"]
