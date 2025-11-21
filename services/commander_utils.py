"""Utilities for working with commander name/oracle metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence


MAX_COMMANDERS = 2


@dataclass
class CommanderSlot:
    """Represents an ordered commander assignment."""

    name: Optional[str] = None
    oracle_id: Optional[str] = None


def _clean(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text or None


def split_commander_names(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    parts = [piece.strip() for piece in raw.split("//") if piece.strip()]
    return parts or [raw.strip()]


def split_commander_oracle_ids(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def primary_commander_name(raw: Optional[str]) -> Optional[str]:
    names = split_commander_names(raw)
    return names[0] if names else None


def primary_commander_oracle_id(raw: Optional[str]) -> Optional[str]:
    ids = split_commander_oracle_ids(raw)
    return ids[0] if ids else None


def slots_from_blobs(name_blob: Optional[str], oracle_blob: Optional[str]) -> List[CommanderSlot]:
    names = split_commander_names(name_blob)
    ids = split_commander_oracle_ids(oracle_blob)
    length = max(len(names), len(ids))
    if length == 0:
        return []
    slots: List[CommanderSlot] = []
    for idx in range(length):
        slots.append(
            CommanderSlot(
                name=names[idx] if idx < len(names) else None,
                oracle_id=ids[idx] if idx < len(ids) else None,
            )
        )
    return slots


def slots_from_payload(payload: Sequence[dict]) -> List[CommanderSlot]:
    slots: List[CommanderSlot] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = _clean(entry.get("name"))
        oracle_id = _clean(entry.get("oracle_id"))
        if not name and not oracle_id:
            continue
        slots.append(CommanderSlot(name=name, oracle_id=oracle_id))
    return slots


def serialize_slots(
    slots: Sequence[CommanderSlot],
    limit: int = MAX_COMMANDERS,
) -> tuple[Optional[str], Optional[str], List[CommanderSlot]]:
    cleaned: List[CommanderSlot] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()

    for slot in slots:
        if len(cleaned) >= limit:
            break
        name = _clean(slot.name)
        oracle_id = _clean(slot.oracle_id)
        if not name and not oracle_id:
            continue
        name_key = name.casefold() if name else None
        oracle_key = oracle_id.casefold() if oracle_id else None
        duplicate_name = bool(name_key and name_key in seen_names)
        duplicate_oracle = bool(oracle_key and oracle_key in seen_ids)
        if duplicate_name and (not oracle_key or duplicate_oracle):
            continue
        cleaned.append(CommanderSlot(name=name, oracle_id=oracle_id))
        if name_key:
            seen_names.add(name_key)
        if oracle_key:
            seen_ids.add(oracle_key)

    names = [slot.name for slot in cleaned if slot.name]
    ids = [slot.oracle_id for slot in cleaned if slot.oracle_id]
    name_blob = " // ".join(names) if names else None
    oracle_blob = ",".join(ids) if ids else None
    return name_blob, oracle_blob, cleaned


def merge_slots(
    existing: Sequence[CommanderSlot],
    new_slots: Sequence[CommanderSlot],
    *,
    mode: str = "replace",
    limit: int = MAX_COMMANDERS,
) -> tuple[Optional[str], Optional[str], List[CommanderSlot]]:
    if mode == "append":
        combined: List[CommanderSlot] = list(existing) + list(new_slots)
    else:
        combined = list(new_slots)
    return serialize_slots(combined, limit=limit)
