"""Materialise an Archidekt deck as a local Folder for pod game logging.

Live at log time: when a player picks one of their Archidekt Commander decks, we
resolve its cards against the local Scryfall cache and create (or refresh) a
local Folder, so the game references a real deck and inherits the commander,
card list, bracket, and win-rate tracking of any other deck. Re-importing the
same Archidekt deck refreshes the existing folder in place (keeping its id, so
games that already reference it stay intact).
"""

from __future__ import annotations

from typing import Any

from extensions import db
from models import Card, Folder
from core.domains.cards.services.scryfall_cache import (
    cache_ready,
    ensure_cache_loaded,
    find_by_set_cn,
    metadata_from_print,
    unique_oracle_by_name,
)
from core.domains.decks.services.commander_utils import split_commander_names
from core.domains.decks.services.deck_tags import sync_folder_deck_tag_map
from core.domains.decks.services.proxy_decks import resolve_proxy_cards
from core.domains.games.services import archidekt_service
from shared.folders import folder_name_exists, generate_unique_folder_name


def _cache_ready() -> bool:
    return cache_ready() or ensure_cache_loaded()


def _apply_commander(folder: Folder, commander_name: str | None) -> None:
    clean = (commander_name or "").strip()
    if not clean:
        folder.commander_name = None
        folder.commander_oracle_id = None
        return
    parts = split_commander_names(clean) or [clean]
    folder.commander_name = " // ".join(parts)
    oracle_ids: list[str] = []
    for part in parts:
        try:
            oracle_id = unique_oracle_by_name(part)
        except Exception:
            oracle_id = None
        if oracle_id:
            oracle_ids.append(oracle_id)
    folder.commander_oracle_id = ",".join(oracle_ids) if oracle_ids else None


def _populate_cards(folder: Folder, deck_lines: list[str]) -> tuple[int, list[str]]:
    resolved, errors = resolve_proxy_cards(deck_lines)
    aggregated: dict[tuple, dict[str, Any]] = {}
    for card in resolved:
        key = (card.oracle_id, card.set_code.upper(), str(card.collector_number), card.lang.lower())
        entry = aggregated.get(key)
        if entry:
            entry["quantity"] += card.quantity
        else:
            aggregated[key] = {
                "name": card.name,
                "oracle_id": card.oracle_id,
                "set_code": card.set_code.upper(),
                "collector_number": str(card.collector_number),
                "lang": card.lang.lower(),
                "quantity": card.quantity,
            }

    ready = _cache_ready()
    for entry in aggregated.values():
        metadata: dict[str, Any] = {}
        if ready:
            try:
                print_row = find_by_set_cn(entry["set_code"], entry["collector_number"], entry["name"])
            except Exception:
                print_row = None
            if print_row:
                metadata = metadata_from_print(print_row)
        db.session.add(
            Card(
                name=entry["name"],
                set_code=entry["set_code"],
                collector_number=entry["collector_number"],
                folder_id=folder.id,
                oracle_id=entry["oracle_id"],
                lang=entry["lang"],
                is_foil=False,
                quantity=max(int(entry["quantity"]), 1),
                type_line=metadata.get("type_line"),
                rarity=metadata.get("rarity"),
                oracle_text=metadata.get("oracle_text"),
                mana_value=metadata.get("mana_value"),
                colors=metadata.get("colors"),
                color_identity=metadata.get("color_identity"),
                color_identity_mask=metadata.get("color_identity_mask"),
                layout=metadata.get("layout"),
                faces_json=metadata.get("faces_json"),
            )
        )
    return len(aggregated), errors


def import_archidekt_deck(deck_id: Any, *, owner_user_id: int) -> dict[str, Any]:
    """Fetch an Archidekt deck and create/refresh it as a local Folder.

    Returns a summary dict; raises ``archidekt_service.ArchidektError`` on a bad
    deck id or upstream failure.
    """
    data = archidekt_service.fetch_deck(deck_id)
    archidekt_id = str(data.get("id") or deck_id)

    deck_lines = [f"1 {name}" for name in (data.get("commanders") or [])]
    deck_lines += [f"{card['quantity']} {card['name']}" for card in (data.get("cards") or [])]

    folder = Folder.query.filter_by(owner_user_id=owner_user_id, archidekt_deck_id=archidekt_id).first()
    refreshed = folder is not None
    if folder is None:
        name = (data.get("name") or "Archidekt Deck").strip() or "Archidekt Deck"
        if folder_name_exists(name, owner_user_id=owner_user_id):
            name = generate_unique_folder_name(name, owner_user_id=owner_user_id)
        folder = Folder(
            name=name,
            owner_user_id=owner_user_id,
            is_proxy=True,
            archidekt_deck_id=archidekt_id,
        )
        folder.set_primary_role(Folder.CATEGORY_DECK)
        db.session.add(folder)
        db.session.flush()
    else:
        # Refresh in place: keep the folder id so existing games still reference
        # it; replace its cards with the current Archidekt list.
        Card.query.filter_by(folder_id=folder.id).delete(synchronize_session=False)

    _apply_commander(folder, data.get("commander_name"))
    folder.archidekt_bracket = data.get("bracket")
    card_count, warnings = _populate_cards(folder, deck_lines)
    sync_folder_deck_tag_map(folder)
    db.session.commit()

    return {
        "folder_id": folder.id,
        "name": folder.name,
        "commander_name": folder.commander_name,
        "bracket": folder.archidekt_bracket,
        "card_count": card_count,
        "refreshed": refreshed,
        "warnings": warnings,
        "archidekt_deck_id": archidekt_id,
        "url": data.get("url"),
    }


__all__ = ["import_archidekt_deck"]
