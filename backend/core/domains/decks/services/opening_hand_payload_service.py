"""Payload and media helpers for opening-hand flows."""

from __future__ import annotations

from typing import Iterable, Optional

from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    cache_ready,
    ensure_cache_loaded,
    prints_for_oracle,
    unique_oracle_by_name,
)
from core.domains.decks.services.commander_utils import split_commander_names, split_commander_oracle_ids
from core.domains.decks.viewmodels.opening_hand_vm import OpeningHandTokenVM
from core.shared.utils.assets import static_url
from shared.mtg import (
    _card_type_flags,
    _oracle_text_from_faces,
    _type_line_from_print,
)


def _ensure_cache_ready() -> bool:
    return cache_ready() or ensure_cache_loaded()


def _scryfall_card_url(set_code: str | None, collector_number: str | None) -> str | None:
    scode = (set_code or "").strip().lower()
    cn = (collector_number or "").strip()
    if not scode or not cn:
        return None
    return f"https://scryfall.com/card/{scode}/{cn}"


def _opening_hand_token_key(token: dict | None) -> str:
    if not isinstance(token, dict):
        return "token|token"
    name = (token.get("name") or "Token").strip().lower() or "token"
    type_line = (token.get("type_line") or "Token").strip().lower() or "token"
    return f"{name}|{type_line}"


def _dedupe_opening_hand_tokens(tokens: Iterable[dict] | None) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for token in tokens or []:
        key = _opening_hand_token_key(token)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return deduped


def _image_from_print(print_obj: dict | None) -> dict:
    if not print_obj:
        return {"small": None, "normal": None, "large": None}
    imgs = sc.image_for_print(print_obj) or {}
    faces = print_obj.get("card_faces") or []
    if not imgs.get("small") and faces:
        face_imgs = (faces[0] or {}).get("image_uris") or {}
        imgs.setdefault("small", face_imgs.get("small"))
        imgs.setdefault("normal", face_imgs.get("normal"))
        imgs.setdefault("large", face_imgs.get("large"))
    return {
        "small": imgs.get("small"),
        "normal": imgs.get("normal"),
        "large": imgs.get("large"),
    }


def _back_image_from_print(print_obj: dict | None) -> dict:
    if not print_obj:
        return {"small": None, "normal": None, "large": None}
    faces = print_obj.get("card_faces") or []
    if not isinstance(faces, list) or len(faces) < 2:
        return {"small": None, "normal": None, "large": None}
    face_imgs = (faces[1] or {}).get("image_uris") or {}
    return {
        "small": face_imgs.get("small"),
        "normal": face_imgs.get("normal"),
        "large": face_imgs.get("large"),
    }


def _pick_nondigital_print(prints: Iterable[dict] | None) -> dict | None:
    items = list(prints or [])
    if not items:
        return None
    return next((item for item in items if not item.get("digital")), items[0])


def _commander_card_payload(name: Optional[str], oracle_id: Optional[str]) -> Optional[dict]:
    resolved_name = (name or "").strip() or None
    resolved_oid = (oracle_id or "").strip() or None
    if not resolved_name and not resolved_oid:
        return None

    _ensure_cache_ready()

    pr = None
    if resolved_oid:
        try:
            pr = _pick_nondigital_print(prints_for_oracle(resolved_oid) or [])
        except Exception:
            pr = None

    if not pr and resolved_name:
        try:
            resolved_oid = unique_oracle_by_name(resolved_name)
        except Exception:
            resolved_oid = None
        if resolved_oid:
            try:
                pr = _pick_nondigital_print(prints_for_oracle(resolved_oid) or [])
            except Exception:
                pr = None

    placeholder = static_url("img/card-placeholder.svg")
    imgs = _image_from_print(pr)
    back_imgs = _back_image_from_print(pr)
    oracle_text = (pr or {}).get("oracle_text") or _oracle_text_from_faces((pr or {}).get("card_faces"))
    return {
        "name": resolved_name or (pr or {}).get("name") or "Commander",
        "oracle_id": resolved_oid or (pr or {}).get("oracle_id"),
        "small": imgs.get("small") or placeholder,
        "normal": imgs.get("normal") or imgs.get("large") or imgs.get("small") or placeholder,
        "large": imgs.get("large") or imgs.get("normal") or imgs.get("small") or placeholder,
        "back_small": back_imgs.get("small"),
        "back_normal": back_imgs.get("normal"),
        "back_large": back_imgs.get("large"),
        "image": imgs.get("normal") or imgs.get("large") or imgs.get("small") or placeholder,
        "hover": imgs.get("large") or imgs.get("normal") or imgs.get("small") or placeholder,
        "type_line": _type_line_from_print(pr) or "",
        "oracle_text": oracle_text or "",
        "external_url": (pr or {}).get("scryfall_uri") or (pr or {}).get("uri"),
    }


