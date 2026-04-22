"""Shared deck gallery helper functions reused outside the gallery view."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import load_only

from extensions import cache, db
from models import Card, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    cache_ready,
    ensure_cache_loaded,
    prints_for_oracle,
    unique_oracle_by_name,
)
from core.domains.decks.services.commander_utils import (
    primary_commander_name,
    primary_commander_oracle_id,
    split_commander_names,
    split_commander_oracle_ids,
)
from shared.mtg import _lookup_print_data


def image_pack_from_print(print_obj: dict | None) -> dict[str, str | None]:
    if not print_obj:
        return {"small": None, "normal": None, "large": None}
    images = sc.image_for_print(print_obj) or {}
    faces = print_obj.get("card_faces") or []
    if not images.get("small") and faces:
        face_images = (faces[0] or {}).get("image_uris") or {}
        images.setdefault("small", face_images.get("small"))
        images.setdefault("normal", face_images.get("normal"))
        images.setdefault("large", face_images.get("large"))
    return {
        "small": images.get("small"),
        "normal": images.get("normal"),
        "large": images.get("large"),
    }


def prefetch_commander_cards(folder_map: dict[int, Folder]) -> dict[int, Card]:
    """Pull commander print candidates for the provided folders in one query."""
    wanted: dict[int, set[str]] = {}
    wanted_names: dict[int, set[str]] = {}
    oracle_pool: set[str] = set()
    name_pool: set[str] = set()
    for folder_id, folder in folder_map.items():
        oracle_ids = {
            oracle_id.strip().lower()
            for oracle_id in split_commander_oracle_ids(folder.commander_oracle_id)
            if oracle_id.strip()
        }
        if oracle_ids:
            wanted[folder_id] = oracle_ids
            oracle_pool.update(oracle_ids)
        names = {
            name.strip().lower()
            for name in split_commander_names(getattr(folder, "commander_name", "") or "")
            if name.strip()
        }
        if names:
            wanted_names[folder_id] = names
            name_pool.update(names)
    if not oracle_pool:
        oracle_pool = set()

    rows = (
        Card.query.options(
            load_only(
                Card.id,
                Card.name,
                Card.set_code,
                Card.collector_number,
                Card.oracle_id,
                Card.quantity,
                Card.folder_id,
            )
        )
        .filter(Card.folder_id.in_(wanted.keys()))
        .filter(Card.oracle_id.isnot(None))
        .filter(func.lower(Card.oracle_id).in_(oracle_pool))
        .order_by(Card.folder_id.asc(), Card.quantity.desc(), Card.id.asc())
        .all()
    )

    commander_cards: dict[int, Card] = {}
    for card in rows:
        folder_id = card.folder_id
        oracle_id = (card.oracle_id or "").strip().lower()
        if folder_id in wanted and oracle_id in wanted[folder_id] and folder_id not in commander_cards:
            commander_cards[folder_id] = card

    if name_pool:
        name_rows = (
            Card.query.options(
                load_only(
                    Card.id,
                    Card.name,
                    Card.set_code,
                    Card.collector_number,
                    Card.oracle_id,
                    Card.quantity,
                    Card.folder_id,
                )
            )
            .filter(Card.folder_id.in_(wanted_names.keys()))
            .filter(func.lower(Card.name).in_(name_pool))
            .order_by(Card.folder_id.asc(), Card.quantity.desc(), Card.id.asc())
            .all()
        )
        for card in name_rows:
            folder_id = card.folder_id
            lowered_name = (card.name or "").strip().lower()
            if folder_id in wanted_names and lowered_name in wanted_names[folder_id] and folder_id not in commander_cards:
                commander_cards[folder_id] = card
    return commander_cards


def owner_summary(decks: list[dict]) -> list[dict]:
    summary: dict[str, dict] = {}
    for deck in decks:
        raw_owner = (deck.get("owner") or "").strip()
        owner_key = (deck.get("owner_key") or "").strip().lower()
        owner_label = (deck.get("owner_label") or "").strip()
        if not owner_key:
            owner_key = f"owner:{raw_owner.lower()}" if raw_owner else "owner:unassigned"
        label = owner_label or raw_owner or "Unassigned"
        entry = summary.get(owner_key)
        if not entry:
            entry = {
                "key": owner_key,
                "owner": raw_owner or None,
                "label": label,
                "deck_count": 0,
                "card_total": 0,
                "proxy_count": 0,
            }
            summary[owner_key] = entry
        entry["deck_count"] += 1
        entry["card_total"] += int(deck.get("qty") or 0)
        if deck.get("is_proxy"):
            entry["proxy_count"] += 1
    return sorted(summary.values(), key=lambda item: (item["label"].lower(), item["key"]))


def owner_names(decks: list[dict]) -> list[str]:
    names = sorted({(deck.get("owner") or "").strip() for deck in decks if deck.get("owner")})
    return [name for name in names if name]


@cache.memoize(timeout=600)
def commander_thumbnail_payload(
    folder_id: int,
    target_oracle_id: Optional[str],
    commander_name: Optional[str],
    row_count: int,
    qty_sum: int,
    epoch: int,
) -> dict[str, Optional[str]]:
    folder = db.session.get(Folder, folder_id)
    commander_name_value = commander_name or (folder.commander_name if folder else None)
    small = large = None
    alt = ""
    commander_card = None
    try:
        if not cache_ready():
            ensure_cache_loaded(force=False)
    except Exception:
        pass
    resolved_oid = primary_commander_oracle_id(target_oracle_id) if target_oracle_id else None
    if not resolved_oid and folder:
        resolved_oid = primary_commander_oracle_id(folder.commander_oracle_id)
    _ = (row_count, qty_sum, epoch)

    if not resolved_oid and commander_name_value:
        try:
            lookup_name = primary_commander_name(commander_name_value) or commander_name_value
            resolved_oid = unique_oracle_by_name(lookup_name)
        except Exception:
            resolved_oid = None

    if folder and resolved_oid:
        commander_card = (
            Card.query.filter(Card.folder_id == folder_id, Card.oracle_id == resolved_oid)
            .order_by(Card.quantity.desc())
            .first()
        )
        if commander_card:
            commander_name_value = folder.commander_name or commander_card.name
            alt = commander_name_value or "Commander"
            print_data = _lookup_print_data(
                commander_card.set_code,
                commander_card.collector_number,
                commander_card.name,
                commander_card.oracle_id,
            )
            if not print_data:
                try:
                    print_data = sc.find_by_set_cn_loose(
                        commander_card.set_code,
                        commander_card.collector_number,
                        commander_card.name,
                    ) or {}
                except Exception:
                    print_data = {}
            image_pack = image_pack_from_print(print_data)
            small = image_pack.get("small") or small
            large = image_pack.get("large") or image_pack.get("normal") or image_pack.get("small") or large

    if resolved_oid and (not small or not large):
        try:
            prints = prints_for_oracle(resolved_oid) or ()
        except Exception:
            prints = ()
        if prints:
            print_data = None
            if commander_card and commander_card.set_code and commander_card.collector_number:
                print_data = next(
                    (
                        print_obj
                        for print_obj in prints
                        if (print_obj.get("set") or "").lower() == (commander_card.set_code or "").lower()
                        and str(print_obj.get("collector_number") or "").lower() == str(commander_card.collector_number or "").lower()
                    ),
                    None,
                )
            print_data = print_data or next((print_obj for print_obj in prints if not print_obj.get("digital")), prints[0])
            commander_name_value = commander_name_value or print_data.get("name")
            alt = commander_name_value or "Commander"
            image_pack = image_pack_from_print(print_data)
            small = small or image_pack.get("small") or image_pack.get("normal") or image_pack.get("large")
            large = large or image_pack.get("large") or image_pack.get("normal") or image_pack.get("small")

    return {
        "name": commander_name_value,
        "small": small,
        "large": large,
        "alt": alt or (commander_name_value or "Commander"),
    }
