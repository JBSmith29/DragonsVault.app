"""Canonical role derivation for Build-A-Deck recommendations."""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Iterable

from extensions import db
from models import OracleCardRole
from services import scryfall_cache as sc
from services.core_role_logic import derive_core_roles

_LOG = logging.getLogger(__name__)

CANONICAL_ROLES = (
    "ramp",
    "mana_fixing",
    "draw",
    "card_selection",
    "engine",
    "removal",
    "board_wipe",
    "protection",
    "tutor",
    "sacrifice_outlet",
    "token_producer",
    "wincon",
    "utility",
    "land",
)

_ROLE_MAP = {
    "ramp": "ramp",
    "fixing": "mana_fixing",
    "mana_fixing": "mana_fixing",
    "treasure": "ramp",
    "draw": "draw",
    "selection": "card_selection",
    "card_selection": "card_selection",
    "advantage": "card_selection",
    "engine": "engine",
    "enabler": "engine",
    "payoff": "engine",
    "removal": "removal",
    "wipe": "board_wipe",
    "board_wipe": "board_wipe",
    "protection": "protection",
    "tutor": "tutor",
    "sac_outlet": "sacrifice_outlet",
    "sacrifice_outlet": "sacrifice_outlet",
    "token": "token_producer",
    "token_producer": "token_producer",
    "finisher": "wincon",
    "go_wide": "wincon",
    "go_tall": "wincon",
    "voltron": "wincon",
    "land": "land",
}

_FIXING_RE = re.compile(r"\bany color\b|\bmana of any color\b", re.IGNORECASE)
_SCRY_RE = re.compile(r"\bscry\b|\blook at the top\b|\breveal the top\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\bcreate\b.*\btoken\b|\btoken\b", re.IGNORECASE)
_SAC_RE = re.compile(r"\bsacrifice\b", re.IGNORECASE)
_WINCON_RE = re.compile(r"\bwin the game\b|\bloses the game\b", re.IGNORECASE)


def ensure_tables() -> None:
    try:
        db.metadata.create_all(db.engine, tables=[OracleCardRole.__table__])
    except Exception as exc:
        _LOG.error("Failed to ensure oracle card role table: %s", exc)


def canonicalize_role(role: str | None) -> str | None:
    raw = (role or "").strip()
    if not raw:
        return None
    key = raw.casefold().replace("-", "_").replace(" ", "_")
    return _ROLE_MAP.get(key)


def role_label(role: str) -> str:
    return (role or "").replace("_", " ").title().strip()


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


def _oracle_meta(oracle_id: str) -> dict:
    pr = _preferred_print(oracle_id)
    if not pr:
        return {}
    meta = sc.metadata_from_print(pr)
    meta["name"] = pr.get("name") or meta.get("name")
    return meta


def _derive_roles_from_meta(meta: dict) -> set[str]:
    oracle_text = meta.get("oracle_text") or ""
    type_line = meta.get("type_line") or ""
    name = meta.get("name") or ""

    roles: set[str] = set()
    for role in derive_core_roles(oracle_text=oracle_text, type_line=type_line, name=name):
        mapped = canonicalize_role(role)
        if mapped:
            roles.add(mapped)

    if "land" in type_line.lower():
        roles.add("land")

    if _FIXING_RE.search(oracle_text):
        roles.add("mana_fixing")

    if _SCRY_RE.search(oracle_text):
        roles.add("card_selection")

    if _TOKEN_RE.search(oracle_text):
        roles.add("token_producer")

    if _SAC_RE.search(oracle_text):
        roles.add("sacrifice_outlet")

    if _WINCON_RE.search(oracle_text):
        roles.add("wincon")

    if not roles:
        roles.add("utility")

    return roles


def _existing_roles(oracle_ids: list[str]) -> dict[str, set[str]]:
    if not oracle_ids:
        return {}
    rows = (
        db.session.query(OracleCardRole.oracle_id, OracleCardRole.role)
        .filter(OracleCardRole.oracle_id.in_(oracle_ids))
        .all()
    )
    role_map: dict[str, set[str]] = {}
    for oid, role in rows:
        if not oid or not role:
            continue
        role_map.setdefault(str(oid).strip(), set()).add(str(role).strip())
    return role_map


def get_roles_for_oracles(oracle_ids: Iterable[str], *, persist: bool = True) -> dict[str, set[str]]:
    ids = sorted({str(oid).strip() for oid in oracle_ids if oid})
    if not ids:
        return {}
    ensure_tables()
    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for role derivation: %s", exc)

    role_map = _existing_roles(ids)
    missing = [oid for oid in ids if oid not in role_map]
    new_rows: list[OracleCardRole] = []

    for oid in missing:
        meta = _oracle_meta(oid)
        if not meta:
            role_map[oid] = {"utility"}
            continue

        roles = _derive_roles_from_meta(meta)
        role_map[oid] = roles
        if persist:
            for role in sorted(roles):
                new_rows.append(OracleCardRole(oracle_id=oid, role=role))

    if persist and new_rows:
        try:
            db.session.bulk_save_objects(new_rows)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            _LOG.warning("Failed to persist oracle roles: %s", exc)

    return role_map


def role_counts(oracle_ids: Iterable[str], *, persist: bool = True) -> dict[str, int]:
    role_map = get_roles_for_oracles(oracle_ids, persist=persist)
    counts: Counter[str] = Counter()
    for roles in role_map.values():
        counts.update(roles)
    return {role: int(count) for role, count in counts.items()}


__all__ = [
    "CANONICAL_ROLES",
    "canonicalize_role",
    "get_roles_for_oracles",
    "role_counts",
    "role_label",
]