def _commander_card_payloads(name_blob: Optional[str], oracle_blob: Optional[str]) -> list[dict]:
    names = split_commander_names(name_blob)
    oracles = split_commander_oracle_ids(oracle_blob)
    if not names and not oracles:
        return []
    payloads: list[dict] = []
    for idx in range(max(len(names), len(oracles), 1)):
        name = names[idx] if idx < len(names) else (names[0] if names else None)
        oracle_id = oracles[idx] if idx < len(oracles) else (oracles[0] if oracles else None)
        payload = _commander_card_payload(name, oracle_id)
        if payload:
            payloads.append(payload)
    return payloads


def _client_card_payload(entry: dict, placeholder: str) -> dict:
    normal = entry.get("large") or entry.get("normal") or entry.get("small") or placeholder
    small = entry.get("small") or entry.get("normal") or entry.get("large") or placeholder
    hover = entry.get("large") or entry.get("normal") or entry.get("small") or placeholder
    back_image = entry.get("back_large") or entry.get("back_normal") or entry.get("back_small") or entry.get("back_image")
    back_hover = entry.get("back_large") or entry.get("back_normal") or entry.get("back_small") or entry.get("back_hover")
    flags = _card_type_flags(entry.get("type_line"))
    payload = {
        "name": entry.get("name") or "Card",
        "image": normal,
        "small": small,
        "hover": hover,
        "detail_url": entry.get("detail_url") or entry.get("external_url"),
        "type_line": entry.get("type_line") or "",
        "oracle_text": entry.get("oracle_text") or "",
        "is_creature": bool(flags["is_creature"]),
        "is_land": bool(flags["is_land"]),
        "is_instant": bool(flags["is_instant"]),
        "is_sorcery": bool(flags["is_sorcery"]),
        "is_permanent": bool(flags["is_permanent"]),
        "zone_hint": str(flags["zone_hint"]),
    }
    uid = entry.get("uid")
    if uid:
        payload["uid"] = uid
    if back_image or back_hover:
        payload["back_image"] = back_image or back_hover
        payload["back_hover"] = back_hover or back_image
    return payload


def _token_payload(token: dict, placeholder: str) -> dict:
    token_name = (token.get("name") or "Token").strip()
    token_type = (token.get("type_line") or "").strip()
    token_imgs = token.get("images") or {}
    token_flags = _card_type_flags(token_type)
    return OpeningHandTokenVM(
        id=token.get("id"),
        name=token_name,
        type_line=token_type,
        image=token_imgs.get("normal") or token_imgs.get("small") or placeholder,
        hover=token_imgs.get("large") or token_imgs.get("normal") or token_imgs.get("small") or placeholder,
        is_creature=bool(token_flags["is_creature"]),
        is_land=bool(token_flags["is_land"]),
        is_instant=bool(token_flags["is_instant"]),
        is_sorcery=bool(token_flags["is_sorcery"]),
        is_permanent=bool(token_flags["is_permanent"]),
        zone_hint=str(token_flags["zone_hint"]),
    ).to_payload()


__all__ = [
    "_back_image_from_print",
    "_client_card_payload",
    "_commander_card_payload",
    "_commander_card_payloads",
    "_dedupe_opening_hand_tokens",
    "_ensure_cache_ready",
    "_image_from_print",
    "_opening_hand_token_key",
    "_pick_nondigital_print",
    "_scryfall_card_url",
    "_token_payload",
]
