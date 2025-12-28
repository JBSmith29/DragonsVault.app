"""Land helpers for Build-A-Deck (targets and basic land suggestions)."""

from __future__ import annotations

import logging
from functools import lru_cache

from sqlalchemy import func

from extensions import db
from models import Card, Folder, FolderRole
from services import scryfall_cache as sc
from services.commander_utils import split_commander_oracle_ids
from . import build_constraints_service as constraints

_LOG = logging.getLogger(__name__)

LAND_TARGET_MIN = 36
LAND_TARGET_MAX = 38

_BASIC_LANDS_BY_COLOR = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
}
_COLORLESS_LAND = "Wastes"


def land_target_range() -> tuple[int, int]:
    return LAND_TARGET_MIN, LAND_TARGET_MAX


def _commander_colors(commander_oracle_id: str | None) -> list[str]:
    colors: set[str] = set()
    for oid in split_commander_oracle_ids(commander_oracle_id):
        pr = _preferred_print(oid)
        if not pr:
            continue
        meta = sc.metadata_from_print(pr)
        for color in meta.get("color_identity") or []:
            colors.add(str(color).upper())
    order = ["W", "U", "B", "R", "G"]
    return [c for c in order if c in colors]


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


@lru_cache(maxsize=64)
def _oracle_id_for_basic(name: str) -> str | None:
    try:
        return sc.unique_oracle_by_name(name)
    except Exception as exc:
        _LOG.warning("Failed to resolve oracle id for basic land %s: %s", name, exc)
        return None


def _collection_folder_ids(owner_user_id: int | None) -> list[int]:
    if not owner_user_id:
        return []
    rows = (
        db.session.query(FolderRole.folder_id)
        .join(Folder, Folder.id == FolderRole.folder_id)
        .filter(
            FolderRole.role == FolderRole.ROLE_COLLECTION,
            Folder.owner_user_id == owner_user_id,
        )
        .all()
    )
    return [row[0] for row in rows]


def _owned_counts(owner_user_id: int | None, oracle_ids: list[str]) -> dict[str, int]:
    if not owner_user_id or not oracle_ids:
        return {}
    collection_ids = _collection_folder_ids(owner_user_id)
    if not collection_ids:
        return {}
    rows = (
        db.session.query(Card.oracle_id, func.coalesce(func.sum(Card.quantity), 0))
        .filter(Card.folder_id.in_(collection_ids), Card.oracle_id.in_(oracle_ids))
        .group_by(Card.oracle_id)
        .all()
    )
    return {str(oid).strip(): int(total or 0) for oid, total in rows if oid}


def basic_land_options(commander_oracle_id: str | None) -> list[dict]:
    if not commander_oracle_id:
        return []
    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for land suggestions: %s", exc)
        return []
    colors = _commander_colors(commander_oracle_id)
    if not colors:
        colors = []
    names = [_BASIC_LANDS_BY_COLOR[c] for c in colors if c in _BASIC_LANDS_BY_COLOR]
    if not names:
        names = [_COLORLESS_LAND]
    options: list[dict] = []
    for name in names:
        oracle_id = _oracle_id_for_basic(name)
        if not oracle_id:
            continue
        options.append({"name": name, "oracle_id": oracle_id})
    return options


def basic_land_recommendations(
    *,
    commander_oracle_id: str | None,
    owner_user_id: int | None,
    deck_oracle_ids: set[str],
    needed: int,
) -> list[dict]:
    if needed <= 0:
        return []
    options = basic_land_options(commander_oracle_id)
    if not options:
        return []
    oracle_ids = [opt["oracle_id"] for opt in options if opt.get("oracle_id")]
    owned_counts = _owned_counts(owner_user_id, oracle_ids)

    results: list[dict] = []
    for opt in options:
        oracle_id = (opt.get("oracle_id") or "").strip()
        if not oracle_id:
            continue
        ok, message = constraints.enforce_color_identity(commander_oracle_id, oracle_id)
        allows_multiple, _ = constraints.card_allows_multiple(oracle_id)
        in_deck = oracle_id in deck_oracle_ids
        can_add = ok and (allows_multiple or not in_deck)
        results.append(
            {
                "oracle_id": oracle_id,
                "name": opt.get("name") or oracle_id,
                "owned_qty": int(owned_counts.get(oracle_id, 0)),
                "in_deck": in_deck,
                "score": 100.0,
                "reasons": ["Needs lands"],
                "legal": ok,
                "legal_reason": message if not ok else None,
                "can_add": can_add,
                "disabled_reason": None if can_add else (message or "Already in deck."),
            }
        )
    return results


def quick_add_buttons(
    *,
    commander_oracle_id: str | None,
    land_count: int,
) -> list[dict]:
    target_min, _ = land_target_range()
    if land_count >= target_min:
        return []
    options = basic_land_options(commander_oracle_id)
    if not options:
        return []
    buttons: list[dict] = []
    for opt in options:
        oracle_id = opt.get("oracle_id")
        if not oracle_id:
            continue
        buttons.append({"label": f"+1 {opt['name']}", "oracle_id": oracle_id, "quantity": 1})
        buttons.append({"label": f"+5 {opt['name']}", "oracle_id": oracle_id, "quantity": 5})
    return buttons


__all__ = [
    "land_target_range",
    "basic_land_options",
    "basic_land_recommendations",
    "quick_add_buttons",
]
