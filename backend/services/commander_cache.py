"""Persistent caching helpers for commander bracket evaluations."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Optional

from flask import current_app

from extensions import db
from models import CommanderBracketCache
from utils.time import utcnow

__all__ = [
    "compute_bracket_signature",
    "get_cached_bracket",
    "store_cached_bracket",
]


def _normalize_epoch(epoch: int) -> int:
    """
    Keep cache_epoch within 32-bit signed integer to satisfy DB schema.
    """
    try:
        val = int(epoch or 0)
    except Exception:
        val = 0
    max_int = 2_147_483_647
    return abs(val) % max_int


def compute_bracket_signature(
    cards: Iterable[Dict[str, Any]] | None,
    commander: Optional[Dict[str, Any]],
    *,
    epoch: int,
) -> str:
    """
    Return a stable SHA1 digest for the bracket inputs.

    Uses card names, oracle text, mana values, and quantities plus the commander
    identity and current cache epoch to ensure the signature changes whenever deck
    composition or supporting data does.
    """
    normalized_cards: list[Dict[str, Any]] = []
    for card in cards or ():
        name = str(card.get("name") or "")
        oracle_text = str(card.get("oracle_text") or "")
        type_line = str(card.get("type_line") or "")
        mana_cost = str(card.get("mana_cost") or "")
        mana_value = card.get("mana_value")
        produced = card.get("produced_mana")
        if isinstance(produced, (list, tuple, set)):
            produced_values = sorted(str(item) for item in produced if item is not None)
        else:
            produced_values = [str(produced)] if produced is not None else []
        normalized_cards.append(
            {
                "name": name,
                "oracle": oracle_text,
                "type": type_line,
                "mana_cost": mana_cost,
                "mana_value": float(mana_value) if isinstance(mana_value, (int, float)) else str(mana_value or ""),
                "quantity": int(card.get("quantity") or 0),
                "produced": produced_values,
            }
        )

    normalized_cards.sort(key=lambda item: (item["name"], item["mana_cost"], item["type"], item["mana_value"], item["quantity"]))

    commander_payload = {
        "oid": (commander or {}).get("oracle_id"),
        "name": (commander or {}).get("name"),
    }

    payload = {
        "epoch": _normalize_epoch(epoch),
        "commander": commander_payload,
        "cards": normalized_cards,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def get_cached_bracket(folder_id: Optional[int], signature: str, epoch: int) -> Optional[Dict[str, Any]]:
    """Fetch cached bracket payload for the given folder/signature if available."""
    if not folder_id:
        return None
    try:
        entry = db.session.get(CommanderBracketCache, folder_id)
    except Exception as exc:  # pragma: no cover - defensive fallback
        current_app.logger.debug("Commander bracket cache lookup failed for folder %s: %s", folder_id, exc)
        db.session.rollback()
        return None
    if not entry:
        return None
    if entry.cache_epoch != _normalize_epoch(epoch):
        return None
    if entry.card_signature != signature:
        return None
    return entry.payload or None


def store_cached_bracket(folder_id: Optional[int], signature: str, epoch: int, payload: Dict[str, Any]) -> None:
    """Persist a freshly computed bracket payload for reuse."""
    if not folder_id:
        return
    try:
        entry = db.session.get(CommanderBracketCache, folder_id)
    except Exception as exc:  # pragma: no cover - defensive fallback
        current_app.logger.warning("Unable to load commander cache row for folder %s: %s", folder_id, exc)
        db.session.rollback()
        return

    if entry is None:
        entry = CommanderBracketCache(folder_id=folder_id)
        db.session.add(entry)

    entry.card_signature = signature
    entry.cache_epoch = _normalize_epoch(epoch)
    entry.payload = payload
    entry.updated_at = utcnow()

    try:
        db.session.commit()
    except Exception as exc:  # pragma: no cover - defensive fallback
        current_app.logger.warning("Unable to persist commander bracket cache for folder %s: %s", folder_id, exc)
        db.session.rollback()
