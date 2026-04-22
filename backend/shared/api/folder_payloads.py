"""Folder and card payload builders shared by Flask and Django APIs."""

from __future__ import annotations

from typing import Any

from core.domains.cards.services.scryfall_cache import (
    find_by_set_cn,
    normalize_color_identity,
    prints_for_oracle,
)


def serialize_folder(folder, counts: dict[str, int] | None = None) -> dict[str, Any]:
    """Transform a folder model into a stable JSON payload."""
    counts = counts or {}
    updated_at = getattr(folder, "updated_at", None)
    return {
        "id": folder.id,
        "name": folder.name,
        "category": folder.category,
        "deck_tag": folder.deck_tag,
        "commander_name": folder.commander_name,
        "is_proxy": bool(folder.is_proxy),
        "is_public": bool(folder.is_public),
        "owner_user_id": folder.owner_user_id,
        "updated_at": updated_at.isoformat() if updated_at is not None else None,
        "counts": {
            "unique": int(counts.get("unique") or 0),
            "total": int(counts.get("total") or 0),
        },
    }


def _print_for_card(
    card,
    print_cache: dict[tuple[str, str, str], dict[str, Any]] | None = None,
    oracle_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    print_cache = print_cache if print_cache is not None else {}
    oracle_cache = oracle_cache if oracle_cache is not None else {}
    set_key = (card.set_code or "").strip().lower()
    cn_key = str(card.collector_number or "").strip().lower()
    name_key = (card.name or "").strip().lower()
    cache_key = (set_key, cn_key, name_key)
    if cache_key in print_cache:
        return print_cache[cache_key]

    payload: dict[str, Any] = {}
    try:
        found = find_by_set_cn(card.set_code, card.collector_number, card.name)
    except Exception:
        found = None
    if isinstance(found, dict):
        payload = found
    elif card.oracle_id:
        oid = str(card.oracle_id).strip().lower()
        if oid in oracle_cache:
            payload = oracle_cache.get(oid) or {}
        else:
            try:
                prints = prints_for_oracle(card.oracle_id) or []
            except Exception:
                prints = []
            if prints:
                payload = next((item for item in prints if not item.get("digital")), prints[0]) or {}
            oracle_cache[oid] = payload

    print_cache[cache_key] = payload
    return payload


def serialize_card(
    card,
    print_cache: dict[tuple[str, str, str], dict[str, Any]] | None = None,
    oracle_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Transform a card model into a stable JSON payload."""
    pr = _print_for_card(card, print_cache=print_cache, oracle_cache=oracle_cache)
    type_line = (card.type_line or "").strip() or str(pr.get("type_line") or "").strip()
    if not type_line:
        faces = (pr or {}).get("card_faces") or []
        if faces:
            type_line = str((faces[0] or {}).get("type_line") or "").strip()

    rarity = (card.rarity or "").strip().lower() or str(pr.get("rarity") or "").strip().lower()
    raw_identity = (
        getattr(card, "color_identity", None)
        or getattr(card, "colors", None)
        or pr.get("color_identity")
        or pr.get("colors")
        or []
    )
    letters, derived_mask = normalize_color_identity(raw_identity)
    color_identity_mask = card.color_identity_mask
    if color_identity_mask is None:
        color_identity_mask = derived_mask

    return {
        "id": card.id,
        "name": card.name,
        "set_code": card.set_code,
        "collector_number": card.collector_number,
        "lang": card.lang,
        "quantity": card.quantity,
        "is_foil": bool(card.is_foil),
        "folder_id": card.folder_id,
        "oracle_id": card.oracle_id,
        "type_line": type_line or None,
        "rarity": rarity or None,
        "color_identity": letters or None,
        "color_identity_mask": color_identity_mask,
    }
