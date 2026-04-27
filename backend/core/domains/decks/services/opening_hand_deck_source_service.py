"""Deck loading and option helpers for opening-hand flows."""

from __future__ import annotations

import re
from typing import Optional

from flask import url_for
from flask_login import current_user
from sqlalchemy import or_, text
from sqlalchemy.orm import load_only

from extensions import db
from models import BuildSession, Card, Folder, FolderRole, FolderShare, UserFriend
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    find_by_set_cn,
    prints_for_oracle,
)
from core.domains.decks.services.commander_utils import split_commander_names, split_commander_oracle_ids
from core.domains.decks.services.opening_hand_payload_service import (
    _back_image_from_print,
    _commander_card_payloads,
    _ensure_cache_ready,
    _image_from_print,
    _pick_nondigital_print,
    _scryfall_card_url,
)
from shared.auth import ensure_folder_access
from shared.mtg import (
    _lookup_print_data,
    _oracle_text_from_faces,
    _type_line_from_print,
)
from shared.validation import parse_positive_int


def _parse_pasted_decklist(raw: str) -> list[tuple[str, int]]:
    want: list[tuple[str, int]] = []
    if not raw:
        return want
    for line in raw.splitlines():
        text_line = (line or "").strip()
        if not text_line or text_line.startswith("#"):
            continue
        qty = 1
        name = text_line
        leading_qty = re.match(r"^\s*(\d+)\s*[xX]?\s+(.+)$", text_line)
        trailing_qty = None if leading_qty else re.match(r"^\s*(.+?)\s*[xX]\s*(\d+)\s*$", text_line)
        if leading_qty:
            qty = int(leading_qty.group(1))
            name = leading_qty.group(2)
        elif trailing_qty:
            name = trailing_qty.group(1)
            qty = int(trailing_qty.group(2))
        name = name.strip()
        if not name:
            continue
        want.append((name, max(qty, 1)))
    return want


def _gather_commander_filters(folder: Folder) -> tuple[set[str], set[str]]:
    oracle_ids = {part for part in split_commander_oracle_ids(folder.commander_oracle_id) if part}
    names = set()
    if folder.commander_name:
        for frag in re.split(r"[\/,&]+", folder.commander_name):
            value = frag.strip().lower()
            if value:
                names.add(value)
    return oracle_ids, names


def _resolve_folder_card_print(card: Card) -> dict | None:
    try:
        pr = _lookup_print_data(card.set_code, card.collector_number, card.name, card.oracle_id)
    except Exception:
        pr = None
    if not pr and card.oracle_id:
        try:
            pr = _pick_nondigital_print(prints_for_oracle(card.oracle_id) or [])
        except Exception:
            pr = None
    if not pr:
        try:
            pr = find_by_set_cn(card.set_code, card.collector_number, card.name)
        except Exception:
            pr = None
    return pr


def _deck_entries_from_folder(folder_id: int) -> tuple[Optional[str], list[dict], list[str], list[dict]]:
    folder = db.session.get(Folder, folder_id)
    if not folder:
        return None, [], ["Deck not found."], []
    ensure_folder_access(folder, write=False, allow_shared=True)

    _ensure_cache_ready()
    commander_oracle_ids, commander_names = _gather_commander_filters(folder)
    card_rows = (
        Card.query.filter(Card.folder_id == folder_id)
        .options(
            load_only(
                Card.id,
                Card.name,
                Card.set_code,
                Card.collector_number,
                Card.oracle_id,
                Card.quantity,
                Card.type_line,
                Card.oracle_text,
            )
        )
        .all()
    )

    deck_name = folder.name or "Deck"
    entries: list[dict] = []
    warnings: list[str] = []
    for card in card_rows:
        qty = int(card.quantity or 0)
        if qty <= 0:
            continue
        card_name = card.name or ""
        lower_name = card_name.strip().lower()
        if card.oracle_id and card.oracle_id in commander_oracle_ids:
            continue
        if lower_name and lower_name in commander_names:
            continue

        pr = _resolve_folder_card_print(card)
        imgs = _image_from_print(pr)
        back_imgs = _back_image_from_print(pr)
        entries.append(
            {
                "name": card_name,
                "qty": qty,
                "card_id": card.id,
                "oracle_id": card.oracle_id,
                "small": imgs.get("small"),
                "normal": imgs.get("normal"),
                "large": imgs.get("large"),
                "back_small": back_imgs.get("small"),
                "back_normal": back_imgs.get("normal"),
                "back_large": back_imgs.get("large"),
                "detail_url": url_for("views.card_detail", card_id=card.id),
                "external_url": (pr or {}).get("scryfall_uri")
                or (pr or {}).get("uri")
                or _scryfall_card_url(card.set_code, card.collector_number),
                "type_line": (card.type_line or "").strip() or _type_line_from_print(pr),
                "oracle_text": (card.oracle_text or "").strip()
                or (pr or {}).get("oracle_text")
                or _oracle_text_from_faces((pr or {}).get("card_faces"))
                or "",
                "mana_cost": (pr or {}).get("mana_cost") or "",
                "mana_value": (pr or {}).get("cmc"),
            }
        )

    if not entries:
        warnings.append("No drawable cards found in this deck.")
    commander_cards = _commander_card_payloads(folder.commander_name, folder.commander_oracle_id)
    return deck_name, entries, warnings, commander_cards


