"""Side-by-side deck comparison.

Given two folder ids, produce a diff report covering:
    * shared cards (name + quantity on each side)
    * cards unique to either deck
    * mana-curve deltas per converted-mana-value bucket
    * color-pip deltas
    * aggregate counts (size, mainboard by type)

All price/legality concerns are handled by other services; this one is a
pure set-and-bag diff that can run against any pair of folders the caller is
allowed to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from extensions import db
from models import Card, Folder


__all__ = [
    "DeckComparison",
    "compare_folders",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class _FolderSnapshot:
    folder: Folder
    cards_by_key: dict[str, dict[str, Any]]
    total_quantity: int
    type_counts: dict[str, int]
    curve_counts: dict[str, int]
    pip_counts: dict[str, int]


@dataclass
class DeckComparison:
    left: dict[str, Any]
    right: dict[str, Any]
    shared: list[dict[str, Any]]
    only_left: list[dict[str, Any]]
    only_right: list[dict[str, Any]]
    curve_diff: dict[str, dict[str, int]]
    pip_diff: dict[str, dict[str, int]]
    type_diff: dict[str, dict[str, int]]
    summary: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left,
            "right": self.right,
            "shared": self.shared,
            "only_left": self.only_left,
            "only_right": self.only_right,
            "curve_diff": self.curve_diff,
            "pip_diff": self.pip_diff,
            "type_diff": self.type_diff,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TYPE_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Land", ("land",)),
    ("Creature", ("creature",)),
    ("Planeswalker", ("planeswalker",)),
    ("Battle", ("battle",)),
    ("Enchantment", ("enchantment",)),
    ("Artifact", ("artifact",)),
    ("Instant", ("instant",)),
    ("Sorcery", ("sorcery",)),
)


def _type_bucket(type_line: str | None) -> str:
    lowered = (type_line or "").lower()
    for label, tokens in _TYPE_BUCKETS:
        if any(token in lowered for token in tokens):
            return label
    return "Other"


def _curve_bucket(mana_value: float | None) -> str:
    if mana_value is None:
        return "unknown"
    try:
        value = float(mana_value)
    except (TypeError, ValueError):
        return "unknown"
    if value < 0:
        return "unknown"
    bucket = int(round(value))
    return str(bucket) if bucket <= 6 else "7+"


def _colors_from_identity(card: Card) -> Iterable[str]:
    raw = getattr(card, "color_identity", None) or ""
    return [ch.upper() for ch in str(raw) if ch.isalpha()]


def _snapshot(folder: Folder) -> _FolderSnapshot:
    cards = list(folder.cards or [])
    cards_by_key: dict[str, dict[str, Any]] = {}
    type_counts: dict[str, int] = {}
    curve_counts: dict[str, int] = {}
    pip_counts: dict[str, int] = {}
    total = 0

    for card in cards:
        qty = max(0, int(card.quantity or 0))
        if qty <= 0:
            continue
        total += qty
        key = (card.oracle_id or "").strip().lower() or (card.name or "").strip().lower()
        entry = cards_by_key.setdefault(
            key,
            {
                "name": card.name,
                "quantity": 0,
                "oracle_id": card.oracle_id,
                "type_line": card.type_line,
            },
        )
        entry["quantity"] += qty
        type_counts[_type_bucket(card.type_line)] = (
            type_counts.get(_type_bucket(card.type_line), 0) + qty
        )
        if (card.type_line or "").lower().find("land") == -1:
            curve_counts[_curve_bucket(card.mana_value)] = (
                curve_counts.get(_curve_bucket(card.mana_value), 0) + qty
            )
        for ch in _colors_from_identity(card):
            pip_counts[ch] = pip_counts.get(ch, 0) + qty

    return _FolderSnapshot(
        folder=folder,
        cards_by_key=cards_by_key,
        total_quantity=total,
        type_counts=type_counts,
        curve_counts=curve_counts,
        pip_counts=pip_counts,
    )


def _folder_payload(snapshot: _FolderSnapshot) -> dict[str, Any]:
    folder = snapshot.folder
    return {
        "id": folder.id,
        "name": folder.name,
        "category": folder.category,
        "commander_name": folder.commander_name,
        "total_cards": snapshot.total_quantity,
        "unique_cards": len(snapshot.cards_by_key),
    }


def _diff_buckets(left: dict[str, int], right: dict[str, int]) -> dict[str, dict[str, int]]:
    keys = sorted(set(left) | set(right), key=lambda k: (k == "unknown", k))
    return {
        key: {
            "left": int(left.get(key, 0)),
            "right": int(right.get(key, 0)),
            "delta": int(right.get(key, 0)) - int(left.get(key, 0)),
        }
        for key in keys
    }


def _card_comparison_row(
    key: str,
    left_entry: dict[str, Any] | None,
    right_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    source = left_entry or right_entry or {}
    return {
        "key": key,
        "name": source.get("name"),
        "oracle_id": source.get("oracle_id"),
        "type_line": source.get("type_line"),
        "left_quantity": int((left_entry or {}).get("quantity", 0) or 0),
        "right_quantity": int((right_entry or {}).get("quantity", 0) or 0),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compare_folders(left: Folder, right: Folder) -> DeckComparison:
    """Compare two folders and return a structured diff."""
    if left.id == right.id:
        raise ValueError("Cannot compare a deck with itself")

    # Ensure cards are loaded even when folders were fetched without eager load.
    for folder in (left, right):
        if folder.cards is None:  # pragma: no cover - SQLAlchemy populates lazily
            db.session.refresh(folder)

    left_snapshot = _snapshot(left)
    right_snapshot = _snapshot(right)

    shared: list[dict[str, Any]] = []
    only_left: list[dict[str, Any]] = []
    only_right: list[dict[str, Any]] = []

    all_keys = set(left_snapshot.cards_by_key) | set(right_snapshot.cards_by_key)
    for key in sorted(all_keys):
        left_entry = left_snapshot.cards_by_key.get(key)
        right_entry = right_snapshot.cards_by_key.get(key)
        row = _card_comparison_row(key, left_entry, right_entry)
        if left_entry and right_entry:
            shared.append(row)
        elif left_entry:
            only_left.append(row)
        else:
            only_right.append(row)

    def _by_name(row: dict[str, Any]) -> str:
        return (row.get("name") or "").lower()

    shared.sort(key=_by_name)
    only_left.sort(key=_by_name)
    only_right.sort(key=_by_name)

    curve_diff = _diff_buckets(left_snapshot.curve_counts, right_snapshot.curve_counts)
    pip_diff = _diff_buckets(left_snapshot.pip_counts, right_snapshot.pip_counts)
    type_diff = _diff_buckets(left_snapshot.type_counts, right_snapshot.type_counts)

    summary = {
        "shared": len(shared),
        "only_left": len(only_left),
        "only_right": len(only_right),
        "left_total": left_snapshot.total_quantity,
        "right_total": right_snapshot.total_quantity,
        "left_unique": len(left_snapshot.cards_by_key),
        "right_unique": len(right_snapshot.cards_by_key),
    }

    return DeckComparison(
        left=_folder_payload(left_snapshot),
        right=_folder_payload(right_snapshot),
        shared=shared,
        only_left=only_left,
        only_right=only_right,
        curve_diff=curve_diff,
        pip_diff=pip_diff,
        type_diff=type_diff,
        summary=summary,
    )
