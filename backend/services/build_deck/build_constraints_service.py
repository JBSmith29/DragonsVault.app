"""Constraints for Build-A-Deck legality checks (color identity, commander, singleton)."""

from __future__ import annotations

import logging

from services import scryfall_cache as sc
from services.commander_utils import split_commander_oracle_ids

_LOG = logging.getLogger(__name__)


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


def commander_color_mask(commander_oracle_id: str | None) -> tuple[int, bool]:
    mask = 0
    resolved = False
    for oid in split_commander_oracle_ids(commander_oracle_id):
        pr = _preferred_print(oid)
        if not pr:
            continue
        resolved = True
        meta = sc.metadata_from_print(pr)
        mask |= int(meta.get("color_identity_mask") or 0)
    return mask, resolved


def card_color_mask(card_oracle_id: str) -> tuple[int, bool]:
    pr = _preferred_print(card_oracle_id)
    if not pr:
        return 0, False
    meta = sc.metadata_from_print(pr)
    return int(meta.get("color_identity_mask") or 0), True


def commander_is_legal(commander_oracle_id: str | None) -> tuple[bool, str | None]:
    if not commander_oracle_id:
        return False, "Commander is required."
    legal = True
    for oid in split_commander_oracle_ids(commander_oracle_id):
        pr = _preferred_print(oid)
        if not pr:
            _LOG.warning("Commander legality check skipped (missing print) for %s.", oid)
            continue
        type_line = (pr.get("type_line") or "").lower()
        oracle_text = (sc.metadata_from_print(pr).get("oracle_text") or "").lower()
        if "can be your commander" in oracle_text:
            continue
        if "legendary" in type_line and ("creature" in type_line or "planeswalker" in type_line):
            continue
        legal = False
        break
    if not legal:
        return False, "Selected commander is not legal."
    return True, None


def enforce_color_identity(commander_oracle_id: str | None, card_oracle_id: str) -> tuple[bool, str | None]:
    commander_mask, commander_ok = commander_color_mask(commander_oracle_id)
    card_mask, card_ok = card_color_mask(card_oracle_id)
    if commander_ok and card_ok and commander_mask & card_mask != card_mask:
        return False, "Card color identity is outside the commander identity."
    return True, None


def card_allows_multiple(card_oracle_id: str) -> tuple[bool, bool]:
    pr = _preferred_print(card_oracle_id)
    if not pr:
        _LOG.warning("Singleton check skipped (missing print) for %s.", card_oracle_id)
        return True, False
    type_line = (pr.get("type_line") or "").lower()
    if "basic land" in type_line:
        return True, True
    oracle_text = (sc.metadata_from_print(pr).get("oracle_text") or "").lower()
    if "any number of cards named" in oracle_text:
        return True, True
    if "a deck can have any number of cards named" in oracle_text:
        return True, True
    return False, True


def validate_singleton(card_oracle_id: str, existing_oracle_ids: set[str]) -> tuple[bool, str | None]:
    if card_oracle_id not in existing_oracle_ids:
        return True, None
    allows_multiple, known = card_allows_multiple(card_oracle_id)
    if not known:
        return True, None
    if allows_multiple:
        return True, None
    return False, "Singleton rule violation."


__all__ = [
    "commander_color_mask",
    "card_color_mask",
    "commander_is_legal",
    "enforce_color_identity",
    "card_allows_multiple",
    "validate_singleton",
]