def _opening_hand_build_key(session_id: int) -> str:
    return f"build:{session_id}"


def _opening_hand_deck_key(source: str, deck_id: int) -> str:
    return _opening_hand_build_key(deck_id) if source == "build" else str(deck_id)


def _opening_hand_build_label(session: BuildSession) -> str:
    base = session.build_name or session.commander_name or f"Build {session.id}"
    return f"Proxy Build - {base}"


def _parse_opening_hand_deck_ref(raw_value) -> tuple[str, int] | None:
    if raw_value is None:
        return None
    text_value = str(raw_value).strip()
    if not text_value:
        return None
    if text_value.startswith("build:"):
        return "build", parse_positive_int(text_value.split(":", 1)[1].strip(), field="build session id")
    return "folder", parse_positive_int(text_value, field="deck id")


def _build_session_commander_filters(session: BuildSession) -> tuple[set[str], set[str]]:
    oracle_ids = {oid for oid in split_commander_oracle_ids(session.commander_oracle_id) if oid}
    names = {name.strip().lower() for name in split_commander_names(session.commander_name) if name}
    return oracle_ids, names


def _deck_entries_from_build_session(session_id: int) -> tuple[Optional[str], list[dict], list[str], list[dict]]:
    if not current_user.is_authenticated:
        return None, [], ["Deck not found."], []
    session = BuildSession.query.filter_by(id=session_id, owner_user_id=current_user.id, status="active").first()
    if not session:
        return None, [], ["Deck not found."], []

    _ensure_cache_ready()
    commander_oracle_ids, commander_names = _build_session_commander_filters(session)
    deck_name = _opening_hand_build_label(session)
    entries: list[dict] = []
    warnings: list[str] = []
    for entry in session.cards:
        oracle_id = (entry.card_oracle_id or "").strip()
        qty = int(entry.quantity or 0)
        if not oracle_id or qty <= 0 or oracle_id in commander_oracle_ids:
            continue
        try:
            pr = _pick_nondigital_print(prints_for_oracle(oracle_id) or [])
        except Exception:
            pr = None
        card_name = (pr or {}).get("name") or "Card"
        if commander_names and card_name.strip().lower() in commander_names:
            continue
        imgs = _image_from_print(pr)
        back_imgs = _back_image_from_print(pr)
        oracle_text = (pr or {}).get("oracle_text") or _oracle_text_from_faces((pr or {}).get("card_faces"))
        entries.append(
            {
                "name": card_name,
                "qty": qty,
                "card_id": None,
                "oracle_id": oracle_id,
                "small": imgs.get("small"),
                "normal": imgs.get("normal"),
                "large": imgs.get("large"),
                "back_small": back_imgs.get("small"),
                "back_normal": back_imgs.get("normal"),
                "back_large": back_imgs.get("large"),
                "detail_url": None,
                "external_url": (pr or {}).get("scryfall_uri") or (pr or {}).get("uri"),
                "type_line": _type_line_from_print(pr),
                "oracle_text": oracle_text or "",
                "mana_cost": (pr or {}).get("mana_cost") or "",
                "mana_value": (pr or {}).get("cmc"),
            }
        )

    if not entries:
        warnings.append("No drawable cards found in this build.")
    commander_cards = _commander_card_payloads(session.commander_name, session.commander_oracle_id)
    return deck_name, entries, warnings, commander_cards


