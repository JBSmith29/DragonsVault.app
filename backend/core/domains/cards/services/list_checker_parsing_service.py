"""Parsing and face-matching helpers for list checker input."""

from __future__ import annotations

import re
from collections import OrderedDict

from sqlalchemy import func, or_

from models import Card
from shared.mtg import _normalize_name


def parse_card_list(text: str) -> "OrderedDict[str, dict]":
    """Parse lines like '2x Name' or '2 Name' into an OrderedDict of normalized entries."""
    wanted = OrderedDict()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = line.strip('"').strip("'")
        qty = 1
        name = line

        match = re.match(r"^\s*(\d+)\s*x?\s+(.+?)\s*$", line, flags=re.IGNORECASE)
        if not match:
            match = re.match(r"^\s*(.+?)\s*x\s*(\d+)\s*$", line, flags=re.IGNORECASE)
            if match:
                name, qty = match.group(1), int(match.group(2))
        else:
            qty, name = int(match.group(1)), match.group(2)

        normalized = _normalize_name(name)
        if not normalized:
            continue
        if normalized in wanted:
            wanted[normalized]["qty"] += qty
        else:
            wanted[normalized] = {"display": name.strip(), "qty": qty}
    return wanted


def face_like_patterns(name: str) -> list[str]:
    """
    Build SQL ILIKE patterns that tolerate optional spaces around '//' and
    allow matching either face: 'n // %', 'n//%', '% // n', '%//n'.
    """
    cleaned = " ".join((name or "").split()).strip()
    if not cleaned:
        return []
    return [
        f"{cleaned} // %",
        f"{cleaned}//%",
        f"% // {cleaned}",
        f"%//{cleaned}",
    ]


def find_card_by_name_or_face(name: str):
    """
    Try exact (case-insensitive). If not found, try to match either face
    of a card stored as 'Face A // Face B' (or 'A//B').
    Returns: Card | None
    """
    if not name:
        return None

    cleaned = " ".join(name.split()).strip()
    if not cleaned:
        return None

    exact = Card.query.filter(func.lower(Card.name) == cleaned.lower()).first()
    if exact:
        return exact

    patterns = face_like_patterns(cleaned)
    if patterns:
        face_match = (
            Card.query.filter(or_(*[Card.name.ilike(pattern) for pattern in patterns]))
            .order_by(func.length(Card.name))
            .first()
        )
        if face_match:
            return face_match

    return (
        Card.query.filter(Card.name.ilike(f"%{cleaned}%"))
        .order_by(func.length(Card.name))
        .first()
    )


__all__ = [
    "face_like_patterns",
    "find_card_by_name_or_face",
    "parse_card_list",
]
