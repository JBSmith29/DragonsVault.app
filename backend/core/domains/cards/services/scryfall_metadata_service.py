"""Metadata extraction and local cache search helpers for Scryfall prints."""

from __future__ import annotations

from math import inf
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

_COLOR_BIT_MAP = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}
_WUBRG_ORDER = ("W", "U", "B", "R", "G")


def normalize_color_identity(colors: Optional[Iterable[str]]) -> Tuple[str, int]:
    """Return a WUBRG-ordered color string plus a bitmask."""
    raw = {str(color or "").strip().upper() for color in (colors or []) if color}
    letters = [color for color in _WUBRG_ORDER if color in raw]
    mask = 0
    for letter in letters:
        mask |= _COLOR_BIT_MAP.get(letter, 0)
    return "".join(letters), mask


def _joined_oracle_text(print_data: Dict[str, Any]) -> str:
    parts = []
    text = print_data.get("oracle_text")
    if text:
        parts.append(text)
    for face in print_data.get("card_faces") or []:
        face_text = (face or {}).get("oracle_text")
        if face_text:
            parts.append(face_text)
    return " // ".join(part for part in parts if part)


def _face_payload(face_data: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fallback = fallback or {}
    colors_raw = face_data.get("colors") or []
    identity_raw = face_data.get("color_identity") or []
    colors_letters, _ = normalize_color_identity(colors_raw)
    identity_letters, _ = normalize_color_identity(identity_raw)
    image_uris = (face_data.get("image_uris") or {}) if isinstance(face_data, dict) else {}
    return {
        "name": face_data.get("name") or fallback.get("name"),
        "oracle_text": face_data.get("oracle_text"),
        "mana_cost": face_data.get("mana_cost"),
        "type_line": face_data.get("type_line"),
        "colors": colors_letters or None,
        "color_identity": identity_letters or None,
        "image_uris": {
            "small": image_uris.get("small"),
            "normal": image_uris.get("normal"),
            "large": image_uris.get("large"),
        },
    }


def metadata_from_print(print_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract the cached metadata stored on Card rows from a Scryfall print."""
    if not print_data:
        return {
            "type_line": None,
            "rarity": None,
            "oracle_text": None,
            "mana_value": None,
            "colors": None,
            "color_identity": None,
            "color_identity_mask": None,
            "layout": None,
            "faces_json": None,
        }

    type_line_raw = (print_data.get("type_line") or "").strip()
    rarity_raw = (print_data.get("rarity") or "").strip().lower()
    layout_raw = (print_data.get("layout") or "").strip().lower()

    oracle_text = _joined_oracle_text(print_data) or None

    mana_value = print_data.get("cmc")
    if mana_value is None:
        mana_value = print_data.get("mana_value")
    try:
        mana_value = float(mana_value) if mana_value is not None else None
    except (TypeError, ValueError):
        mana_value = None

    colors_raw = print_data.get("colors")
    if not colors_raw:
        colors_raw = []
        for face in print_data.get("card_faces") or []:
            colors_raw.extend(face.get("colors") or [])
    colors_letters, _ = normalize_color_identity(colors_raw or [])

    identity_raw = print_data.get("color_identity")
    if not identity_raw:
        identity_raw = colors_raw or []
    identity_letters, mask = normalize_color_identity(identity_raw or [])

    faces = print_data.get("card_faces") or []
    if faces:
        faces_json = [_face_payload(face, fallback=print_data) for face in faces if isinstance(face, dict)]
    else:
        faces_json = [_face_payload(print_data, fallback=print_data)]

    return {
        "type_line": type_line_raw or None,
        "rarity": rarity_raw or None,
        "oracle_text": oracle_text,
        "mana_value": mana_value,
        "colors": colors_letters or None,
        "color_identity": identity_letters or None,
        "color_identity_mask": mask or None,
        "layout": layout_raw or None,
        "faces_json": faces_json or None,
    }


def _collector_sort_key(value: Optional[str]) -> Tuple[int, int, str]:
    raw = str(value or "")
    digits = "".join(ch for ch in raw if ch.isdigit())
    number = int(digits) if digits else inf
    return (0 if digits else 1, number, raw)


def search_local_cards(
    *,
    ensure_cache_loaded_fn: Callable[[], bool],
    cache: list[Dict[str, Any]],
    name: str = "",
    set_code: str = "",
    base_types: Iterable[str] = (),
    typal: str = "",
    colors: Iterable[str] = (),
    color_mode: str = "contains",
    commander_only: bool = False,
    order: str = "name",
    direction: str = "asc",
    page: int = 1,
    per: int = 60,
) -> Optional[Dict[str, Any]]:
    if not ensure_cache_loaded_fn():
        return None

    name = (name or "").strip().lower()
    set_code = (set_code or "").strip().lower()
    typal = (typal or "").strip().lower()
    color_mode = color_mode or "contains"
    colors = [color.upper() for color in colors if color]

    def matches(card):
        if name and name not in (card.get("name") or "").lower():
            return False
        if set_code and (card.get("set") or "").lower() != set_code:
            return False
        type_line = (card.get("type_line") or "").lower()
        for base in base_types or []:
            if base.lower() not in type_line:
                return False
        if typal and typal not in type_line:
            return False
        color_identity = card.get("color_identity") or []
        color_identity_set = {str(color or "").upper() for color in color_identity}
        filter_colors = set(colors)
        if filter_colors:
            if color_mode == "exact":
                if color_identity_set != filter_colors:
                    return False
            elif not filter_colors.issubset(color_identity_set):
                return False
        if commander_only:
            legality = ((card.get("legalities") or {}).get("commander") or "").lower()
            if legality != "legal":
                return False
        return True

    filtered = [card for card in cache if matches(card)]
    reverse = direction == "desc"

    def sort_key(card):
        if order == "cmc":
            return (card.get("cmc") or 0, card.get("name") or "")
        if order == "rarity":
            return ((card.get("rarity") or "").lower(), card.get("name") or "")
        if order == "set":
            return ((card.get("set") or "").lower(), _collector_sort_key(card.get("collector_number")))
        if order in {"collector", "cn"}:
            return _collector_sort_key(card.get("collector_number"))
        return ((card.get("name") or "").lower(),)

    filtered.sort(key=sort_key, reverse=reverse)

    total = len(filtered)
    start = max(0, (page - 1) * per)
    sliced = filtered[start : start + per]
    return {
        "data": sliced,
        "total_cards": total,
        "has_more": start + per < total,
    }