def _deck_entries_from_list(
    raw_list: str,
    commander_hint: Optional[str] = None,
) -> tuple[str, list[dict], list[str], list[dict]]:
    _ensure_cache_ready()
    parsed = _parse_pasted_decklist(raw_list)
    entries: list[dict] = []
    warnings: list[str] = []
    commander_names = set()
    commander_display_hint = None
    if commander_hint:
        commander_display_hint = commander_hint.strip()
        commander_names = {
            value.strip().lower()
            for value in re.split(r"[\/,&]+", commander_hint)
            if value and value.strip()
        }

    for name, qty in parsed:
        try:
            oracle_id = sc.unique_oracle_by_name(name)
        except Exception:
            oracle_id = None
        if not oracle_id:
            warnings.append(f'Unable to resolve "{name}".')
            continue
        try:
            pr = _pick_nondigital_print(prints_for_oracle(oracle_id) or [])
        except Exception:
            pr = None
        resolved_name = (pr or {}).get("name") or name
        if commander_names and resolved_name.strip().lower() in commander_names:
            continue
        imgs = _image_from_print(pr)
        back_imgs = _back_image_from_print(pr)
        oracle_text = (pr or {}).get("oracle_text") or _oracle_text_from_faces((pr or {}).get("card_faces"))
        entries.append(
            {
                "name": resolved_name,
                "qty": qty,
                "card_id": None,
                "oracle_id": oracle_id,
                "small": imgs.get("small"),
                "normal": imgs.get("normal"),
                "large": imgs.get("large"),
                "back_small": back_imgs.get("small"),
                "back_normal": back_imgs.get("normal"),
                "back_large": back_imgs.get("large"),
                "detail_url": None,
                "external_url": (pr or {}).get("scryfall_uri") or (pr or {}).get("uri"),
                "type_line": _type_line_from_print(pr),
                "oracle_text": oracle_text or "",
                "mana_cost": (pr or {}).get("mana_cost") or "",
                "mana_value": (pr or {}).get("cmc"),
            }
        )

    if not entries:
        warnings.append("No drawable cards were resolved from the pasted deck list.")
    commander_cards = _commander_card_payloads(commander_display_hint, None)
    return "Custom List", entries, warnings, commander_cards


def _opening_hand_deck_options() -> tuple[dict[str, dict], list[dict]]:
    role_filter = Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES))
    deck_query = Folder.query.filter(role_filter)
    if current_user and getattr(current_user, "is_authenticated", False):
        friend_ids = db.session.query(UserFriend.friend_user_id).filter(UserFriend.user_id == current_user.id)
        shared_ids = db.session.query(FolderShare.folder_id).filter(FolderShare.shared_user_id == current_user.id)
        deck_query = deck_query.filter(
            or_(
                Folder.owner_user_id == current_user.id,
                Folder.owner_user_id.in_(friend_ids),
                Folder.id.in_(shared_ids),
            )
        )
    else:
        deck_query = deck_query.filter(text("1=0"))

    deck_lookup: dict[str, dict] = {}
    deck_options: list[dict] = []
    for deck in deck_query.order_by(Folder.name.asc()).all():
        key = str(deck.id)
        label = deck.name or f"Deck {deck.id}"
        deck_lookup[key] = {"source": "folder", "deck": deck, "label": label}
        deck_options.append({"id": key, "name": label})

    if current_user.is_authenticated:
        build_sessions = (
            BuildSession.query.filter_by(owner_user_id=current_user.id, status="active")
            .order_by(BuildSession.updated_at.desc(), BuildSession.created_at.desc())
            .all()
        )
        for build_session in build_sessions:
            key = _opening_hand_build_key(build_session.id)
            label = _opening_hand_build_label(build_session)
            deck_lookup[key] = {"source": "build", "deck": build_session, "label": label}
            deck_options.append({"id": key, "name": label})

    return deck_lookup, deck_options


__all__ = [
    "_deck_entries_from_build_session",
    "_deck_entries_from_folder",
    "_deck_entries_from_list",
    "_opening_hand_build_key",
    "_opening_hand_build_label",
    "_opening_hand_deck_key",
    "_opening_hand_deck_options",
    "_parse_opening_hand_deck_ref",
]
