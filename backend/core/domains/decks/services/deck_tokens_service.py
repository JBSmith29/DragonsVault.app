"""Deck token overview service."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict
from urllib.parse import quote

from flask import render_template
from sqlalchemy import func

from extensions import db
from models import Card, Folder, FolderRole, User
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    cache_epoch,
    cache_ready,
    ensure_cache_loaded,
    find_by_set_cn,
    prints_for_oracle,
)
from core.domains.decks.viewmodels.deck_vm import (
    DeckTokenDeckSummaryVM,
    DeckTokenDeckVM,
    DeckTokenSourceVM,
    DeckTokenVM,
)
from shared.mtg import _card_type_flags, _img_url_for_print, _lookup_print_data, _oracle_text_from_faces, _token_stubs_from_oracle_text


def _ensure_cache_ready() -> bool:
    return cache_ready() or ensure_cache_loaded()


def deck_tokens_overview():
    """Aggregate all tokens produced by cards across every deck folder."""

    def _normalize_token_name(name: str | None) -> str:
        cleaned = (name or "").strip()
        if not cleaned:
            return "token"
        lowered = cleaned.lower()
        if lowered.endswith(" token"):
            lowered = lowered[:-6].strip()
        return lowered or "token"

    def _token_pt_key(token: dict) -> str | None:
        power = token.get("power")
        toughness = token.get("toughness")
        if power is None or toughness is None:
            return None
        power_text = str(power).strip()
        toughness_text = str(toughness).strip()
        if not power_text or not toughness_text:
            return None
        return f"{power_text}/{toughness_text}"

    def _tokens_are_generic(tokens: list[dict]) -> bool:
        if not tokens:
            return True
        for token in tokens:
            name = (token.get("name") or "").strip().lower()
            if token.get("id") or (name and name != "token"):
                return False
        return True

    deck_rows = (
        Folder.query.filter(Folder.role_entries.any(FolderRole.role == FolderRole.ROLE_DECK))
        .order_by(Folder.name.asc())
        .all()
    )

    deck_map = {deck.id: deck for deck in deck_rows}
    owner_ids = {deck.owner_user_id for deck in deck_rows if deck.owner_user_id}
    owner_label_map: dict[int, str] = {}
    if owner_ids:
        owner_rows = (
            db.session.query(User.id, User.display_name, User.username, User.email)
            .filter(User.id.in_(owner_ids))
            .all()
        )
        for user_id, display_name, username, email in owner_rows:
            label = display_name or username or email
            if label:
                owner_label_map[user_id] = label

    owner_options_map: dict[str, str] = {}
    deck_owner_key_map: dict[int, str] = {}
    deck_owner_label_map: dict[int, str] = {}
    for deck in deck_rows:
        owner_id = deck.owner_user_id
        if owner_id:
            label = owner_label_map.get(owner_id) or deck.owner or f"User {owner_id}"
            key = str(owner_id)
        else:
            label = (deck.owner or "").strip() or "Unknown"
            key = "unknown"
        owner_options_map.setdefault(key, label)
        deck_owner_key_map[deck.id] = key
        deck_owner_label_map[deck.id] = label

    deck_ids = list(deck_map.keys())
    deck_count = len(deck_ids)
    if not deck_ids:
        return render_template(
            "decks/deck_tokens.html",
            tokens=[],
            deck_summaries=[],
            deck_count=0,
            deck_with_tokens=0,
            token_count=0,
            total_sources=0,
            total_qty=0,
            cache_epoch=cache_epoch(),
            owner_options=[],
        )

    have_cache = _ensure_cache_ready()
    card_rows = (
        db.session.query(
            Card.id,
            Card.name,
            Card.set_code,
            Card.collector_number,
            Card.oracle_id,
            Card.folder_id,
            func.coalesce(Card.quantity, 0).label("qty"),
            Card.oracle_text,
            Card.faces_json,
        )
        .filter(Card.folder_id.in_(deck_ids))
        .all()
    )

    print_cache_by_oracle: dict[str, dict | None] = {}
    print_cache_by_setcn: dict[tuple[str | None, str | None, str], dict | None] = {}
    token_cache_by_oracle: dict[str, list[dict]] = {}
    tokens_by_key: Dict[str, dict] = {}
    deck_token_sets: defaultdict[int, set] = defaultdict(set)
    total_sources = 0
    total_qty = 0

    for card_id, name, set_code, collector_number, oracle_id, folder_id, qty, oracle_text, faces_json in card_rows:
        qty = int(qty or 0) or 1
        deck = deck_map.get(folder_id)
        if not deck:
            continue

        text = oracle_text or _oracle_text_from_faces(faces_json)
        tokens: list[dict] = []
        if oracle_id:
            cached = token_cache_by_oracle.get(oracle_id)
            if cached is None:
                try:
                    cached = sc.tokens_from_oracle(oracle_id) or []
                except Exception:
                    cached = []
                token_cache_by_oracle[oracle_id] = cached
            tokens = cached
        if _tokens_are_generic(tokens):
            tokens = []
        if not tokens:
            tokens = _token_stubs_from_oracle_text(text)
        if not tokens:
            continue

        print_data = None
        if have_cache:
            if oracle_id:
                if oracle_id in print_cache_by_oracle:
                    print_data = print_cache_by_oracle[oracle_id]
                else:
                    try:
                        prints = prints_for_oracle(oracle_id) or []
                        print_data = prints[0] if prints else None
                    except Exception:
                        print_data = None
                    print_cache_by_oracle[oracle_id] = print_data
            if not print_data:
                set_key = (set_code, collector_number, (name or "").lower())
                if set_key in print_cache_by_setcn:
                    print_data = print_cache_by_setcn[set_key]
                else:
                    try:
                        print_data = find_by_set_cn(set_code, collector_number, name)
                    except Exception:
                        print_data = None
                    print_cache_by_setcn[set_key] = print_data

        src_img_url = None
        if have_cache and print_data:
            src_img_url = _img_url_for_print(print_data, "small") or _img_url_for_print(print_data, "normal")
        if not src_img_url and name:
            src_img_url = "https://api.scryfall.com/cards/named?format=image&version=normal&exact=" + quote(name)

        deck_name = deck.name or f"Deck {folder_id}"
        seen_token_keys: set[str] = set()
        for token in tokens:
            token_name = (token.get("name") or "Token").strip()
            token_type = (token.get("type_line") or "").strip()
            token_id = token.get("id")
            is_creature_token = "creature" in token_type.lower()
            if is_creature_token:
                pt_key = _token_pt_key(token)
                if pt_key:
                    token_key = f"creature:{_normalize_token_name(token_name)}:{pt_key}"
                else:
                    token_key = token_id or f"{token_name.lower()}|{token_type.lower()}"
            else:
                token_key = f"noncreature:{_normalize_token_name(token_name)}"
            if token_key in seen_token_keys:
                continue
            seen_token_keys.add(token_key)

            images = token.get("images") or {}
            entry = tokens_by_key.setdefault(
                token_key,
                {
                    "id": token_id,
                    "name": token_name,
                    "type_line": token_type or "Token",
                    "small": images.get("small"),
                    "normal": images.get("normal"),
                    "sources": [],
                    "decks": {},
                    "total_qty": 0,
                },
            )
            if entry.get("id") is None and token_id:
                entry["id"] = token_id
            if not entry.get("small") and images.get("small"):
                entry["small"] = images.get("small")
            if not entry.get("normal") and images.get("normal"):
                entry["normal"] = images.get("normal")
            if (entry.get("name") or "").lower() == "token" and token_name.lower() != "token":
                entry["name"] = token_name
            if (not entry.get("type_line") or entry.get("type_line") == "Token") and token_type:
                entry["type_line"] = token_type

            source_entry = {
                "card_id": card_id,
                "name": name,
                "qty": qty,
                "img": src_img_url,
                "deck_id": folder_id,
                "deck_name": deck_name,
            }
            entry["sources"].append(source_entry)
            entry["total_qty"] += qty

            deck_bucket = entry["decks"].setdefault(
                folder_id,
                {
                    "deck_id": folder_id,
                    "deck_name": deck_name,
                    "sources": [],
                    "qty": 0,
                    "card_count": 0,
                },
            )
            deck_bucket["sources"].append(source_entry)
            deck_bucket["qty"] += qty
            deck_bucket["card_count"] += 1

            deck_token_sets[folder_id].add(token_key)
            total_sources += 1
            total_qty += qty

    tokens_raw = []
    for entry in tokens_by_key.values():
        deck_groups = []
        decks_dict = entry.pop("decks")
        for deck_info in decks_dict.values():
            deck_info["sources"].sort(key=lambda source: (source["name"] or "").lower())
            deck_groups.append(deck_info)
        deck_groups.sort(key=lambda deck_info: (deck_info["deck_name"] or "").lower())
        entry["decks"] = deck_groups
        entry["deck_count"] = len(deck_groups)
        entry["sources"].sort(key=lambda source: ((source["deck_name"] or "").lower(), (source["name"] or "").lower()))
        entry["total_sources"] = len(entry["sources"])
        entry["image"] = entry.get("small") or entry.get("normal")
        owner_keys = {
            deck_owner_key_map.get(deck_info.get("deck_id") or 0, "unknown")
            for deck_info in deck_groups
        }
        owner_labels = {
            deck_owner_label_map.get(deck_info.get("deck_id") or 0, "Unknown")
            for deck_info in deck_groups
        }
        entry["owner_ids_csv"] = ",".join(sorted(owner_keys))
        entry["owner_labels"] = sorted(owner_labels, key=lambda label: label.lower())
        tokens_raw.append(entry)

    tokens_raw.sort(key=lambda token: (token["name"] or "").lower())

    token_vms: list[DeckTokenVM] = []
    for entry in tokens_raw:
        deck_ids_for_token = [
            deck.get("deck_id")
            for deck in entry.get("decks") or []
            if deck.get("deck_id") is not None
        ]
        deck_names = [
            deck.get("deck_name")
            for deck in entry.get("decks") or []
            if deck.get("deck_name")
        ]
        owner_labels = entry.get("owner_labels") or []
        search_key = (
            f"{entry.get('name') or ''} {entry.get('type_line') or ''} "
            f"{' '.join(deck_names)} {' '.join(owner_labels)}"
        ).lower().strip()
        deck_vms = []
        for deck in entry.get("decks") or []:
            sources_vm = [
                DeckTokenSourceVM(
                    card_id=source.get("card_id"),
                    name=source.get("name") or "",
                    qty=int(source.get("qty") or 0),
                    img=source.get("img"),
                )
                for source in deck.get("sources") or []
            ]
            deck_vms.append(
                DeckTokenDeckVM(
                    deck_id=deck.get("deck_id"),
                    deck_name=deck.get("deck_name") or "",
                    card_count=int(deck.get("card_count") or 0),
                    sources=sources_vm,
                )
            )
        token_vms.append(
            DeckTokenVM(
                name=entry.get("name") or "Token",
                type_line=entry.get("type_line") or "Token",
                image=entry.get("image"),
                hover_image=entry.get("normal") or entry.get("image"),
                deck_count=int(entry.get("deck_count") or 0),
                total_sources=int(entry.get("total_sources") or 0),
                total_qty=int(entry.get("total_qty") or 0),
                decks=deck_vms,
                search_key=search_key,
                deck_ids_csv=",".join(str(deck_id) for deck_id in deck_ids_for_token),
                owner_ids_csv=entry.get("owner_ids_csv") or "",
            )
        )

    deck_summary_vms: list[DeckTokenDeckSummaryVM] = []
    for deck in deck_rows:
        produced_tokens = deck_token_sets.get(deck.id, set())
        deck_summary_vms.append(
            DeckTokenDeckSummaryVM(
                id=deck.id,
                name=deck.name or f"Deck {deck.id}",
                token_count=len(produced_tokens),
                is_proxy=bool(deck.is_proxy),
            )
        )
    deck_summary_vms.sort(key=lambda item: (-item.token_count, (item.name or "").lower()))

    deck_with_tokens = sum(1 for summary in deck_summary_vms if summary.token_count)
    owner_options = [{"id": owner_id, "label": owner_options_map[owner_id]} for owner_id in owner_options_map]
    owner_options.sort(key=lambda item: (item["label"] or "").lower())

    return render_template(
        "decks/deck_tokens.html",
        tokens=token_vms,
        deck_summaries=deck_summary_vms,
        deck_count=deck_count,
        deck_with_tokens=deck_with_tokens,
        token_count=len(token_vms),
        total_sources=total_sources,
        total_qty=total_qty,
        cache_epoch=cache_epoch(),
        owner_options=owner_options,
    )


__all__ = ["deck_tokens_overview"]
