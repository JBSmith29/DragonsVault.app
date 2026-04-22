"""Scryfall metadata helpers for list checker results."""

from __future__ import annotations

from core.domains.cards.services import scryfall_cache
from shared.mtg import _normalize_name

_RARITY_LABELS = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "mythic": "Mythic",
    "special": "Special",
    "bonus": "Bonus",
}


def normalize_rarity(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    return _RARITY_LABELS.get(text, text.title())


def normalize_type(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    # Keep the front (type) side before em-dash subtype details.
    return text.split("—", 1)[0].strip()


def build_scryfall_lookup_maps():
    name_to_sid = {}
    face_to_sid = {}
    name_to_meta = {}
    face_to_meta = {}

    try:
        if scryfall_cache.ensure_cache_loaded():
            from core.domains.cards.services.scryfall_cache import get_all_prints

            for print_row in get_all_prints():
                normalized_name = _normalize_name(print_row.get("name") or "")
                if not normalized_name:
                    continue

                sid = print_row.get("id")
                lang = (print_row.get("lang") or "en").lower()
                oracle_id = print_row.get("oracle_id")
                rarity_label = normalize_rarity(print_row.get("rarity"))
                ci_raw = print_row.get("color_identity") or print_row.get("colors") or []
                ci_letters, _ = scryfall_cache.normalize_color_identity(ci_raw)
                type_label = normalize_type(print_row.get("type_line"))
                meta = {"rarity": rarity_label, "color_identity": ci_letters, "type": type_label}

                previous = name_to_sid.get(normalized_name)
                if previous is None or (previous[1] != "en" and lang == "en"):
                    name_to_sid[normalized_name] = (sid, lang, oracle_id)
                previous_meta = name_to_meta.get(normalized_name)
                if previous_meta is None or (previous_meta["lang"] != "en" and lang == "en"):
                    name_to_meta[normalized_name] = {"lang": lang, **meta}

                raw_name = print_row.get("name") or ""
                if "//" not in raw_name:
                    continue

                front_face, back_face = [piece.strip() for piece in raw_name.split("//", 1)]
                for face_name in (front_face, back_face):
                    normalized_face = _normalize_name(face_name)
                    if not normalized_face:
                        continue
                    previous_face = face_to_sid.get(normalized_face)
                    if previous_face is None or (previous_face[1] != "en" and lang == "en"):
                        face_to_sid[normalized_face] = (sid, lang, oracle_id)
                    previous_face_meta = face_to_meta.get(normalized_face)
                    if previous_face_meta is None or (previous_face_meta["lang"] != "en" and lang == "en"):
                        face_to_meta[normalized_face] = {"lang": lang, **meta}
    except Exception:
        return {}, {}, {}, {}

    return name_to_sid, face_to_sid, name_to_meta, face_to_meta


__all__ = [
    "build_scryfall_lookup_maps",
    "normalize_rarity",
    "normalize_type",
]
