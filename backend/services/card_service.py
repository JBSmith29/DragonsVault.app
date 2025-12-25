"""Card browsing, collection summaries, and deck-centric routes."""

from __future__ import annotations

import base64
import json
import random
import re
from collections import defaultdict
from math import ceil
from typing import Dict, Iterable, List, Optional, Set
from sqlalchemy.exc import IntegrityError

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import load_only, selectinload

from extensions import cache, db
from models import Card, Folder, FolderRole, FolderShare, User
from models.role import Role, SubRole, CardRole, OracleCoreRoleTag, OracleEvergreenTag
from services import scryfall_cache as sc
from services.proxy_decks import fetch_goldfish_deck, resolve_proxy_cards
from services.commander_brackets import BRACKET_RULESET_EPOCH, evaluate_commander_bracket, spellbook_dataset_epoch
from services.commander_cache import compute_bracket_signature, get_cached_bracket, store_cached_bracket
from services.deck_tags import get_deck_tag_category, get_deck_tag_groups
from services.deck_service import deck_curve_rows, deck_land_mana_sources, deck_mana_pip_dist
from services.request_cache import request_cached
from services.scryfall_cache import (
    cache_epoch,
    cache_ready,
    ensure_cache_loaded,
    find_by_set_cn,
    metadata_from_print,
    prints_for_oracle,
    rulings_for_oracle,
    set_name_for_code,
    unique_oracle_by_name,
)
from services.commander_utils import (
    primary_commander_name,
    primary_commander_oracle_id,
    split_commander_names,
    split_commander_oracle_ids,
)
from services.stats import get_folder_stats

RE_CREATE_TOKEN = re.compile(r"\bcreate\b.*\btoken\b", flags=re.IGNORECASE | re.DOTALL)
from services.symbols_cache import (
    ensure_symbols_cache,
    render_mana_html,
    render_oracle_html,
    colors_to_icons,
)
from services.audit import record_audit_event
from services.authz import ensure_folder_access
from utils.db import get_or_404
from utils.validation import (
    ValidationError,
    log_validation_error,
    parse_positive_int,
    parse_positive_int_list,
)

from routes.base import (
    _bulk_print_lookup,
    _collection_metadata,
    _collection_rows_with_fallback,
    _format_price_text,
    _move_folder_choices,
    _lookup_print_data,
    _prices_for_print,
    _prices_for_print_exact,
    _safe_commit,
    _unique_art_variants,
    color_identity_name,
    compute_folder_color_identity,
)
from viewmodels.card_vm import (
    CardInfoVM,
    CardListItemVM,
    CardTokenVM,
    FolderRefVM,
    ImageSetVM,
    format_role_label,
    slice_badges,
    TypeBreakdownVM,
)
from viewmodels.deck_vm import (
    DeckCommanderVM,
    DeckOwnerSummaryVM,
    DeckTokenDeckSummaryVM,
    DeckTokenDeckVM,
    DeckTokenSourceVM,
    DeckTokenVM,
    DeckVM,
)
from viewmodels.folder_vm import CollectionBucketVM, FolderOptionVM, FolderVM, SharedFolderEntryVM
from viewmodels.opening_hand_vm import OpeningHandCardVM, OpeningHandTokenVM

HAND_SIZE = 7

# Cheap readiness check before touching the Scryfall cache on hot paths
def _ensure_cache_ready() -> bool:
    return cache_ready() or ensure_cache_loaded()

def _color_letters_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(v).upper() for v in value if v]
    else:
        raw = [ch for ch in str(value).upper()]
    return [ch for ch in raw if ch in {"W", "U", "B", "R", "G"}]


def _card_type_flags(type_line: str | None) -> dict[str, object]:
    lowered = (type_line or "").lower()
    is_land = "land" in lowered
    is_creature = "creature" in lowered
    is_instant = "instant" in lowered
    is_sorcery = "sorcery" in lowered
    is_permanent = any(
        token in lowered for token in ("artifact", "enchantment", "planeswalker", "battle", "land", "creature")
    )
    if is_land:
        zone_hint = "lands"
    elif is_creature:
        zone_hint = "creatures"
    elif is_instant or is_sorcery:
        zone_hint = "graveyard"
    elif is_permanent:
        zone_hint = "permanents"
    else:
        zone_hint = "permanents"
    return {
        "is_land": is_land,
        "is_creature": is_creature,
        "is_instant": is_instant,
        "is_sorcery": is_sorcery,
        "is_permanent": is_permanent,
        "zone_hint": zone_hint,
    }


def _request_cached_find_by_set_cn(set_code: str | None, collector_number, name: str | None):
    key = (
        "card_view",
        "setcn",
        (set_code or "").lower(),
        str(collector_number or ""),
        (name or "").lower(),
    )
    return request_cached(key, lambda: find_by_set_cn(set_code, collector_number, name))


def _request_cached_prints_for_oracle(oracle_id: str | None) -> list[dict]:
    if not oracle_id:
        return []
    key = ("card_view", "prints", oracle_id)
    return request_cached(key, lambda: prints_for_oracle(oracle_id) or [])


def _request_cached_rulings(oracle_id: str | None) -> list[dict]:
    if not oracle_id:
        return []
    key = ("card_view", "rulings", oracle_id)
    return request_cached(key, lambda: rulings_for_oracle(oracle_id) or [])


def _request_cached_primary_role_label(card_id: int | None) -> str | None:
    if not card_id:
        return None
    key = ("card_view", "primary_role", int(card_id))

    def _load() -> str | None:
        primary_role = (
            db.session.query(Role)
            .join(CardRole, CardRole.role_id == Role.id)
            .filter(CardRole.card_id == card_id, CardRole.primary.is_(True))
            .first()
        )
        if not primary_role:
            return None
        return primary_role.label or getattr(primary_role, "name", None) or primary_role.key

    return request_cached(key, _load)


def _request_cached_evergreen_labels(oracle_id: str | None) -> list[str]:
    if not oracle_id:
        return []
    key = ("card_view", "evergreen", oracle_id)

    def _load() -> list[str]:
        return [
            row[0]
            for row in (
                db.session.query(OracleEvergreenTag.keyword)
                .filter(OracleEvergreenTag.oracle_id == oracle_id)
                .order_by(OracleEvergreenTag.keyword.asc())
                .all()
            )
            if row and row[0]
        ]

    return request_cached(key, _load)


def _faces_list(faces_json) -> list[dict]:
    if not faces_json:
        return []
    if isinstance(faces_json, dict):
        faces = faces_json.get("faces") or []
    else:
        faces = faces_json
    return [face for face in faces if isinstance(face, dict)]


def _mana_costs_from_faces(faces_json) -> list[str]:
    costs = [face.get("mana_cost") for face in _faces_list(faces_json) if face.get("mana_cost")]
    return [cost for cost in costs if cost]


def _mana_cost_from_faces(faces_json) -> str | None:
    costs = _mana_costs_from_faces(faces_json)
    if not costs:
        return None
    return " // ".join(costs) if len(costs) > 1 else costs[0]


def _oracle_text_from_faces(faces_json) -> str | None:
    parts = [face.get("oracle_text") for face in _faces_list(faces_json) if face.get("oracle_text")]
    if not parts:
        return None
    return " // ".join(parts)

def _faces_image_payload(faces_json) -> list[dict]:
    faces = []
    for idx, face in enumerate(_faces_list(faces_json)):
        if not isinstance(face, dict):
            continue
        image_uris = face.get("image_uris") or {}
        if not image_uris:
            continue
        faces.append(
            {
                "small": image_uris.get("small"),
                "normal": image_uris.get("normal"),
                "large": image_uris.get("large"),
                "label": face.get("name") or ("Front" if idx == 0 else "Back"),
            }
        )
    return faces


def _scryfall_card_url(set_code: str | None, collector_number: str | None) -> str | None:
    scode = (set_code or "").strip().lower()
    cn = (collector_number or "").strip()
    if not scode or not cn:
        return None
    return f"https://scryfall.com/card/{scode}/{cn}"


def _scryfall_set_url(set_code: str | None) -> str | None:
    scode = (set_code or "").strip().lower()
    if not scode:
        return None
    return f"https://scryfall.com/sets/{scode}"


_COMMON_TOKEN_KINDS = [
    ("treasure", "Treasure"),
    ("food", "Food"),
    ("clue", "Clue"),
    ("blood", "Blood"),
    ("map", "Map"),
    ("powerstone", "Powerstone"),
]


def _token_stubs_from_oracle_text(text: str | None) -> list[dict]:
    if not text:
        return []
    lower = text.lower()
    found: list[dict] = []
    if "token" in lower:
        for key, label in _COMMON_TOKEN_KINDS:
            if f"{key} token" in lower:
                found.append(
                    {
                        "id": None,
                        "name": label,
                        "type_line": f"Token - {label}",
                        "images": {"small": None, "normal": None, "large": None},
                    }
                )
    if not found and RE_CREATE_TOKEN.search(text):
        found.append(
            {
                "id": None,
                "name": "Token",
                "type_line": "Token",
                "images": {"small": None, "normal": None, "large": None},
            }
        )
    return found



def _dashboard_card_stats(user_key: str, collection_ids: tuple[int, ...]) -> dict:
    """Aggregate collection-wide stats."""
    cache_key = ("dashboard_stats", user_key, collection_ids)

    def _load() -> dict:
        totals = (
            db.session.query(
                func.count(Card.id),
                func.coalesce(func.sum(Card.quantity), 0),
                func.count(func.distinct(Card.name)),
                func.count(func.distinct(func.lower(Card.set_code))),
            )
            .one()
        )
        total_rows, total_qty, unique_names, set_count = totals

        collection_qty = 0
        if collection_ids:
            collection_qty = (
                db.session.query(func.coalesce(func.sum(Card.quantity), 0))
                .filter(Card.folder_id.in_(collection_ids))
                .scalar()
                or 0
            )

        return {
            "rows": int(total_rows or 0),
            "qty": int(total_qty or 0),
            "unique_names": int(unique_names or 0),
            "sets": int(set_count or 0),
            "collection_qty": int(collection_qty or 0),
        }

    return request_cached(cache_key, _load)


def _prefetch_commander_cards(folder_map: dict[int, Folder]) -> dict[int, Card]:
    """Pull commander print candidates for the provided folders in one query."""
    wanted: dict[int, set[str]] = {}
    wanted_names: dict[int, set[str]] = {}
    oracle_pool: set[str] = set()
    name_pool: set[str] = set()
    for fid, folder in folder_map.items():
        ids = {oid.strip().lower() for oid in split_commander_oracle_ids(folder.commander_oracle_id) if oid.strip()}
        if ids:
            wanted[fid] = ids
            oracle_pool.update(ids)
        names = {
            name.strip().lower()
            for name in split_commander_names(getattr(folder, "commander_name", "") or "")
            if name.strip()
        }
        if names:
            wanted_names[fid] = names
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
        fid = card.folder_id
        oid = (card.oracle_id or "").strip().lower()
        if fid in wanted and oid in wanted[fid] and fid not in commander_cards:
            commander_cards[fid] = card

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
            fid = card.folder_id
            lname = (card.name or "").strip().lower()
            if fid in wanted_names and lname in wanted_names[fid] and fid not in commander_cards:
                commander_cards[fid] = card
    return commander_cards

RARITY_CHOICE_ORDER: List[tuple[str, str]] = [
    ("common", "Common"),
    ("uncommon", "Uncommon"),
    ("rare", "Rare"),
    ("mythic", "Mythic"),
    ("mythic rare", "Mythic Rare"),
    ("special", "Special"),
    ("bonus", "Bonus"),
    ("masterpiece", "Masterpiece"),
    ("timeshifted", "Timeshifted"),
    ("basic", "Basic"),
]


def _encode_state(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_state(token: str) -> Optional[dict]:
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:
        return None


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


def _card_entry_payload(
    *,
    name: str,
    card_id: Optional[int],
    oracle_id: Optional[str],
    image_small: Optional[str],
    image_normal: Optional[str],
    image_large: Optional[str],
    detail_url: Optional[str],
    external_url: Optional[str],
) -> dict:
    return {
        "name": name,
        "card_id": card_id,
        "oracle_id": oracle_id,
        "small": image_small,
        "normal": image_normal,
        "large": image_large,
        "detail_url": detail_url,
        "external_url": external_url,
    }


def _expanded_deck_entries(entries: list[dict]) -> list[dict]:
    expanded: list[dict] = []
    counter = 0
    for entry in entries:
        qty = int(entry.get("qty") or 0) or 1
        base = entry.copy()
        base.pop("qty", None)
        for idx in range(qty):
            clone = base.copy()
            uid_seed = (
                (base.get("card_id") or "")
                or (base.get("oracle_id") or "")
                or (base.get("name") or "")
            )
            clone["uid"] = f"{uid_seed}-{counter}"
            expanded.append(clone)
            counter += 1
    return expanded


def _parse_pasted_decklist(raw: str) -> list[tuple[str, int]]:
    want = []
    if not raw:
        return want
    for line in raw.splitlines():
        text = (line or "").strip()
        if not text or text.startswith("#"):
            continue
        qty = 1
        name = text
        m = re.match(r"^\s*(\d+)\s*[xX]?\s+(.+)$", text)
        if not m:
            m = re.match(r"^\s*(.+?)\s*[xX]\s*(\d+)\s*$", text)
            if m:
                name = m.group(1)
                qty = int(m.group(2))
        else:
            qty = int(m.group(1))
            name = m.group(2)
        name = name.strip()
        if not name:
            continue
        qty = max(qty, 1)
        want.append((name, qty))
    return want


def _gather_commander_filters(folder: Folder) -> tuple[set[str], set[str]]:
    oracle_ids = set()
    names = set()
    if folder and folder.commander_oracle_id:
        for part in split_commander_oracle_ids(folder.commander_oracle_id):
            oracle_ids.add(part)
    if folder and folder.commander_name:
        fragments = re.split(r"[\/,&]+", folder.commander_name)
        for frag in fragments:
            frag = frag.strip().lower()
            if frag:
                names.add(frag)
    return oracle_ids, names


def _commander_card_payload(name: Optional[str], oracle_id: Optional[str]) -> Optional[dict]:
    resolved_name = (name or "").strip() or None
    resolved_oid = (oracle_id or "").strip() or None
    if not resolved_name and not resolved_oid:
        return None

    _ensure_cache_ready()

    pr = None
    if resolved_oid:
        try:
            prints = prints_for_oracle(resolved_oid) or []
            if prints:
                pr = next((p for p in prints if not p.get("digital")), prints[0])
        except Exception:
            pr = None

    if not pr and resolved_name:
        try:
            resolved_oid = unique_oracle_by_name(resolved_name)
        except Exception:
            resolved_oid = None
        if resolved_oid:
            try:
                prints = prints_for_oracle(resolved_oid) or []
                if prints:
                    pr = next((p for p in prints if not p.get("digital")), prints[0])
            except Exception:
                pr = None

    placeholder = url_for("static", filename="img/card-placeholder.svg")
    imgs = _image_from_print(pr)
    type_line = ""
    if pr:
        type_line = (pr or {}).get("type_line") or ""
        if not type_line:
            faces = (pr or {}).get("card_faces") or []
            if faces:
                type_line = (faces[0] or {}).get("type_line") or ""

    payload = {
        "name": resolved_name or (pr or {}).get("name") or "Commander",
        "oracle_id": resolved_oid or (pr or {}).get("oracle_id"),
        "small": imgs.get("small") or placeholder,
        "normal": imgs.get("normal") or imgs.get("large") or imgs.get("small") or placeholder,
        "large": imgs.get("large") or imgs.get("normal") or imgs.get("small") or placeholder,
        "image": imgs.get("normal") or imgs.get("large") or imgs.get("small") or placeholder,
        "hover": imgs.get("large") or imgs.get("normal") or imgs.get("small") or placeholder,
        "type_line": type_line or "",
        "external_url": (pr or {}).get("scryfall_uri") or (pr or {}).get("uri"),
    }
    return payload


def _commander_card_payloads(name_blob: Optional[str], oracle_blob: Optional[str]) -> list[dict]:
    names = split_commander_names(name_blob)
    oracles = split_commander_oracle_ids(oracle_blob)
    if not names and not oracles:
        return []

    pairs: list[tuple[Optional[str], Optional[str]]] = []
    max_len = max(len(names), len(oracles), 1)
    for idx in range(max_len):
        name = names[idx] if idx < len(names) else (names[0] if names else None)
        oracle_id = oracles[idx] if idx < len(oracles) else (oracles[0] if oracles else None)
        pairs.append((name, oracle_id))

    payloads: list[dict] = []
    for name, oracle_id in pairs:
        payload = _commander_card_payload(name, oracle_id)
        if payload:
            payloads.append(payload)
    return payloads


def _deck_entries_from_folder(folder_id: int) -> tuple[Optional[str], list[dict], list[str], list[dict]]:
    folder = db.session.get(Folder, folder_id)
    if not folder:
        return None, [], ["Deck not found."], []

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
            )
        )
        .all()
    )

    entries: list[dict] = []
    warnings: list[str] = []
    deck_name = folder.name or "Deck"

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

        pr = None
        try:
            pr = _lookup_print_data(card.set_code, card.collector_number, card.name, card.oracle_id)
        except Exception:
            pr = None

        if not pr and card.oracle_id:
            try:
                prints = prints_for_oracle(card.oracle_id) or []
                pr = prints[0] if prints else None
            except Exception:
                pr = None

        if not pr:
            try:
                pr = find_by_set_cn(card.set_code, card.collector_number, card.name)
            except Exception:
                pr = None

        imgs = _image_from_print(pr)
        detail_url = url_for("views.card_detail", card_id=card.id)
        external_url = (
            (pr or {}).get("scryfall_uri")
            or (pr or {}).get("uri")
            or _scryfall_card_url(card.set_code, card.collector_number)
        )

        entries.append(
            {
                "name": card_name,
                "qty": qty,
                "card_id": card.id,
                "oracle_id": card.oracle_id,
                "small": imgs.get("small"),
                "normal": imgs.get("normal"),
                "large": imgs.get("large"),
                "detail_url": detail_url,
                "external_url": external_url,
                "type_line": getattr(card, "type_line", "") or "",
            }
        )
    if not entries:
        warnings.append("No drawable cards found in this deck.")

    commander_cards = _commander_card_payloads(folder.commander_name, folder.commander_oracle_id)

    return deck_name, entries, warnings, commander_cards


def _deck_entries_from_list(
    raw_list: str, commander_hint: Optional[str] = None
) -> tuple[str, list[dict], list[str], list[dict]]:
    _ensure_cache_ready()
    parsed = _parse_pasted_decklist(raw_list)
    entries: list[dict] = []
    warnings: list[str] = []
    commander_names = set()
    commander_display_hint = None
    if commander_hint:
        commander_display_hint = commander_hint.strip()
        parts = re.split(r"[\/,&]+", commander_hint)
        for part in parts:
            val = (part or "").strip().lower()
            if val:
                commander_names.add(val)

    for name, qty in parsed:
        oracle_id = None
        try:
            oracle_id = sc.unique_oracle_by_name(name)
        except Exception:
            oracle_id = None
        if not oracle_id:
            warnings.append(f'Unable to resolve "{name}".')
            continue
        try:
            prints = prints_for_oracle(oracle_id) or []
        except Exception:
            prints = []
        pr = prints[0] if prints else None
        resolved_name = (pr or {}).get("name") or name
        if commander_names and resolved_name.strip().lower() in commander_names:
            continue
        imgs = _image_from_print(pr)
        entries.append(
            {
                "name": resolved_name,
                "qty": qty,
                "card_id": None,
                "oracle_id": oracle_id,
                "small": imgs.get("small"),
                "normal": imgs.get("normal"),
                "large": imgs.get("large"),
                "detail_url": None,
                "external_url": (pr or {}).get("scryfall_uri") or (pr or {}).get("uri"),
                "type_line": (pr or {}).get("type_line") or "",
            }
        )
    if not entries:
        warnings.append("No drawable cards were resolved from the pasted deck list.")

    commander_cards = _commander_card_payloads(commander_display_hint, None)

    return "Custom List", entries, warnings, commander_cards


def _client_card_payload(entry: dict, placeholder: str) -> dict:
    normal = entry.get("large") or entry.get("normal") or entry.get("small") or placeholder
    small = entry.get("small") or entry.get("normal") or entry.get("large") or placeholder
    hover = entry.get("large") or entry.get("normal") or entry.get("small") or placeholder
    detail_url = entry.get("detail_url") or entry.get("external_url")
    flags = _card_type_flags(entry.get("type_line"))
    return {
        "name": entry.get("name") or "Card",
        "image": normal,
        "small": small,
        "hover": hover,
        "detail_url": detail_url,
        "type_line": entry.get("type_line") or "",
        "is_creature": bool(flags["is_creature"]),
        "is_land": bool(flags["is_land"]),
        "is_instant": bool(flags["is_instant"]),
        "is_sorcery": bool(flags["is_sorcery"]),
        "is_permanent": bool(flags["is_permanent"]),
        "zone_hint": str(flags["zone_hint"]),
    }


def _folder_name_exists(name: str, *, exclude_id: int | None = None) -> bool:
    normalized = (name or "").strip().lower()
    if not normalized:
        return False
    query = Folder.query.filter(func.lower(Folder.name) == normalized)
    if current_user and getattr(current_user, "is_authenticated", False):
        query = query.filter(Folder.owner_user_id == current_user.id)
    if exclude_id:
        query = query.filter(Folder.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _generate_unique_folder_name(base_name: str, *, exclude_id: int | None = None) -> str:
    candidate = base_name
    suffix = 2
    while _folder_name_exists(candidate, exclude_id=exclude_id):
        candidate = f"{base_name} ({suffix})"
        suffix += 1
    return candidate


def _parse_collection_lines(raw_text: str) -> tuple[list[dict], list[str]]:
    """
    Parse lines like:
      - '1 Annie Joins Up (OTJ) 191' -> qty, name, set_code, collector_number
      - '2 Sol Ring' -> qty, name (set/collector optional, will prompt to choose)
    """
    entries: list[dict] = []
    errors: list[str] = []
    if not raw_text:
        return entries, ["Enter at least one card line."]
    # qty, name, optional (SET), optional collector number
    pattern = re.compile(r"^\s*(\d+)\s*(?:x)?\s+(.+?)(?:\s*\(([^)]+)\))?(?:\s+(\S+))?\s*$")
    for idx, line in enumerate(raw_text.splitlines(), start=1):
        stripped = (line or "").strip()
        if not stripped:
            continue
        m = pattern.match(stripped)
        if not m:
            errors.append(
                f"Line {idx}: Could not parse '{stripped}'. Expected formats like '1 Card Name (SET) 123' or '2 Card Name'."
            )
            continue
        qty = max(int(m.group(1) or 0), 0)
        name = m.group(2).strip()
        set_code = (m.group(3) or "").strip().lower()
        cn = (m.group(4) or "").strip()
        if qty <= 0:
            errors.append(f"Line {idx}: Quantity must be positive.")
            continue
        entries.append(
            {
                "index": idx,
                "qty": qty,
                "name": name,
                "set_code": set_code,
                "collector_number": cn,
            }
        )
    return entries, errors


def _clone_deck_to_playground(source: Folder) -> Folder:
    """Clone an existing deck into a Build-A-Deck playground (proxy) folder."""
    if not source or not isinstance(source, Folder):
        raise ValueError("Source deck is required.")

    base_name = f"[Playground] {source.name}"
    final_name = _generate_unique_folder_name(base_name)

    playground = Folder(
        name=final_name,
        commander_oracle_id=source.commander_oracle_id,
        commander_name=source.commander_name,
        deck_tag=source.deck_tag,
        owner=source.owner,
        is_proxy=True,
    )
    playground.set_primary_role(Folder.CATEGORY_BUILD)
    db.session.add(playground)
    db.session.flush()

    for card in source.cards:
        db.session.add(
            Card(
                name=card.name,
                set_code=card.set_code,
                collector_number=card.collector_number,
                date_bought=None,
                folder_id=playground.id,
                quantity=card.quantity,
                oracle_id=card.oracle_id,
                lang=card.lang,
                is_foil=card.is_foil,
                type_line=card.type_line,
                rarity=card.rarity,
                oracle_text=card.oracle_text,
                mana_value=card.mana_value,
                colors=card.colors,
                color_identity=card.color_identity,
                color_identity_mask=card.color_identity_mask,
                layout=card.layout,
                faces_json=card.faces_json,
            )
        )

    return playground


def _create_proxy_deck_from_lines(
    deck_name: str | None,
    owner: str | None,
    commander_name: str | None,
    deck_lines: Iterable[str],
) -> tuple[Folder | None, list[str], list[str]]:
    """
    Create a proxy deck folder populated with resolved cards.

    Returns (folder, warnings, info_messages). Folder is None if no cards were resolved.
    """
    deck_lines = list(deck_lines or [])
    line_count = len(deck_lines)
    resolved_cards, resolve_errors = resolve_proxy_cards(deck_lines)
    if not resolved_cards:
        fallback_reason = "Deck parser did not resolve any recognizable cards."
        reason = resolve_errors[0] if resolve_errors else fallback_reason
        current_app.logger.warning(
            "Proxy deck creation aborted before insert: %s",
            reason,
            extra={
                "deck_name": (deck_name or "").strip() or None,
                "owner": (owner or "").strip() or None,
                "commander_hint": (commander_name or "").strip() or None,
                "line_count": line_count,
                "line_sample": deck_lines[:5],
                "warnings": resolve_errors,
            },
        )
        if not resolve_errors:
            resolve_errors = [fallback_reason]
        return None, resolve_errors, []

    info_messages: list[str] = []
    base_name = (deck_name or "").strip()
    if not base_name:
        base_name = "Proxy Deck"
    final_name = base_name
    if _folder_name_exists(final_name):
        final_name = _generate_unique_folder_name(final_name)
        if final_name != base_name:
            info_messages.append(f'Deck name in use. Created as "{final_name}".')

    folder = Folder(
        name=final_name,
        owner=owner.strip() if owner else None,
        owner_user_id=current_user.id if current_user.is_authenticated else None,
        is_proxy=True,
    )
    folder.set_primary_role(Folder.CATEGORY_DECK)

    commander_warnings: list[str] = []
    commander_clean = (commander_name or "").strip()
    if commander_clean:
        parts = split_commander_names(commander_clean) or [commander_clean]
        folder.commander_name = " // ".join(parts)
        oracle_ids: list[str] = []
        for part in parts:
            try:
                oid = unique_oracle_by_name(part)
            except Exception as exc:
                commander_warnings.append(f"Commander lookup failed for {part}: {exc}")
                oid = None
            if oid:
                oracle_ids.append(oid)
        folder.commander_oracle_id = ",".join(oracle_ids) if oracle_ids else None

    db.session.add(folder)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        final_name = _generate_unique_folder_name(final_name)
        folder.name = final_name
        db.session.add(folder)
        db.session.flush()
        info_messages.append(f'Deck name in use. Created as "{final_name}".')

    aggregated: dict[tuple[str | None, str, str, str], dict] = {}
    for card in resolved_cards:
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

    for entry in aggregated.values():
        metadata = {}
        pr = None
        if _ensure_cache_ready():
            try:
                pr = find_by_set_cn(entry["set_code"], entry["collector_number"], entry["name"])
            except Exception:
                pr = None
        if pr:
            metadata = metadata_from_print(pr)
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

    warnings = resolve_errors + commander_warnings
    return folder, warnings, info_messages


def _owner_summary(decks: list[dict]) -> list[dict]:
    summary: dict[str, dict] = {}
    for deck in decks:
        raw_owner = deck.get("owner") or ""
        owner_key = raw_owner.strip().lower()
        label = raw_owner.strip() or "Unassigned"
        entry = summary.get(owner_key)
        if not entry:
            entry = {
                "owner": raw_owner.strip() or None,
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
    return sorted(
        summary.values(),
        key=lambda item: (item["owner"] is None, item["label"].lower()),
    )


@cache.memoize(timeout=600)
def _commander_thumbnail_payload(
    folder_id: int,
    target_oracle_id: Optional[str],
    commander_name: Optional[str],
    row_count: int,
    qty_sum: int,
    epoch: int,
) -> dict[str, Optional[str]]:
    folder = db.session.get(Folder, folder_id)
    cmd_name = commander_name or (folder.commander_name if folder else None)
    small = large = None
    alt = ""
    try:
        if not cache_ready():
            ensure_cache_loaded(force=False)
    except Exception:
        pass
    resolved_oid = primary_commander_oracle_id(target_oracle_id) if target_oracle_id else None
    if not resolved_oid and folder:
        resolved_oid = primary_commander_oracle_id(folder.commander_oracle_id)
    _ = (row_count, qty_sum, epoch)  # bake stats into the cache key

    if not resolved_oid and cmd_name:
        try:
            lookup_name = primary_commander_name(cmd_name) or cmd_name
            resolved_oid = unique_oracle_by_name(lookup_name)
        except Exception:
            resolved_oid = None

    if folder and resolved_oid:
        cmd_card = (
            Card.query.filter(Card.folder_id == folder_id, Card.oracle_id == resolved_oid)
            .order_by(Card.quantity.desc())
            .first()
        )
        if cmd_card:
            cmd_name = folder.commander_name or cmd_card.name
            alt = cmd_name or "Commander"
            pr = _lookup_print_data(cmd_card.set_code, cmd_card.collector_number, cmd_card.name, cmd_card.oracle_id)
            if not pr:
                try:
                    pr = sc.find_by_set_cn_loose(cmd_card.set_code, cmd_card.collector_number, cmd_card.name) or {}
                except Exception:
                    pr = {}
            iu = pr.get("image_uris") or {}
            if iu:
                small = iu.get("small") or iu.get("normal") or iu.get("large") or iu.get("png")
                large = iu.get("png") or iu.get("large") or iu.get("normal") or iu.get("small")
            else:
                faces = pr.get("card_faces") or []
                if faces:
                    fiu = (faces[0] or {}).get("image_uris") or {}
                    small = fiu.get("small") or fiu.get("normal") or fiu.get("large") or fiu.get("png")
                    large = fiu.get("png") or fiu.get("large") or fiu.get("normal") or fiu.get("small")

    if resolved_oid and (not small or not large):
        try:
            prints = prints_for_oracle(resolved_oid) or ()
        except Exception:
            prints = ()
        if prints:
            # Prefer the same set/collector if we know it from the commander card, else any non-digital
            pr = None
            if cmd_card and cmd_card.set_code and cmd_card.collector_number:
                pr = next(
                    (
                        p for p in prints
                        if (p.get("set") or "").lower() == (cmd_card.set_code or "").lower()
                        and str(p.get("collector_number") or "").lower() == str(cmd_card.collector_number or "").lower()
                    ),
                    None,
                )
            pr = pr or next((p for p in prints if not p.get("digital")), prints[0])
            cmd_name = cmd_name or pr.get("name")
            alt = cmd_name or "Commander"
            iu = (pr or {}).get("image_uris") or {}
            small = small or iu.get("small") or iu.get("normal") or iu.get("large") or iu.get("png")
            large = large or iu.get("png") or iu.get("large") or iu.get("normal") or iu.get("small")
            if not small or not large:
                faces = (pr or {}).get("card_faces") or []
                if faces:
                    fiu = (faces[0] or {}).get("image_uris") or {}
                    small = small or fiu.get("small") or fiu.get("normal") or fiu.get("large") or fiu.get("png")
                    large = large or fiu.get("png") or fiu.get("large") or fiu.get("normal") or fiu.get("small")

    return {
        "name": cmd_name,
        "small": small,
        "large": large,
        "alt": alt or (cmd_name or "Commander"),
    }


def _owner_names(decks: list[dict]) -> list[str]:
    names = sorted({(deck.get("owner") or "").strip() for deck in decks if deck.get("owner")})
    return [name for name in names if name]


def create_proxy_deck():
    deck_name = (request.form.get("deck_name") or "").strip()
    owner = (request.form.get("deck_owner") or "").strip() or None
    commander_input = (request.form.get("deck_commander") or "").strip()
    decklist_text = request.form.get("decklist") or ""
    deck_url = (request.form.get("deck_url") or "").strip()
    expects_json = request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"
    if not expects_json:
        best = request.accept_mimetypes.best
        expects_json = best == "application/json"

    fetched_errors: list[str] = []
    if deck_url:
        fetched_name = fetched_owner = fetched_commander = None
        fetched_lines: list[str] = []
        errors: list[str] = []

        # ARCHIDEKT REMOVED — replaced by internal role engine
        fetched_name, fetched_owner, fetched_commander, fetched_lines, errors = fetch_goldfish_deck(deck_url)
        fetched_errors.extend(errors)
        if fetched_lines:
            decklist_text = decklist_text or "\n".join(fetched_lines)
        if not deck_name and fetched_name:
            deck_name = fetched_name
        if not owner and fetched_owner:
            owner = fetched_owner
        if not commander_input and fetched_commander:
            commander_input = fetched_commander

    deck_lines = [line for line in (decklist_text.splitlines() if decklist_text else []) if line.strip()]
    if not deck_lines:
        detail = "No cards were found in the submitted decklist."
        current_app.logger.warning(
            "Proxy deck creation blocked: empty deck submission.",
            extra={
                "deck_name": deck_name or None,
                "owner": owner,
                "deck_url": deck_url or None,
                "fetched_errors": fetched_errors,
            },
        )
        if expects_json:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": detail,
                        "warnings": fetched_errors[:10],
                    }
                ),
                400,
            )
        flash(detail, "warning")
        for msg in fetched_errors:
            flash(msg, "warning")
        return redirect(request.referrer or url_for("views.decks_overview"))

    folder, creation_warnings, info_messages = _create_proxy_deck_from_lines(
        deck_name,
        owner,
        commander_input,
        deck_lines,
    )
    if not folder:
        combined = fetched_errors + creation_warnings
        detail = combined[0] if combined else "No cards were found in the submitted decklist."
        current_app.logger.warning(
            "Proxy deck creation failed after parsing.",
            extra={
                "deck_name": deck_name or None,
                "owner": owner,
                "deck_url": deck_url or None,
                "commander_hint": commander_input or None,
                "line_count": len(deck_lines),
                "warning_sample": combined[:5],
            },
        )
        if expects_json:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": detail,
                        "warnings": combined[:10],
                    }
                ),
                400,
            )
        flash(f"Unable to create proxy deck: {detail}", "danger")
        for msg in combined:
            flash(msg, "warning")
        return redirect(request.referrer or url_for("views.decks_overview"))

    db.session.commit()
    redirect_url = url_for("views.folder_detail", folder_id=folder.id)

    combined_warnings = fetched_errors + creation_warnings
    if expects_json:
        return (
            jsonify(
                {
                    "ok": True,
                    "folder_id": folder.id,
                    "redirect": redirect_url,
                    "warnings": combined_warnings[:10],
                    "info": info_messages[:5],
                }
            ),
            200,
        )

    for msg in info_messages:
        flash(msg, "info")

    if combined_warnings:
        for msg in combined_warnings[:5]:
            flash(msg, "warning")
        if len(combined_warnings) > 5:
            flash(f"{len(combined_warnings) - 5} additional warnings suppressed.", "warning")

    flash(f'Created proxy deck "{folder.name}".', "success")
    return redirect(redirect_url)


def create_proxy_deck_bulk():
    raw_urls = (request.form.get("deck_urls") or "").strip()
    if not raw_urls:
        flash("Please provide at least one MTGGoldfish deck URL.", "warning")
        return redirect(request.referrer or url_for("views.decks_overview"))

    urls = [line.strip() for line in raw_urls.splitlines() if line.strip()]
    if not urls:
        flash("Please provide at least one MTGGoldfish deck URL.", "warning")
        return redirect(request.referrer or url_for("views.decks_overview"))

    imported: list[Folder] = []
    warning_messages: list[str] = []
    info_messages: list[str] = []
    failure_messages: list[str] = []

    for url in urls:
        fetched_name, fetched_owner, fetched_commander, fetched_lines, fetch_errors = fetch_goldfish_deck(url)
        if not fetched_lines:
            message = fetch_errors[0] if fetch_errors else "No decklist data returned."
            failure_messages.append(f"{url}: {message}")
            continue

        folder, creation_warnings, creation_info = _create_proxy_deck_from_lines(
            fetched_name,
            fetched_owner,
            fetched_commander,
            fetched_lines,
        )
        if not folder:
            combined = fetch_errors + creation_warnings
            message = combined[0] if combined else "Unable to import deck."
            failure_messages.append(f"{url}: {message}")
            continue

        imported.append(folder)
        info_messages.extend(creation_info)

        combined_warnings = fetch_errors + creation_warnings
        for msg in combined_warnings:
            warning_messages.append(f"{folder.name}: {msg}")

    if imported:
        db.session.commit()
        flash(
            f'Imported {len(imported)} proxy deck{"s" if len(imported) != 1 else ""}.',
            "success",
        )
        for msg in info_messages:
            flash(msg, "info")
    else:
        db.session.rollback()

    for msg in warning_messages[:5]:
        flash(msg, "warning")
    if len(warning_messages) > 5:
        flash(f"{len(warning_messages) - 5} additional warnings suppressed.", "warning")

    for msg in failure_messages[:5]:
        flash(msg, "danger")
    if len(failure_messages) > 5:
        flash(f"{len(failure_messages) - 5} additional errors suppressed.", "danger")

    if not imported and not failure_messages:
        flash("No decks were imported.", "warning")

    return redirect(url_for("views.decks_overview"))


def api_fetch_proxy_deck():
    payload = request.get_json(silent=True) or {}
    deck_url = (payload.get("deck_url") or request.form.get("deck_url") or "").strip()
    if not deck_url:
        return jsonify({"ok": False, "error": "No deck URL provided."}), 400

    name, owner, commander, lines, errors = fetch_goldfish_deck(deck_url)
    response = {
        "ok": True,
        "deck_name": name,
        "owner": owner,
        "commander": commander,
        "decklist": "\n".join(lines) if lines else "",
        "warnings": errors,
    }
    if not lines:
        response["ok"] = False
        response["error"] = errors[0] if errors else "Unable to read decklist from MTGGoldfish."
        status = 400
    else:
        status = 200
    return jsonify(response), status


def _deck_drawer_summary(folder: Folder) -> dict:
    _ensure_cache_ready()

    cards = (
        db.session.query(
            Card.id,
            Card.name,
            Card.set_code,
            Card.collector_number,
            Card.oracle_id,
            Card.lang,
            Card.is_foil,
            Card.quantity,
            Card.type_line,
            Card.oracle_text,
            Card.mana_value,
            Card.faces_json,
        )
        .filter(Card.folder_id == folder.id)
        .all()
    )

    BASE_TYPES = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]
    type_counts = {t: 0 for t in BASE_TYPES}

    def _mana_cost_from_faces(faces_json) -> str | None:
        if not faces_json:
            return None
        if isinstance(faces_json, dict):
            faces = faces_json.get("faces") or []
        else:
            faces = faces_json
        costs = [face.get("mana_cost") for face in faces if isinstance(face, dict) and face.get("mana_cost")]
        if not costs:
            return None
        return " // ".join(costs) if len(costs) > 1 else costs[0]

    bracket_cards: list[dict[str, object]] = []
    total_cards = 0

    for cid, name, scode, cn, oid, lang, is_foil, qty, type_line, oracle_text, mana_value, faces_json in cards:
        qty = int(qty or 0) or 1
        total_cards += qty
        type_line = type_line or ""
        if type_line:
            for t in BASE_TYPES:
                if t in type_line:
                    type_counts[t] += qty

        mana_cost = _mana_cost_from_faces(faces_json)

        bracket_card = {
            "name": name,
            "type_line": type_line or "",
            "oracle_text": oracle_text or "",
            "mana_value": mana_value,
            "quantity": qty,
            "mana_cost": mana_cost,
            "produced_mana": None,
        }
        bracket_cards.append(bracket_card)

    type_breakdown = [(t, type_counts[t]) for t in BASE_TYPES if type_counts[t] > 0]
    mana_pip_dist = deck_mana_pip_dist(folder.id, mode="drawer")
    land_mana_sources = [
        {"color": color, "icon": icon, "label": color, "count": count}
        for color, icon, count in deck_land_mana_sources(folder.id, filter_by_identity=False)
    ]
    curve_rows = deck_curve_rows(folder.id, mode="drawer")

    placeholder_thumb = url_for("static", filename="img/card-placeholder.svg")
    commander_payload = None
    if folder.commander_oracle_id or folder.commander_name:
        pr = None
        try:
            o_id = primary_commander_oracle_id(folder.commander_oracle_id)
            if not o_id and folder.commander_name:
                lookup_name = primary_commander_name(folder.commander_name) or folder.commander_name
                o_id = unique_oracle_by_name(lookup_name)
            if o_id:
                prints = prints_for_oracle(o_id) or []
                pr = prints[0] if prints else None
        except Exception:
            pr = None
        if pr:
            iu = pr.get("image_uris") or {}
            commander_payload = {
                "name": folder.commander_name or pr.get("name"),
                "image": iu.get("small") or iu.get("normal"),
                "hover": iu.get("large") or iu.get("normal") or iu.get("small"),
                "scryfall": pr.get("scryfall_uri"),
            }
        else:
            commander_payload = {"name": folder.commander_name}

    if commander_payload:
        commander_payload.setdefault("image", placeholder_thumb)
        commander_payload.setdefault("hover", placeholder_thumb)

    commander_stub = {
        "oracle_id": primary_commander_oracle_id(folder.commander_oracle_id),
        "name": primary_commander_name(folder.commander_name) or folder.commander_name,
    }
    epoch = cache_epoch() + BRACKET_RULESET_EPOCH + spellbook_dataset_epoch()
    signature = compute_bracket_signature(bracket_cards, commander_stub, epoch=epoch)
    commander_ctx = None
    if folder.id:
        commander_ctx = get_cached_bracket(folder.id, signature, epoch)
    if not commander_ctx:
        commander_ctx = evaluate_commander_bracket(bracket_cards, commander_stub)
        if folder.id:
            store_cached_bracket(folder.id, signature, epoch, commander_ctx)

    spellbook_details = commander_ctx.get("spellbook_details") or []
    if len(spellbook_details) > 8:
        spellbook_details = spellbook_details[:8]

    deck_color_letters, _deck_color_label = compute_folder_color_identity(folder.id)
    deck_color_list = list(deck_color_letters) if deck_color_letters else []

    deck_tag_label = None
    if folder.deck_tag:
        for category, tags in get_deck_tag_groups().items():
            if folder.deck_tag in tags:
                deck_tag_label = f"{category}: {folder.deck_tag}"
                break
        if not deck_tag_label:
            deck_tag_label = folder.deck_tag

    return {
        "deck": {
            "id": folder.id,
            "name": folder.name,
            "tag": folder.deck_tag,
            "tag_label": deck_tag_label,
            "tag_category": get_deck_tag_category(folder.deck_tag),
        },
        "commander": commander_payload,
        "bracket": {
            "level": commander_ctx.get("level"),
            "label": commander_ctx.get("label"),
            "score": commander_ctx.get("score"),
            "summary_points": commander_ctx.get("summary_points") or [],
            "spellbook_combos": spellbook_details,
        },
        "type_breakdown": type_breakdown,
        "mana_pip_dist": mana_pip_dist,
        "land_mana_sources": land_mana_sources,
        "curve_rows": curve_rows,
        "total_cards": total_cards,
        "deck_colors": deck_color_list,
    }


def _apply_cache_type_color_filters(base_query, selected_types, selected_colors, color_mode, type_mode="contains"):
    """
    Fallback filter using normalized DB fields (type_line + color_identity_mask).
    """
    want_types = [t.lower() for t in (selected_types or []) if t]
    want_colors = [c.upper() for c in (selected_colors or []) if c]
    use_types = bool(want_types)
    use_colors = bool(want_colors)
    if not use_types and not use_colors:
        return base_query

    query = base_query
    if use_types:
        if type_mode == "exact":
            for t in want_types:
                query = query.filter(Card.type_line.ilike(f"%{t}%"))
        else:
            query = query.filter(or_(*[Card.type_line.ilike(f"%{t}%") for t in want_types]))

    if use_colors:
        has_c = "C" in want_colors
        non_c = [c for c in want_colors if c != "C"]
        mask_map = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}
        want_mask = 0
        for ch in non_c:
            want_mask |= mask_map.get(ch, 0)
        mask_expr = func.coalesce(Card.color_identity_mask, 0)

        if color_mode == "exact":
            if has_c and non_c:
                query = query.filter(mask_expr == -1)
            elif has_c:
                query = query.filter(mask_expr == 0)
            else:
                query = query.filter(mask_expr == want_mask)
        else:
            if has_c and not non_c:
                query = query.filter(mask_expr == 0)
            else:
                if want_mask:
                    query = query.filter(mask_expr.op("&")(want_mask) == want_mask)
                if has_c:
                    query = query.filter(or_(mask_expr == 0, mask_expr.op("&")(want_mask) == want_mask))

    return query


def dashboard_index():
    return redirect(url_for("views.dashboard"))


def dashboard():
    collection_ids, _collection_names, _collection_lower = _collection_metadata()

    stats_key = str(session.get("_user_id") or "anon")
    stats = _dashboard_card_stats(stats_key, tuple(sorted(collection_ids)))
    total_rows = stats["rows"]
    total_qty = stats["qty"]
    unique_names = stats["unique_names"]
    set_count = stats["sets"]

    _ = ensure_cache_loaded()
    deck_query = (
        db.session.query(
            Folder.id,
            Folder.name,
            func.count(Card.id).label("rows"),
            func.coalesce(func.sum(Card.quantity), 0).label("qty"),
        )
        .outerjoin(Card, Card.folder_id == Folder.id)
        .filter(Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES)))
    )
    deck_rows = (
        deck_query.group_by(Folder.id, Folder.name)
        .order_by(func.coalesce(func.sum(Card.quantity), 0).desc(), Folder.name.asc())
        .all()
    )
    decks = [
        {"id": rid, "name": rname, "rows": int(rrows or 0), "qty": int(rqty or 0)}
        for (rid, rname, rrows, rqty) in deck_rows
    ]
    deck_count = len(decks)

    collection_qty = stats["collection_qty"]

    deck_vms: list[DeckVM] = []
    placeholder_thumb = url_for("static", filename="img/card-placeholder.svg")

    def ci_html_from_letters(letters: str) -> str:
        if not letters:
            return '<span class="pip-row"><img class="mana mana-sm" src="/static/symbols/C.svg" alt="{C}"></span>'
        return (
            '<span class="pip-row">'
            + "".join(
                f'<img class="mana mana-sm" src="/static/symbols/{c}.svg" alt="{{{c}}}">' for c in letters
            )
            + "</span>"
        )

    def exact_print_for_card(card_row: Card | None) -> dict | None:
        if not card_row:
            return None
        try:
            pr = find_by_set_cn(card_row.set_code, card_row.collector_number, card_row.name)
            if pr:
                return pr
        except Exception:
            pr = None
        return _lookup_print_data(
            getattr(card_row, "set_code", None),
            getattr(card_row, "collector_number", None),
            getattr(card_row, "name", None),
            getattr(card_row, "oracle_id", None),
        )

    if decks:
        folder_ids = [d["id"] for d in decks]
        folder_map = {f.id: f for f in Folder.query.filter(Folder.id.in_(folder_ids)).all()}
        commander_cards = _prefetch_commander_cards(folder_map)
        epoch = cache_epoch()

        for deck in decks:
            fid = deck["id"]
            f = folder_map.get(fid)
            cmd_card = commander_cards.get(fid) if f else None
            oracle_ids = []
            if f:
                oracle_ids = [oid.strip() for oid in split_commander_oracle_ids(f.commander_oracle_id) if oid.strip()]
            pr = exact_print_for_card(cmd_card) if cmd_card else None
            images = []

            def add_image_from_print(pr_obj, name_hint=None):
                if not pr_obj:
                    return
                img = _image_from_print(pr_obj)
                small = img.get("small") or img.get("normal") or img.get("large")
                normal = img.get("normal") or img.get("large") or img.get("small")
                large = img.get("large") or img.get("normal") or img.get("small")
                name_val = name_hint or getattr(f, "commander_name", None) or (cmd_card.name if cmd_card else pr_obj.get("name"))
                images.append({
                    "name": name_val,
                    "small": small or large or placeholder_thumb,
                    "normal": normal or small or placeholder_thumb,
                    "large": large or normal or placeholder_thumb,
                    "alt": name_val or "Commander",
                })

            if not pr and f:
                pr = _lookup_print_data(
                    getattr(f, "commander_set_code", None),
                    getattr(f, "commander_collector_number", None),
                    getattr(f, "commander_name", None),
                    primary_commander_oracle_id(getattr(f, "commander_oracle_id", None)),
                )
            if pr:
                add_image_from_print(pr)

            if not images and oracle_ids:
                primary_oid = primary_commander_oracle_id(getattr(f, "commander_oracle_id", None)) if f else None
                target_oid = primary_oid or oracle_ids[0]
                try:
                    prints = prints_for_oracle(target_oid) or []
                except Exception:
                    prints = []
                if prints:
                    add_image_from_print(prints[0])

            if not images:
                target_oid = primary_commander_oracle_id(getattr(f, "commander_oracle_id", None)) if f else None
                thumb_payload = _commander_thumbnail_payload(
                    fid,
                    target_oid,
                    getattr(f, "commander_name", None) if f else None,
                    deck.get("rows") or 0,
                    deck.get("qty") or 0,
                    epoch,
                )
                images.append({
                    "name": thumb_payload.get("name"),
                    "small": thumb_payload.get("small") or placeholder_thumb,
                    "normal": None,
                    "large": thumb_payload.get("large") or placeholder_thumb,
                    "alt": thumb_payload.get("alt") or (thumb_payload.get("name") or "Commander"),
                })

            cmd_vm = None
            if images:
                primary = images[0]
                images_vm = [
                    ImageSetVM(
                        small=img.get("small"),
                        normal=img.get("normal"),
                        large=img.get("large"),
                        label=img.get("name"),
                    )
                    for img in images
                ]
                cmd_vm = DeckCommanderVM(
                    name=primary.get("name"),
                    small=primary.get("small"),
                    large=primary.get("large"),
                    alt=primary.get("alt"),
                    images=images_vm,
                )

            letters, _label = compute_folder_color_identity(fid)
            letters = letters or ""
            deck_vms.append(
                DeckVM(
                    id=fid,
                    name=deck.get("name") or "",
                    qty=int(deck.get("qty") or 0),
                    owner=getattr(f, "owner", None) if f else None,
                    owner_key=(getattr(f, "owner", None) or "").strip().lower() if f else "",
                    is_proxy=bool(getattr(f, "is_proxy", False)) if f else False,
                    tag=getattr(f, "deck_tag", None) if f else None,
                    tag_label=getattr(f, "deck_tag", None) if f else None,
                    ci_name=color_identity_name(letters),
                    ci_html=ci_html_from_letters(letters),
                    ci_letters=letters or "C",
                    commander=cmd_vm,
                    bracket_level=None,
                    bracket_label=None,
                )
            )

    return render_template(
        "decks/dashboard.html",
        stats={
            "rows": int(total_rows),
            "qty": int(total_qty),
            "unique_names": int(unique_names),
            "decks": int(deck_count),
            "collection_qty": int(collection_qty),
            "sets": int(set_count),
        },
        decks=deck_vms,
    )


def api_card(card_id):
    ensure_symbols_cache(force=False)

    card = get_or_404(
        Card,
        card_id,
        options=(
            selectinload(Card.folder),
            selectinload(Card.roles),
            selectinload(Card.subroles),
        ),
    )
    have_cache = ensure_cache_loaded()
    role_names = []
    subrole_names = []
    try:
        role_names = [
            (r.label or getattr(r, "name", None) or r.key)
            for r in (card.roles or [])
            if (r.label or getattr(r, "name", None) or r.key)
        ]
        subrole_names = [
            (s.label or getattr(s, "name", None) or s.key)
            for s in (card.subroles or [])
            if (s.label or getattr(s, "name", None) or s.key)
        ]
        primary_role = _request_cached_primary_role_label(card.id)
    except Exception:
        role_names = role_names or []
        subrole_names = subrole_names or []
        primary_role = None

    best = _lookup_print_data(card.set_code, card.collector_number, card.name, card.oracle_id) if have_cache else {}

    def _img(obj):
        if not obj:
            return {"small": None, "normal": None, "large": None}
        iu = obj.get("image_uris") or {}
        if iu:
            return {
                "small": iu.get("small"),
                "normal": iu.get("normal"),
                "large": iu.get("large") or iu.get("png"),
            }
        faces = obj.get("card_faces") or []
        if faces:
            iu2 = (faces[0] or {}).get("image_uris") or {}
            return {
                "small": iu2.get("small"),
                "normal": iu2.get("normal"),
                "large": iu2.get("large") or iu2.get("png"),
            }
        return {"small": None, "normal": None, "large": None}

    oracle_text = getattr(card, "oracle_text", None) or _oracle_text_from_faces(getattr(card, "faces_json", None))
    mana_cost = _mana_cost_from_faces(getattr(card, "faces_json", None))
    colors = _color_letters_list(getattr(card, "colors", None))
    color_identity = _color_letters_list(getattr(card, "color_identity", None)) or colors
    if not colors:
        colors = color_identity

    info = {
        "name": card.name,
        "mana_cost": mana_cost,
        "mana_cost_html": render_mana_html(mana_cost, use_local=False),
        "type_line": getattr(card, "type_line", None),
        "oracle_text": oracle_text,
        "oracle_text_html": render_oracle_html(oracle_text, use_local=False),
        "colors": colors or [],
        "color_identity": color_identity or [],
        "rarity": getattr(card, "rarity", None),
        "set": (card.set_code or ""),
        "collector_number": card.collector_number,
        "scryfall_uri": (best or {}).get("scryfall_uri") or _scryfall_card_url(card.set_code, card.collector_number),
        "scryfall_set_uri": (best or {}).get("scryfall_set_uri") or _scryfall_set_url(card.set_code),
        "cmc": getattr(card, "mana_value", None),
        "set_name": (set_name_for_code(card.set_code) if have_cache else None),
        "legalities": (best or {}).get("legalities") or {},
        "commander_legality": ((best or {}).get("legalities") or {}).get("commander"),
    }
    info["faces"] = _faces_image_payload(getattr(card, "faces_json", None))

    images = []
    im = _img(best)
    images.append({"small": im["small"], "normal": im["normal"], "large": im["large"]})
    info["scryfall_id"] = (best or {}).get("id")

    resp = jsonify(
        {
            "card": {
                "id": card.id,
                "quantity": card.quantity,
                "folder": card.folder.name if card.folder else None,
                "roles": role_names,
                "subroles": subrole_names,
                "primary_role": primary_role,
            },
            "info": info,
            "images": images,
        }
    )
    resp.cache_control.public = True
    resp.cache_control.max_age = 60
    return resp


def list_cards():
    """
    Browse / filter cards with deck context (★ commander column appears for deck folders only).
    Fallback to Scryfall cache when DB-lacking columns are requested.
    """
    BASE_TYPES = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]

    def extract_base_types(type_line: str | None):
        if not type_line:
            return []
        s = (type_line or "").lower()
        return [t for t in BASE_TYPES if t.lower() in s]

    collection_ids, collection_names, _collection_lower = _collection_metadata()

    # Query params
    q_text = (request.args.get("q") or "").strip()
    folder_arg = (request.args.get("folder") or "").strip()
    if folder_arg:
        try:
            parse_positive_int(folder_arg, field="folder id")
        except ValidationError as exc:
            log_validation_error(exc, context="list_cards")
            flash("Invalid folder selection.", "warning")
            return redirect(url_for("views.list_cards"))
    set_code = (request.args.get("set") or "").strip().lower()
    typal = (request.args.get("tribe") or request.args.get("typal") or "").strip().lower()
    foil_arg = (request.args.get("foil_only") or request.args.get("foil") or "").strip().lower()
    foil_only = foil_arg in {"1", "true", "yes", "on", "y"}
    rarity = (request.args.get("rarity") or "").strip().lower()
    if rarity == "any":
        rarity = ""

    role_query_text = (request.args.get("role_q") or "").strip()
    roles_param_vals = request.args.getlist("roles")
    subroles_param_vals = request.args.getlist("subroles")
    roles_param = (request.args.get("roles") or "").strip()
    subroles_param = (request.args.get("subroles") or "").strip()
    role_list = [r.strip() for r in roles_param.split(",") if r.strip()] if roles_param else []
    subrole_list = [s.strip() for s in subroles_param.split(",") if s.strip()] if subroles_param else []
    if roles_param_vals:
        role_list.extend([r.strip() for r in roles_param_vals if r.strip()])
    if subroles_param_vals:
        subrole_list.extend([s.strip() for s in subroles_param_vals if s.strip()])
    role_list = [r for r in role_list if r]
    subrole_list = [s for s in subrole_list if s]

    type_mode = (request.args.get("type_mode") or "contains").lower()
    raw_types_any = [t for t in request.args.getlist("type_any") if t]
    raw_types = [t for t in request.args.getlist("type") if t]
    selected_types = [
        t.lower()
        for t in ((raw_types if type_mode == "exact" else raw_types_any) or raw_types or raw_types_any)
    ]

    selected_colors = [c.lower() for c in request.args.getlist("color")]  # may include 'c'
    color_mode = (request.args.get("color_mode") or "contains").lower()

    scope = (request.args.get("scope") or "").lower()
    collection_flag = (request.args.get("collection") == "1") or (scope == "collection")

    sort = (request.args.get("sort") or "name").lower()
    direction = (request.args.get("dir") or "asc").lower()
    reverse = direction == "desc"

    # Paging
    allowed_per_page = (25, 50, 100, 150, 200)
    try:
        per = int(request.args.get("per", request.args.get("per_page", request.args.get("page_size", 25))))
    except Exception:
        per = 25
    if per not in allowed_per_page:
        per = 25
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except Exception:
        page = 1

    # Deck context
    folder_id_int = int(folder_arg) if folder_arg.isdigit() else None
    folder_obj = db.session.get(Folder, folder_id_int) if folder_id_int else None
    is_deck_folder = bool(folder_obj and not folder_obj.is_collection)
    folder_is_proxy = bool(getattr(folder_obj, "is_proxy_deck", False))

    # Base query
    query = Card.query
    if role_list:
        query = query.join(Card.roles).filter(Role.label.in_(role_list))
    if subrole_list:
        query = query.join(Card.subroles).filter(SubRole.label.in_(subrole_list))
    if role_query_text:
        role_query_base = role_query_text.lower().strip()
        role_query_alt = re.sub(r"[_-]+", " ", role_query_base).strip()
        role_query_tokens = {role_query_base, role_query_alt}
        role_query_patterns = [f"%{token}%" for token in role_query_tokens if token]
        role_match = (
            db.session.query(OracleCoreRoleTag.id)
            .filter(OracleCoreRoleTag.oracle_id == Card.oracle_id)
            .filter(or_(*[func.lower(OracleCoreRoleTag.role).ilike(pattern) for pattern in role_query_patterns]))
            .exists()
        )
        evergreen_match = (
            db.session.query(OracleEvergreenTag.id)
            .filter(OracleEvergreenTag.oracle_id == Card.oracle_id)
            .filter(or_(*[func.lower(OracleEvergreenTag.keyword).ilike(pattern) for pattern in role_query_patterns]))
            .exists()
        )
        query = query.filter(or_(role_match, evergreen_match))
    if role_list or subrole_list:
        query = query.distinct()
    if role_list:
        query = query.join(Card.roles).filter(Role.label.in_(role_list))
    if subrole_list:
        query = query.join(Card.subroles).filter(SubRole.label.in_(subrole_list))
    if q_text:
        for tok in [t for t in q_text.split() if t]:
            query = query.filter(Card.name.ilike(f"%{tok}%"))
    if folder_id_int is not None:
        query = query.filter(Card.folder_id == folder_id_int)
    if collection_flag:
        if collection_ids:
            query = query.filter(Card.folder_id.in_(collection_ids))
        else:
            query = query.filter(Card.id == -1)
    if set_code:
        query = query.filter(func.lower(Card.set_code) == set_code)
    if foil_only:
        query = query.filter(Card.is_foil.is_(True))
    if rarity:
        if hasattr(Card, "rarity"):
            query = query.filter(func.lower(Card.rarity) == rarity)

    # Typal filter
    needs_typal_fallback = False
    if typal:
        if hasattr(Card, "type_line"):
            query = query.filter(Card.type_line.ilike(f"%{typal}%"))
        else:
            needs_typal_fallback = True

    # Base type filters
    needs_type_fallback = False
    use_db_types = hasattr(Card, "type_line")
    if selected_types:
        if use_db_types:
            if type_mode == "exact":
                for t in selected_types:
                    query = query.filter(Card.type_line.ilike(f"%{t}%"))
            else:
                query = query.filter(or_(*[Card.type_line.ilike(f"%{t}%") for t in selected_types]))
        else:
            needs_type_fallback = True

    # Color identity filters
    needs_color_fallback = False
    if selected_colors:
        has_c = "c" in selected_colors
        non_c = [c.upper() for c in selected_colors if c != "c"]
        mask_map = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}
        want_mask = 0
        for ch in non_c:
            want_mask |= mask_map.get(ch, 0)
        mask_expr = func.coalesce(Card.color_identity_mask, 0)

        if color_mode == "exact":
            if has_c and non_c:
                query = query.filter(mask_expr == -1)
            elif has_c:
                query = query.filter(mask_expr == 0)
            else:
                query = query.filter(mask_expr == want_mask)
        else:
            if has_c and not non_c:
                query = query.filter(mask_expr == 0)
            else:
                if want_mask:
                    query = query.filter(mask_expr.op("&")(want_mask) == want_mask)
                if has_c:
                    query = query.filter(or_(mask_expr == 0, mask_expr.op("&")(want_mask) == want_mask))

    if needs_typal_fallback or needs_type_fallback or needs_color_fallback:
        query = _apply_cache_type_color_filters(
            query,
            selected_types if needs_type_fallback else None,
            selected_colors,
            color_mode,
            type_mode=type_mode,
        )

    if typal and needs_typal_fallback:
        rows = query.with_entities(Card.id, Card.set_code, Card.collector_number, Card.name, Card.oracle_id).all()
        keep_ids = []
        for cid, scode, cn, name, oid in rows:
            p = _lookup_print_data(scode, cn, name, oid)
            tl = (p or {}).get("type_line") or ""
            if typal.lower() in tl.lower():
                keep_ids.append(cid)
        if keep_ids:
            query = Card.query.filter(Card.id.in_(keep_ids))
        else:
            sets, langs, _folders = _facets()
            set_options = _set_options_with_names(sets)
            rarity_options = _rarity_options()
            move_folder_options = _move_folder_choices()
            return render_template(
                "cards/cards.html",
                cards=[],
                total=0,
                page=1,
                per=per,
                pages=1,
                prev_url=None,
                next_url=None,
                page_urls=[],
                start=0,
                end=0,
                q=q_text,
                folder_id=folder_arg,
                folder_is_proxy=folder_is_proxy,
                set_code=set_code,
                tribe=typal,
                foil_only=foil_only,
                rarity=rarity,
                selected_types=selected_types,
                selected_colors=selected_colors,
                color_mode=color_mode,
                type_mode=type_mode,
                collection_flag=collection_flag,
                sort=sort,
                direction=direction,
                sets=sets,
                langs=langs,
                set_options=set_options,
                rarity_options=rarity_options,
                per_page=per,
                is_deck_folder=is_deck_folder,
                collection_folders=collection_names,
                move_folder_options=move_folder_options,
            )

    # Sorting (DB-native where possible)
    if sort == "qty":
        order_col = func.coalesce(Card.quantity, 0)
    elif sort == "set":
        order_col = func.lower(Card.set_code)
    elif sort == "cn":
        order_col = func.lower(Card.collector_number)
    elif sort == "foil":
        order_col = Card.is_foil
    elif sort in {"ctype", "type"}:
        order_col = func.lower(func.coalesce(Card.type_line, ""))
    elif sort in {"rar", "rarity"}:
        order_col = func.lower(func.coalesce(Card.rarity, ""))
    elif sort in {"colors", "colour"}:
        order_col = func.coalesce(Card.color_identity_mask, 0)
    elif sort in {"core_role", "core"}:
        core_subq = (
            db.session.query(
                OracleCoreRoleTag.oracle_id.label("oracle_id"),
                func.min(func.lower(OracleCoreRoleTag.role)).label("core_role"),
            )
            .group_by(OracleCoreRoleTag.oracle_id)
            .subquery()
        )
        query = query.outerjoin(core_subq, core_subq.c.oracle_id == Card.oracle_id)
        order_col = func.coalesce(core_subq.c.core_role, "")
    elif sort in {"evergreen", "evergreen_tag"}:
        evergreen_subq = (
            db.session.query(
                OracleEvergreenTag.oracle_id.label("oracle_id"),
                func.min(func.lower(OracleEvergreenTag.keyword)).label("evergreen_tag"),
            )
            .group_by(OracleEvergreenTag.oracle_id)
            .subquery()
        )
        query = query.outerjoin(evergreen_subq, evergreen_subq.c.oracle_id == Card.oracle_id)
        order_col = func.coalesce(evergreen_subq.c.evergreen_tag, "")
    elif sort in {"price", "art"}:
        order_col = func.lower(Card.name)
    elif sort == "folder":
        query = query.outerjoin(Folder, Folder.id == Card.folder_id)
        order_col = func.lower(Folder.name)
    else:
        order_col = func.lower(Card.name)

    if reverse:
        query = query.order_by(order_col.desc(), func.lower(Card.name).asc())
    else:
        query = query.order_by(order_col.asc(), func.lower(Card.name).asc())

    # Pagination
    total = query.count()
    pages = max(1, ceil(total / per)) if per else 1
    page = min(page, pages)
    start = (page - 1) * per + 1 if total else 0
    end = min(start + per - 1, total) if total else 0
    card_columns = (
        Card.id,
        Card.name,
        Card.set_code,
        Card.collector_number,
        Card.oracle_id,
        Card.lang,
        Card.is_foil,
        Card.folder_id,
        Card.quantity,
        Card.type_line,
        Card.rarity,
        Card.colors,
        Card.color_identity,
        Card.color_identity_mask,
    )
    cards = (
        query.options(
            load_only(*card_columns),
            selectinload(Card.folder).load_only(Folder.id, Folder.name, Folder.category, Folder.is_proxy),
        )
        .limit(per)
        .offset((page - 1) * per)
        .all()
    )

    oracle_ids = {c.oracle_id for c in cards if c.oracle_id}
    core_role_map: dict[str, list[str]] = {}
    evergreen_map: dict[str, list[str]] = {}
    if oracle_ids:
        core_rows = (
            db.session.query(OracleCoreRoleTag.oracle_id, OracleCoreRoleTag.role)
            .filter(OracleCoreRoleTag.oracle_id.in_(oracle_ids))
            .order_by(OracleCoreRoleTag.role.asc())
            .all()
        )
        for oracle_id, role in core_rows:
            if not role:
                continue
            bucket = core_role_map.setdefault(oracle_id, [])
            if role not in bucket:
                bucket.append(role)
        evergreen_rows = (
            db.session.query(OracleEvergreenTag.oracle_id, OracleEvergreenTag.keyword)
            .filter(OracleEvergreenTag.oracle_id.in_(oracle_ids))
            .order_by(OracleEvergreenTag.keyword.asc())
            .all()
        )
        for oracle_id, keyword in evergreen_rows:
            if not keyword:
                continue
            bucket = evergreen_map.setdefault(oracle_id, [])
            if keyword not in bucket:
                bucket.append(keyword)

    # Build template display maps (use normalized fields; Scryfall only for art)
    if not sc.cache_ready():
        sc.ensure_cache_loaded()
    image_map, hover_map = {}, {}
    type_map = {}

    print_map = _bulk_print_lookup(cards)
    price_text_map: dict[int, str | None] = {}
    price_value_map: dict[int, float | None] = {}

    def _image_from_print(pr):
        if not pr:
            return None
        iu = pr.get("image_uris")
        if iu:
            return iu.get("small") or iu.get("normal") or iu.get("large")
        faces = pr.get("card_faces") or []
        if faces:
            iu = (faces[0] or {}).get("image_uris") or {}
            return iu.get("small") or iu.get("normal") or iu.get("large")
        return None

    def _price_to_float(value):
        if value in (None, "", 0, "0", "0.0", "0.00"):
            return None
        try:
            num = float(value)
        except (TypeError, ValueError):
            return None
        return num if num > 0 else None

    def _format_exact_price(prices: dict | None, is_foil: bool) -> str | None:
        if not prices:
            return None

        def _fmt(value, prefix):
            if value in (None, "", 0, "0", "0.0", "0.00"):
                return None
            try:
                num = float(value)
            except (TypeError, ValueError):
                return None
            if num <= 0:
                return None
            return f"{prefix}{num:,.2f}".replace(",", "")

        if is_foil:
            value = _fmt(prices.get("usd_foil"), "$") or _fmt(prices.get("usd"), "$") or _fmt(prices.get("usd_etched"), "$")
            if value:
                return value
            value = _fmt(prices.get("eur_foil"), "EUR ") or _fmt(prices.get("eur"), "EUR ")
            if value:
                return value
        else:
            value = _fmt(prices.get("usd"), "$") or _fmt(prices.get("usd_foil"), "$") or _fmt(prices.get("usd_etched"), "$")
            if value:
                return value
            value = _fmt(prices.get("eur"), "EUR ") or _fmt(prices.get("eur_foil"), "EUR ")
            if value:
                return value

        return _fmt(prices.get("tix"), "TIX ")

    def _price_value_from_prices(prices: dict | None, is_foil: bool) -> float | None:
        if not prices:
            return None
        keys = ("usd_foil", "usd", "usd_etched") if is_foil else ("usd", "usd_foil", "usd_etched")
        for key in keys:
            val = _price_to_float(prices.get(key))
            if val is not None:
                return val
        for key in ("eur", "eur_foil", "tix"):
            val = _price_to_float(prices.get(key))
            if val is not None:
                return val
        return None

    for c in cards:
        pr = print_map.get(c.id, {})
        img_package = sc.image_for_print(pr) if pr else {}
        thumb_src = img_package.get("small") or img_package.get("normal") or img_package.get("large")
        hover_src = img_package.get("large") or img_package.get("normal") or img_package.get("small")
        if not thumb_src:
            thumb_src = _image_from_print(pr)
        image_map[c.id] = thumb_src
        hover_map[c.id] = hover_src
        type_line = getattr(c, "type_line", None) or ""
        type_map[c.id] = [
            t
            for t in ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]
            if t.lower() in (type_line or "").lower()
        ]
        prices = _prices_for_print_exact(pr) if pr else {}
        price_text_map[c.id] = _format_exact_price(prices, bool(c.is_foil))
        price_value_map[c.id] = _price_value_from_prices(prices, bool(c.is_foil))

    def _rarity_badge_class(label: str | None) -> str | None:
        rl = (label or "").strip().lower()
        if rl == "common":
            return "secondary"
        if rl == "uncommon":
            return "success"
        if rl == "rare":
            return "warning"
        if rl in {"mythic", "mythic rare"}:
            return "danger"
        return None

    cards_vm: list[CardListItemVM] = []
    for c in cards:
        display_name = c.name
        type_line = getattr(c, "type_line", None) or ""
        type_badges = type_map.get(c.id) or []
        type_tokens = [t.lower() for t in type_badges] if type_badges else []
        if not type_tokens and type_line:
            raw_tokens = re.split(r"[\\s\\-/,]+", type_line)
            type_tokens = [tok.lower() for tok in raw_tokens if tok]
        core_roles_raw = core_role_map.get(c.oracle_id or "", []) if c.oracle_id else []
        core_roles_labels = [format_role_label(role) for role in core_roles_raw]
        core_display, core_overflow = slice_badges(core_roles_labels)
        evergreen_raw = evergreen_map.get(c.oracle_id or "", []) if c.oracle_id else []
        evergreen_labels = [format_role_label(tag) for tag in evergreen_raw]
        evergreen_display, evergreen_overflow = slice_badges(evergreen_labels)
        color_value = getattr(c, "color_identity", None) or getattr(c, "colors", None)
        if isinstance(color_value, (list, tuple, set)):
            color_letters = [str(x).upper() for x in color_value if str(x).upper()]
        else:
            color_letters = [ch for ch in str(color_value or "").upper() if ch in "WUBRG"]
        if not color_letters:
            color_letters = ["C"]
        rarity_label = (c.rarity or "").capitalize() or None
        folder_ref = None
        if getattr(c, "folder", None):
            folder_ref = FolderRefVM(id=c.folder.id, name=c.folder.name)
        cards_vm.append(
            CardListItemVM(
                id=c.id,
                name=c.name,
                display_name=display_name,
                quantity=int(c.quantity or 0) or 1,
                folder=folder_ref,
                set_code=c.set_code,
                collector_number=str(c.collector_number) if c.collector_number is not None else None,
                lang=c.lang,
                is_foil=bool(c.is_foil),
                image_small=image_map.get(c.id),
                image_large=hover_map.get(c.id),
                type_line=type_line,
                type_badges=type_badges,
                type_tokens=type_tokens,
                core_roles_display=core_display,
                core_roles_overflow=core_overflow,
                evergreen_display=evergreen_display,
                evergreen_overflow=evergreen_overflow,
                color_letters=color_letters,
                rarity_label=rarity_label,
                rarity_badge_class=_rarity_badge_class(rarity_label),
                price_text=price_text_map.get(c.id),
            )
        )

    if sort == "price":
        def _price_sort_key(card_vm: CardListItemVM):
            value = price_value_map.get(card_vm.id)
            missing = value is None
            name_key = (card_vm.display_name or card_vm.name or "").lower()
            if reverse:
                return (missing, -(value or 0.0), name_key)
            return (missing, value or 0.0, name_key)

        cards_vm.sort(key=_price_sort_key)
    elif sort == "art":
        def _art_sort_key(card_vm: CardListItemVM):
            missing = 1 if not card_vm.image_small else 0
            name_key = (card_vm.display_name or card_vm.name or "").lower()
            return (missing, name_key)

        cards_vm.sort(key=_art_sort_key, reverse=reverse)

    def _url_with(page_num: int):
        args = request.args.to_dict(flat=False)
        args["page"] = [str(page_num)]
        if "per" not in args and "per_page" not in args:
            args["per"] = [str(per)]
        return url_for("views.list_cards", **{k: v if len(v) > 1 else v[0] for k, v in args.items()})

    prev_url = _url_with(page - 1) if page > 1 else None
    next_url = _url_with(page + 1) if page < pages else None
    page_urls = [(n, _url_with(n)) for n in range(1, pages + 1)]
    page_url_map = {n: url for n, url in page_urls}

    sets, langs, _folders = _facets()
    set_options = _set_options_with_names(sets)
    rarity_options = _rarity_options()
    move_folder_options = _move_folder_choices()
    return render_template(
        "cards/cards.html",
        cards=cards_vm,
        total=total,
        page=page,
        per=per,
        pages=pages,
        prev_url=prev_url,
        next_url=next_url,
        page_urls=page_urls,
        page_url_map=page_url_map,
        start=start,
        end=end,
        q=q_text,
        folder_id=folder_arg,
        folder_is_proxy=folder_is_proxy,
        set_code=set_code,
        tribe=typal,
        foil_only=foil_only,
        rarity=rarity,
        role_list=role_list,
        subrole_list=subrole_list,
        selected_types=selected_types,
        selected_colors=selected_colors,
        color_mode=color_mode,
        type_mode=type_mode,
        collection_flag=collection_flag,
        sort=sort,
        direction=direction,
        sets=sets,
        langs=langs,
        set_options=set_options,
        rarity_options=rarity_options,
        role_query_text=role_query_text,
        per_page=per,
        is_deck_folder=is_deck_folder,
        collection_folders=collection_names,
        move_folder_options=move_folder_options,
    )


def shared_folders():
    shared_rows = (
        FolderShare.query.options(
            selectinload(FolderShare.folder).selectinload(Folder.owner_user),
        )
        .join(Folder, Folder.id == FolderShare.folder_id)
        .filter(FolderShare.shared_user_id == current_user.id)
        .order_by(func.lower(Folder.name))
        .all()
    )
    shared_with_me = []
    category_labels = {
        Folder.CATEGORY_DECK: "Deck",
        Folder.CATEGORY_COLLECTION: "Collection",
        Folder.CATEGORY_BUILD: "Build Queue",
    }
    for share in shared_rows:
        folder = share.folder
        owner_label = None
        if folder.owner_user:
            owner_label = folder.owner_user.username or folder.owner_user.email
        owner_label = owner_label or folder.owner
        folder_vm = FolderVM(
            id=folder.id,
            name=folder.name,
            category=folder.category,
            category_label=category_labels.get(folder.category or Folder.CATEGORY_DECK, "Deck"),
            owner=folder.owner,
            owner_label=owner_label,
            owner_user_id=folder.owner_user_id,
            is_collection=bool(folder.is_collection),
            is_deck=bool(folder.is_deck),
            is_build=bool(folder.is_build),
            is_proxy=bool(getattr(folder, "is_proxy", False)),
            is_public=bool(getattr(folder, "is_public", False)),
            deck_tag=folder.deck_tag,
            deck_tag_label=folder.deck_tag,
            commander_name=folder.commander_name,
            commander_oracle_id=folder.commander_oracle_id,
            commander_slot_count=len(folder.commander_name.split("//")) if folder.commander_name else 0,
        )
        shared_with_me.append(
            SharedFolderEntryVM(
                folder=folder_vm,
                owner_label=owner_label or "Unknown",
            )
        )
    shared_ids = {entry.folder.id for entry in shared_with_me if entry.folder}

    public_query = (
        Folder.query.options(selectinload(Folder.owner_user))
        .filter(Folder.is_public.is_(True))
        .order_by(func.lower(Folder.name))
        .all()
    )
    my_public = []
    other_public = []
    for folder in public_query:
        owner_label = None
        if folder.owner_user:
            owner_label = folder.owner_user.username or folder.owner_user.email
        owner_label = owner_label or folder.owner
        folder_vm = FolderVM(
            id=folder.id,
            name=folder.name,
            category=folder.category,
            category_label=category_labels.get(folder.category or Folder.CATEGORY_DECK, "Deck"),
            owner=folder.owner,
            owner_label=owner_label,
            owner_user_id=folder.owner_user_id,
            is_collection=bool(folder.is_collection),
            is_deck=bool(folder.is_deck),
            is_build=bool(folder.is_build),
            is_proxy=bool(getattr(folder, "is_proxy", False)),
            is_public=bool(getattr(folder, "is_public", False)),
            deck_tag=folder.deck_tag,
            deck_tag_label=folder.deck_tag,
            commander_name=folder.commander_name,
            commander_oracle_id=folder.commander_oracle_id,
            commander_slot_count=len(folder.commander_name.split("//")) if folder.commander_name else 0,
        )
        if folder.owner_user_id == current_user.id:
            my_public.append(folder_vm)
        elif folder.id not in shared_ids:
            other_public.append(folder_vm)

    return render_template(
        "cards/shared_folders.html",
        shared_with_me=shared_with_me,
        my_public_folders=my_public,
        other_public_folders=other_public,
    )


def bulk_move_cards():
    """Move multiple cards to another folder."""
    json_payload = request.get_json(silent=True) or {}
    wants_json = request.is_json or bool(json_payload) or "application/json" in (request.headers.get("Accept") or "")

    redirect_target = (
        request.form.get("redirect_to")
        or json_payload.get("redirect_to")
        or request.referrer
        or url_for("views.list_cards")
    )
    if redirect_target and not redirect_target.startswith("/"):
        redirect_target = url_for("views.list_cards")

    def _gather_raw_ids() -> list[str]:
        raw: list[str] = []

        def _extend(value):
            if value is None:
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _extend(item)
            else:
                raw.append(str(value))

        _extend(json_payload.get("card_ids") or json_payload.get("cardIds"))
        if not raw:
            _extend(request.form.getlist("card_ids"))
            _extend(request.form.getlist("card_ids[]"))
        if not raw:
            single = request.form.get("card_ids")
            if single:
                raw.append(single)
        return raw

    try:
        card_ids = parse_positive_int_list(_gather_raw_ids(), field="card id(s)")
    except ValidationError as exc:
        log_validation_error(exc, context="bulk_move_cards")
        message = "Invalid card id(s) supplied."
        if wants_json:
            return jsonify({"success": False, "message": message}), 400
        flash(message, "warning")
        return redirect(redirect_target)
    if not card_ids:
        if wants_json:
            return jsonify({"success": False, "message": "Select at least one card to move."}), 400
        flash("Select at least one card to move.", "warning")
        return redirect(redirect_target)

    target_raw = (
        json_payload.get("target_folder_id")
        or json_payload.get("targetFolderId")
        or request.form.get("target_folder_id")
    )
    try:
        target_id = parse_positive_int(target_raw, field="target folder id")
    except ValidationError as exc:
        log_validation_error(exc, context="bulk_move_cards")
        if wants_json:
            return jsonify({"success": False, "message": "Choose a destination folder."}), 400
        flash("Choose a destination folder.", "warning")
        return redirect(redirect_target)

    target_folder = db.session.get(Folder, target_id)
    if not target_folder:
        if wants_json:
            return jsonify({"success": False, "message": "Destination folder was not found."}), 404
        flash("Destination folder was not found.", "danger")
        return redirect(redirect_target)

    ensure_folder_access(target_folder, write=True)

    cards = Card.query.filter(Card.id.in_(card_ids)).all()
    if not cards:
        if wants_json:
            return jsonify({"success": False, "message": "No matching cards were found."}), 404
        flash("No matching cards were found.", "warning")
        return redirect(redirect_target)

    single_qty = None
    raw_qty = json_payload.get("quantity") or request.form.get("quantity")
    if len(card_ids) == 1 and raw_qty is not None:
        try:
            single_qty = max(int(raw_qty), 1)
        except (TypeError, ValueError):
            single_qty = None

    moved = 0
    merged = 0
    skipped = 0
    for card in cards:
        ensure_folder_access(card.folder, write=True)
        if card.folder_id == target_folder.id:
            skipped += 1
            continue

        move_qty = single_qty if single_qty is not None else card.quantity or 0
        if move_qty <= 0:
            skipped += 1
            continue
        if move_qty > (card.quantity or 0):
            move_qty = card.quantity or move_qty

        existing = (
            Card.query.filter(
                Card.folder_id == target_folder.id,
                Card.name == card.name,
                Card.set_code == card.set_code,
                Card.collector_number == card.collector_number,
                Card.lang == card.lang,
                Card.is_foil == card.is_foil,
            )
            .order_by(Card.id.asc())
            .first()
        )

        remaining = (card.quantity or 0) - move_qty
        if remaining <= 0:
            if existing:
                existing.quantity = (existing.quantity or 0) + move_qty
                merged += move_qty
                db.session.delete(card)
            else:
                card.folder_id = target_folder.id
                moved += move_qty
        else:
            card.quantity = remaining
            if existing:
                existing.quantity = (existing.quantity or 0) + move_qty
                merged += move_qty
            else:
                clone = Card(
                    name=card.name,
                    set_code=card.set_code,
                    collector_number=card.collector_number,
                    folder_id=target_folder.id,
                    quantity=move_qty,
                    oracle_id=card.oracle_id,
                    lang=card.lang,
                    is_foil=card.is_foil,
                    type_line=card.type_line,
                    rarity=card.rarity,
                    oracle_text=card.oracle_text,
                    mana_value=card.mana_value,
                    colors=card.colors,
                    color_identity=card.color_identity,
                    color_identity_mask=card.color_identity_mask,
                    layout=card.layout,
                    faces_json=card.faces_json,
                )
                db.session.add(clone)
                moved += move_qty

    total_changed = (moved or 0) + (merged or 0)
    if total_changed:
        _safe_commit()
        record_audit_event(
            "cards_bulk_move",
            {"target_folder": target_folder.id, "moved_qty": moved, "merged_qty": merged, "card_ids": card_ids[:50]},
        )
        folder_name = target_folder.name or f"Folder {target_folder.id}"
        message = f"Moved {total_changed} card{'s' if total_changed != 1 else ''} to {folder_name}."
        if wants_json:
            return jsonify({"success": True, "message": message, "moved": moved, "merged": merged})
        flash(message, "success")
    else:
        info_msg = "Selected cards are already in that folder."
        if wants_json:
            return jsonify({"success": False, "message": info_msg, "skipped": skipped}), 200
        flash(info_msg, "info")

    return redirect(redirect_target)


def bulk_delete_cards(folder_id: int):
    """Delete one or more cards from a folder (deck or collection)."""
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)

    json_payload = request.get_json(silent=True) or {}
    wants_json = request.is_json or bool(json_payload) or "application/json" in (request.headers.get("Accept") or "")

    redirect_target = (
        request.form.get("redirect_to")
        or request.referrer
        or url_for("views.folder_detail", folder_id=folder_id)
    )

    def _gather_raw_ids() -> list[str]:
        raw: list[str] = []

        def _extend(value):
            if value is None:
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _extend(item)
            else:
                raw.append(str(value))

        _extend(json_payload.get("card_ids") or json_payload.get("cardIds"))
        if not raw:
            _extend(request.form.getlist("card_ids"))
            _extend(request.form.getlist("card_ids[]"))
        if not raw:
            single = request.form.get("card_id")
            if single:
                raw.append(single)
        return raw

    try:
        card_ids = parse_positive_int_list(_gather_raw_ids(), field="card id(s)")
    except ValidationError as exc:
        log_validation_error(exc, context="bulk_delete_cards")
        message = "Invalid card id(s) supplied."
        if wants_json:
            return jsonify({"success": False, "message": message}), 400
        flash(message, "warning")
        return redirect(redirect_target)
    if not card_ids:
        message = "Select at least one card to delete."
        if wants_json:
            return jsonify({"success": False, "message": message}), 400
        flash(message, "warning")
        return redirect(redirect_target)

    cards = (
        Card.query.filter(Card.id.in_(card_ids), Card.folder_id == folder.id)
        .order_by(Card.id.asc())
        .all()
    )
    if not cards:
        message = "No matching cards were found in this folder."
        if wants_json:
            return jsonify({"success": False, "message": message}), 404
        flash(message, "warning")
        return redirect(redirect_target)

    deleted_qty = 0
    for card in cards:
        deleted_qty += card.quantity or 0
        db.session.delete(card)

    _safe_commit()
    record_audit_event(
        "cards_bulk_delete",
        {"folder": folder.id, "card_ids": card_ids[:50], "qty": deleted_qty},
    )

    message = f"Deleted {len(cards)} card{'s' if len(cards) != 1 else ''} ({deleted_qty} cop{'ies' if deleted_qty != 1 else 'y'})."
    if wants_json:
        return jsonify({"success": True, "message": message, "deleted": len(cards), "deleted_qty": deleted_qty})

    flash(message, "success")
    return redirect(redirect_target)


def api_card_printing_options(card_id: int):
    """Return cached printings for a card so the UI can populate dropdowns."""
    card = get_or_404(Card, card_id)
    ensure_folder_access(card.folder, write=True)
    _ensure_cache_ready()

    oracle_id = card.oracle_id
    if not oracle_id:
        try:
            oracle_id = unique_oracle_by_name(card.name)
        except Exception:
            oracle_id = None

    prints: list[dict] = []
    if oracle_id:
        try:
            prints = list(prints_for_oracle(oracle_id) or [])
        except Exception:
            prints = []
    if not prints:
        try:
            pr = find_by_set_cn(card.set_code, card.collector_number, card.name)
            if pr:
                prints = [pr]
                oracle_id = oracle_id or pr.get("oracle_id")
        except Exception:
            prints = []

    current_value = f"{(card.set_code or '').upper()}::{card.collector_number or ''}::{(card.lang or 'en').upper()}"
    options: list[dict] = []
    seen_values: set[str] = set()
    for pr in prints:
        set_code = (pr.get("set") or "").upper()
        cn = str(pr.get("collector_number") or "")
        lang = (pr.get("lang") or "en").upper()
        value = f"{set_code}::{cn}::{lang}"
        if value in seen_values:
            continue
        seen_values.add(value)
        imgs = _image_from_print(pr)
        options.append(
            {
                "value": value,
                "set": set_code,
                "set_name": pr.get("set_name") or (set_name_for_code(set_code.lower()) if set_code else ""),
                "collector_number": cn,
                "lang": lang,
                "finishes": pr.get("finishes") or [],
                "promo_types": pr.get("promo_types") or [],
                "oracle_id": pr.get("oracle_id") or oracle_id,
                "image": imgs.get("normal") or imgs.get("large") or imgs.get("small"),
            }
        )

    if not options:
        options.append(
            {
                "value": current_value,
                "set": (card.set_code or "").upper(),
                "set_name": set_name_for_code((card.set_code or "").lower()) if card.set_code else "",
                "collector_number": card.collector_number or "",
                "lang": (card.lang or "en").upper(),
                "finishes": ["foil" if card.is_foil else "nonfoil"],
                "promo_types": [],
                "oracle_id": oracle_id or card.oracle_id,
                "image": None,
            }
        )

    current_finish = "foil" if card.is_foil else "nonfoil"
    current_finishes: list[str] = []
    for opt in options:
        if opt["value"] == current_value:
            current_finishes = opt.get("finishes") or []
            break
    if not current_finishes and options:
        current_finishes = options[0].get("finishes") or []

    return jsonify(
        {
            "options": options,
            "current": current_value,
            "finishes": current_finishes,
            "current_finish": current_finish,
        }
    )


def api_update_card_printing(card_id: int):
    """Change a card's printing (set/collector/lang/finish), merging quantities when needed."""
    card = get_or_404(Card, card_id)
    ensure_folder_access(card.folder, write=True)

    payload = request.get_json(silent=True) or {}
    printing_raw = (payload.get("printing") or payload.get("printing_value") or request.form.get("printing") or "").strip()
    finish_raw = (payload.get("finish") or request.form.get("finish") or "").strip().lower()
    qty_raw = payload.get("quantity") or request.form.get("quantity")

    if not printing_raw or "::" not in printing_raw:
        return jsonify({"success": False, "message": "Choose a printing to update."}), 400

    parts = printing_raw.split("::")
    while len(parts) < 3:
        parts.append("")
    set_code, collector_number, lang = parts[0].strip(), parts[1].strip(), (parts[2] or "en").strip()

    try:
        target_qty = int(qty_raw)
    except (TypeError, ValueError):
        target_qty = 1
    target_qty = max(1, min(target_qty, card.quantity or 1))

    _ensure_cache_ready()
    pr = None
    oracle_id = card.oracle_id
    if not oracle_id:
        try:
            oracle_id = unique_oracle_by_name(card.name)
        except Exception:
            oracle_id = None

    try:
        if oracle_id:
            for candidate in prints_for_oracle(oracle_id) or []:
                matches_set = (candidate.get("set") or "").lower() == set_code.lower()
                matches_cn = str(candidate.get("collector_number") or "").lower() == str(collector_number).lower()
                matches_lang = (candidate.get("lang") or "en").lower() == lang.lower()
                if matches_set and matches_cn and matches_lang:
                    pr = candidate
                    break
    except Exception:
        pr = None

    if pr is None:
        try:
            pr = find_by_set_cn(set_code, collector_number, card.name)
        except Exception:
            pr = None

    metadata = metadata_from_print(pr) if pr else {}
    new_name = (pr or {}).get("name") or card.name
    new_oracle = (pr or {}).get("oracle_id") or oracle_id or card.oracle_id
    new_type_line = metadata.get("type_line") or card.type_line
    new_rarity = metadata.get("rarity") or card.rarity
    finish_flag = finish_raw or ("foil" if card.is_foil else "nonfoil")
    is_foil = finish_flag in {"foil", "etched", "glossy", "gilded"}

    lang = (lang or "en").lower()
    set_code = (set_code or "").upper()
    collector_number = str(collector_number or "")

    merge_target = (
        Card.query.filter(
            Card.id != card.id,
            Card.folder_id == card.folder_id,
            func.lower(Card.name) == func.lower(new_name or card.name),
            Card.set_code == set_code,
            Card.collector_number == collector_number,
            Card.lang == lang,
            Card.is_foil == is_foil,
        )
        .order_by(Card.id.asc())
        .first()
    )

    remaining = (card.quantity or 0) - target_qty
    if remaining <= 0:
        if merge_target:
            merge_target.quantity = (merge_target.quantity or 0) + target_qty
            db.session.delete(card)
        else:
            card.name = new_name
            card.set_oracle_id(new_oracle)
            card.set_code = set_code
            card.collector_number = collector_number
            card.lang = lang
            card.is_foil = is_foil
            card.type_line = new_type_line
            card.rarity = new_rarity
            card.oracle_text = metadata.get("oracle_text") or card.oracle_text
            card.mana_value = metadata.get("mana_value") if metadata.get("mana_value") is not None else card.mana_value
            card.colors = metadata.get("colors") or card.colors
            card.color_identity = metadata.get("color_identity") or card.color_identity
            card.color_identity_mask = metadata.get("color_identity_mask") or card.color_identity_mask
            card.layout = metadata.get("layout") or card.layout
            if metadata.get("faces_json") is not None:
                card.faces_json = metadata.get("faces_json")
    else:
        card.quantity = remaining
        if merge_target:
            merge_target.quantity = (merge_target.quantity or 0) + target_qty
        else:
            updated = Card(
                name=new_name,
                set_code=set_code,
                collector_number=collector_number,
                folder_id=card.folder_id,
                quantity=target_qty,
                oracle_id=new_oracle,
                lang=lang,
                is_foil=is_foil,
                type_line=new_type_line,
                rarity=new_rarity,
                oracle_text=metadata.get("oracle_text"),
                mana_value=metadata.get("mana_value"),
                colors=metadata.get("colors"),
                color_identity=metadata.get("color_identity"),
                color_identity_mask=metadata.get("color_identity_mask") or card.color_identity_mask,
                layout=metadata.get("layout"),
                faces_json=metadata.get("faces_json"),
            )
            db.session.add(updated)

    _safe_commit()
    record_audit_event(
        "card_update_printing",
        {"card_id": card_id, "target": printing_raw, "qty": target_qty, "finish": finish_flag},
    )
    return jsonify({"success": True, "message": "Printing updated."})


def collection_overview():
    """Overview of collection buckets, with cached stats and simple visuals."""
    collection_rows = _collection_rows_with_fallback()
    folder_ids = [fid for fid, _ in collection_rows if fid is not None]

    if folder_ids:
        folders = Folder.query.filter(Folder.id.in_(folder_ids)).order_by(func.lower(Folder.name)).all()
    else:
        folders = []

    folder_by_id = {f.id: f for f in folders}
    buckets: list[CollectionBucketVM] = []
    for fid, name in collection_rows:
        folder = folder_by_id.get(fid)
        label = folder.name if folder else (name or "Collection")
        folder_option = FolderOptionVM(id=folder.id, name=folder.name) if folder else None
        buckets.append(CollectionBucketVM(label=label, folder=folder_option, rows=0, qty=0))

    filters = {}
    if request.args.get("lang"):
        filters["lang"] = request.args["lang"]
    foil_collection_arg = (request.args.get("foil_only") or "").strip().lower()
    if foil_collection_arg in {"1", "true", "yes", "on"}:
        filters["foil"] = True
    elif request.args.get("foil") in ("0", "1"):
        filters["foil"] = request.args.get("foil") == "1"
    if folder_ids:
        filters["folder_ids"] = folder_ids

    if folder_ids:
        stats_list = get_folder_stats(filters)
        stats_by_id = {s["folder_id"]: {"rows": s["rows"], "qty": s["qty"]} for s in stats_list}
        total_rows = sum(s["rows"] for s in stats_list)
        total_qty = sum(s["qty"] for s in stats_list)

        by_set = (
            db.session.query(Card.set_code, func.coalesce(func.sum(Card.quantity), 0).label("qty"))
            .filter(Card.folder_id.in_(folder_ids))
            .group_by(Card.set_code)
            .order_by(func.coalesce(func.sum(Card.quantity), 0).desc())
            .limit(10)
            .all()
        )
    else:
        total_rows = 0
        total_qty = 0
        stats_by_id = {}
        by_set = []

    for item in buckets:
        folder = item.folder
        if folder:
            stats = stats_by_id.get(folder.id, {"rows": 0, "qty": 0})
            item.rows = stats["rows"]
            item.qty = stats["qty"]
        else:
            item.rows = 0
            item.qty = 0

    have_cache = _ensure_cache_ready()
    sets_with_names = [
        (scd or "", (set_name_for_code(scd) if have_cache else None), int(qty)) for scd, qty in by_set if scd
    ]

    base_types = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]
    type_counts = {t: 0 for t in base_types}

    if folder_ids and have_cache:
        rows = (
            db.session.query(
                Card.name,
                Card.set_code,
                Card.collector_number,
                Card.oracle_id,
                func.coalesce(Card.quantity, 0).label("qty"),
            )
            .filter(Card.folder_id.in_(folder_ids))
            .all()
        )

        type_line_cache = {}
        for name, scode, cn, oid, qty in rows:
            qty = int(qty or 0) or 1
            key = (
                f"oid:{oid}"
                if oid
                else f"{(scode or '').lower()}:{(str(cn) or '').lower()}:{(name or '').lower()}"
            )
            if key in type_line_cache:
                tline = type_line_cache[key]
            else:
                p = None
                try:
                    p = find_by_set_cn(scode, cn, name)
                except Exception:
                    p = None
                if not p and oid:
                    try:
                        prs = prints_for_oracle(oid) or []
                        if prs:
                            p = prs[0]
                    except Exception:
                        p = None
                tline = (p or {}).get("type_line")
                type_line_cache[key] = tline

            for t in [t for t in base_types if t in (tline or "")]:
                type_counts[t] += qty

    type_breakdown = [(t, type_counts.get(t, 0)) for t in base_types if type_counts.get(t, 0) > 0]
    type_icon_classes = {
        "Artifact": "bi-cpu",
        "Battle": "bi-shield-check",
        "Creature": "bi-people",
        "Enchantment": "bi-stars",
        "Instant": "bi-lightning",
        "Land": "bi-geo-alt",
        "Planeswalker": "bi-compass",
        "Sorcery": "bi-fire",
    }
    type_breakdown_vms = [
        TypeBreakdownVM(
            label=label,
            count=int(count or 0),
            icon_class=type_icon_classes.get(label),
            icon_letter=label[0] if label else None,
            url=url_for("views.list_cards", type=label.lower(), collection=1),
        )
        for label, count in type_breakdown
        if count
    ]

    collection_names_for_template = [item.label for item in buckets]

    return render_template(
        "cards/collection.html",
        buckets=buckets,
        total_rows=total_rows,
        total_qty=total_qty,
        sets_with_names=sets_with_names,
        type_breakdown=type_breakdown_vms,
        collection_folders=collection_names_for_template,
    )


def api_deck_insight(deck_id: int):
    folder = get_or_404(Folder, deck_id)
    payload = _deck_drawer_summary(folder)
    return jsonify(payload)


def decks_overview():
    """Render the deck gallery with commander thumbnails and color identity badges."""
    sort = (request.args.get("sort") or "").strip().lower()
    direction = (request.args.get("dir") or "").strip().lower() or "desc"
    reverse = direction == "desc"

    deck_query = (
        db.session.query(
            Folder.id,
            Folder.name,
            func.count(Card.id).label("row_count"),
            func.coalesce(func.sum(Card.quantity), 0).label("qty_sum"),
            Folder.commander_oracle_id,
            Folder.commander_name,
            Folder.owner,
            Folder.is_proxy,
        )
        .outerjoin(Card, Card.folder_id == Folder.id)
        .filter(Folder.role_entries.any(FolderRole.role == FolderRole.ROLE_DECK))
    )
    rows = (
        deck_query.group_by(
            Folder.id,
            Folder.name,
            Folder.commander_oracle_id,
            Folder.commander_name,
            Folder.owner,
            Folder.is_proxy,
        )
        .order_by(func.coalesce(func.sum(Card.quantity), 0).desc(), Folder.name.asc())
        .all()
    )

    # normalize for the template
    decks = []
    for fid, name, _rows, qty, cmd_oid, cmd_name, owner, is_proxy in rows:
        decks.append({
            "id": fid,
            "name": name,
            "qty": int(qty or 0),
            "commander_oid": cmd_oid,
            "commander_name": cmd_name,
            "owner": owner,
            "is_proxy": bool(is_proxy),
            "bracket": {},
            "tag": None,
            "tag_label": None,
        })
    deck_ids = [d["id"] for d in decks]

    # load folders so we can see commander fields
    folders = Folder.query.filter(Folder.id.in_(deck_ids)).all()
    folder_map = {f.id: f for f in folders}

    # attach tag info
    for deck in decks:
        f = folder_map.get(deck["id"])
        if not f:
            continue
        tag = f.deck_tag
        deck["tag"] = tag
        deck["tag_label"] = tag or None

    deck_bracket_map: dict[int, dict] = {}
    if deck_ids:
        _ensure_cache_ready()
        epoch = cache_epoch() + BRACKET_RULESET_EPOCH + spellbook_dataset_epoch()

        def _joined_oracle_text(pr: dict | None) -> str:
            if not pr:
                return ""
            parts = []
            txt = pr.get("oracle_text")
            if txt:
                parts.append(txt)
            for face in pr.get("card_faces") or []:
                ft = (face or {}).get("oracle_text")
                if ft:
                    parts.append(ft)
            return " // ".join(p for p in parts if p)

        oracle_cache: dict[str, dict | None] = {}
        scn_cache: dict[tuple[str, str], dict | None] = {}

        deck_cards_map: dict[int, list[dict]] = {}
        card_rows = Card.query.filter(Card.folder_id.in_(deck_ids)).all()
        for card_row in card_rows:
            fid = card_row.folder_id
            qty = int(getattr(card_row, "quantity", 0) or 0) or 1

            pr = None
            oid = getattr(card_row, "oracle_id", None)
            if oid:
                if oid in oracle_cache:
                    pr = oracle_cache[oid]
                else:
                    try:
                        prints = prints_for_oracle(oid) or []
                        pr = prints[0] if prints else None
                    except Exception:
                        pr = None
                    oracle_cache[oid] = pr
            if pr is None:
                key = (card_row.set_code or "", str(card_row.collector_number or ""))
                if key in scn_cache:
                    cached = scn_cache[key]
                else:
                    try:
                        cached = find_by_set_cn(card_row.set_code, card_row.collector_number, card_row.name)
                    except Exception:
                        cached = None
                    scn_cache[key] = cached
                pr = cached

            if pr:
                payload = {
                    "name": sc.display_name_for_print(pr) if hasattr(sc, "display_name_for_print") else pr.get("name") or card_row.name,
                    "type_line": sc.type_label_for_print(pr) if hasattr(sc, "type_label_for_print") else pr.get("type_line") or "",
                    "oracle_text": _joined_oracle_text(pr),
                    "mana_cost": pr.get("mana_cost"),
                    "mana_value": pr.get("cmc"),
                    "produced_mana": pr.get("produced_mana"),
                    "quantity": qty,
                    "game_changer": bool(pr.get("game_changer")),
                }
            else:
                payload = {
                    "name": card_row.name,
                    "type_line": getattr(card_row, "type_line", "") or "",
                    "oracle_text": getattr(card_row, "oracle_text", "") or "",
                    "mana_cost": getattr(card_row, "mana_cost", None),
                    "mana_value": getattr(card_row, "mana_value", None),
                    "produced_mana": getattr(card_row, "produced_mana", None),
                    "quantity": qty,
                    "game_changer": bool(getattr(card_row, "game_changer", False)),
                }

            deck_cards_map.setdefault(fid, []).append(payload)

        for deck in decks:
            fid = deck["id"]
            folder = folder_map.get(fid)
            if not folder:
                continue
            cards_payload = deck_cards_map.get(fid, [])
            commander_stub = {
                "oracle_id": primary_commander_oracle_id(folder.commander_oracle_id),
                "name": primary_commander_name(folder.commander_name) or folder.commander_name,
            }
            ctx = None
            signature = None
            if fid:
                signature = compute_bracket_signature(cards_payload, commander_stub, epoch=epoch)
                ctx = get_cached_bracket(fid, signature, epoch)
            if not ctx:
                ctx = evaluate_commander_bracket(cards_payload, commander_stub)
                if fid and signature:
                    store_cached_bracket(fid, signature, epoch, ctx)
            deck_bracket_map[fid] = ctx
            deck["bracket"] = ctx

    # same symbols pipeline as elsewhere
    ensure_symbols_cache(force=False)
    if not sc.cache_ready():
        sc.ensure_cache_loaded()
    thumbnail_epoch = cache_epoch()
    deck_ci_letters = {}
    deck_ci_name = {}
    deck_ci_html = {}
    deck_cmdr = {}
    placeholder_thumb = url_for("static", filename="img/card-placeholder.svg")

    for (fid, _name, _rows, _qty, cmd_oid, cmd_name, _owner, _is_proxy) in rows:
        # -- color identity for the deck
        letters, label = compute_folder_color_identity(fid)
        letters = letters or ["C"]
        letters_str = "".join(ch for ch in "WUBRG" if ch in set(letters)) or "C"
        deck_ci_letters[fid] = letters_str
        deck_ci_name[fid] = label or color_identity_name(letters)
        mana_str = "".join(f"{{{ch}}}" for ch in (letters_str if letters_str else "C"))
        deck_ci_html[fid] = render_mana_html(mana_str, use_local=False)

        # —— commander thumbnail: prefer the exact owned printing in this deck
        f = folder_map.get(fid)
        cmd_card = None
        try:
            oracle_ids = [
                (oid or "").strip().lower()
                for oid in split_commander_oracle_ids(f.commander_oracle_id) if (oid or "").strip()
            ] if f else []
            if oracle_ids:
                cmd_card = (
                    Card.query.filter(
                        Card.folder_id == fid,
                        Card.oracle_id.isnot(None),
                        func.lower(Card.oracle_id).in_(oracle_ids),
                    )
                    .order_by(Card.quantity.desc(), Card.id.asc())
                    .first()
                )
            if not cmd_card and f and f.commander_name:
                name_candidates = [n.strip().lower() for n in split_commander_names(f.commander_name) if n.strip()]
                if name_candidates:
                    cmd_card = (
                        Card.query.filter(
                            Card.folder_id == fid,
                            func.lower(Card.name).in_(name_candidates),
                        )
                        .order_by(Card.quantity.desc(), Card.id.asc())
                        .first()
                    )
        except Exception:
            cmd_card = None

        def _img_from_print(pr: dict | None) -> tuple[str | None, str | None]:
            if not pr:
                return None, None
            iu = (pr or {}).get("image_uris") or {}
            if iu:
                return (
                    iu.get("small") or iu.get("normal") or iu.get("large") or iu.get("png"),
                    iu.get("png") or iu.get("large") or iu.get("normal") or iu.get("small"),
                )
            faces = (pr or {}).get("card_faces") or []
            if faces:
                fiu = (faces[0] or {}).get("image_uris") or {}
                return (
                    fiu.get("small") or fiu.get("normal") or fiu.get("large") or fiu.get("png"),
                    fiu.get("png") or fiu.get("large") or fiu.get("normal") or fiu.get("small"),
                )
            return None, None

        images = []
        pr = None
        final_name = cmd_name
        if cmd_card:
            final_name = final_name or getattr(f, "commander_name", None) or cmd_card.name
            try:
                pr = find_by_set_cn(cmd_card.set_code, cmd_card.collector_number, cmd_card.name)
            except Exception:
                pr = None
            if not pr:
                pr = _lookup_print_data(cmd_card.set_code, cmd_card.collector_number, cmd_card.name, cmd_card.oracle_id)
            if pr:
                small, large = _img_from_print(pr)
                images.append({
                    "name": final_name,
                    "small": small or placeholder_thumb,
                    "large": large or small or placeholder_thumb,
                    "alt": (final_name or "Commander"),
                })

        # Add additional commander faces from oracle list (partners, backgrounds)
        if f:
            try:
                oracle_ids = [
                    (oid or "").strip().lower()
                    for oid in split_commander_oracle_ids(f.commander_oracle_id) if (oid or "").strip()
                ]
            except Exception:
                oracle_ids = []
            for oid in oracle_ids:
                if cmd_card and cmd_card.oracle_id and cmd_card.oracle_id.lower() == oid:
                    continue
                try:
                    prints = prints_for_oracle(oid) or []
                except Exception:
                    prints = []
                if not prints:
                    continue
                small, large = _img_from_print(prints[0])
                images.append({
                    "name": final_name or prints[0].get("name"),
                    "small": small or placeholder_thumb,
                    "large": large or small or placeholder_thumb,
                    "alt": (final_name or prints[0].get("name") or "Commander"),
                })

        if not images:
            target_oid = primary_commander_oracle_id(cmd_oid) if cmd_oid else None
            if not target_oid and f:
                target_oid = primary_commander_oracle_id(f.commander_oracle_id)
            thumb_payload = _commander_thumbnail_payload(
                fid,
                target_oid,
                cmd_name,
                int(_rows or 0),
                int(_qty or 0),
                thumbnail_epoch,
            )
            final_name = thumb_payload.get("name") or cmd_name
            images.append({
                "name": final_name,
                "small": thumb_payload.get("small") or placeholder_thumb,
                "large": thumb_payload.get("large") or placeholder_thumb,
                "alt": thumb_payload.get("alt") or (final_name or "Commander"),
            })

        primary = images[0] if images else None
        if primary:
            payload = dict(primary)
            payload["images"] = images
            deck_cmdr[fid] = payload

    # ---- optional sorting ----
    if sort in {"name", "ci", "pips", "qty", "bracket", "owner"}:
        if sort == "name":
            decks.sort(key=lambda d: (d.get("name") or "").lower(), reverse=reverse)
        elif sort == "ci":
            decks.sort(key=lambda d: (deck_ci_name.get(d["id"]) or "Colorless"), reverse=reverse)
        elif sort == "pips":
            decks.sort(key=lambda d: (deck_ci_letters.get(d["id"]) or "C"), reverse=reverse)
        elif sort == "qty":
            decks.sort(key=lambda d: (d.get("qty") or 0), reverse=reverse)
        elif sort == "bracket":
            decks.sort(
                key=lambda d: (
                    deck_bracket_map.get(d["id"], {}).get("level") or 0,
                    d.get("name") or "",
                ),
                reverse=reverse,
            )
        elif sort == "owner":
            decks.sort(
                key=lambda d: (
                    (d.get("owner") or "").lower(),
                    d.get("name") or "",
                ),
                reverse=reverse,
            )

    owner_summary_raw = _owner_summary(decks)
    owner_summary = [
        DeckOwnerSummaryVM(
            owner=item.get("owner"),
            label=item.get("label") or "Unassigned",
            deck_count=int(item.get("deck_count") or 0),
            card_total=int(item.get("card_total") or 0),
            proxy_count=int(item.get("proxy_count") or 0),
        )
        for item in owner_summary_raw
    ]

    deck_vms: list[DeckVM] = []
    for deck in decks:
        fid = deck.get("id")
        cmd_payload = deck_cmdr.get(fid)
        cmd_vm = None
        if cmd_payload:
            images = [
                ImageSetVM(
                    small=img.get("small"),
                    normal=img.get("normal"),
                    large=img.get("large"),
                    label=img.get("label"),
                )
                for img in cmd_payload.get("images", [])
            ]
            cmd_vm = DeckCommanderVM(
                name=cmd_payload.get("name"),
                small=cmd_payload.get("small"),
                large=cmd_payload.get("large"),
                alt=cmd_payload.get("alt"),
                images=images,
            )
        bracket = deck.get("bracket") or {}
        deck_vms.append(
            DeckVM(
                id=fid,
                name=deck.get("name") or "",
                qty=int(deck.get("qty") or 0),
                owner=deck.get("owner"),
                owner_key=(deck.get("owner") or "").strip().lower(),
                is_proxy=bool(deck.get("is_proxy")),
                tag=deck.get("tag"),
                tag_label=deck.get("tag_label"),
                ci_name=deck_ci_name.get(fid) or "Colorless",
                ci_html=deck_ci_html.get(fid) or "",
                ci_letters=deck_ci_letters.get(fid) or "C",
                commander=cmd_vm,
                bracket_level=str(bracket.get("level")) if bracket.get("level") is not None else None,
                bracket_label=bracket.get("label"),
            )
        )

    return render_template(
        "decks/decks.html",
        decks=deck_vms,
        owner_summary=owner_summary,
        owner_names=_owner_names(decks),
        proxy_count=sum(1 for deck in decks if deck.get("is_proxy")),
        deck_tag_groups=get_deck_tag_groups(),
    )


def deck_from_collection():
    form = {
        "deck_name": (request.form.get("deck_name") or "").strip(),
        "commander": (request.form.get("commander") or "").strip(),
        "deck_tag": (request.form.get("deck_tag") or "").strip(),
        "deck_lines": request.form.get("deck_lines") or "",
    }

    def _fmt_entry(entry: dict) -> str:
        card = entry.get("card")
        set_code = entry.get("set_code") or (card.set_code if card else None) or "?"
        cn = entry.get("collector_number") or (card.collector_number if card else None) or "?"
        set_part = set_code.upper() if isinstance(set_code, str) else str(set_code)
        cn_part = cn
        return f"{entry['qty']}x {entry['name']} [{set_part} {cn_part}]"

    stage = request.form.get("stage") or "input"
    warnings: list[str] = []
    errors: list[str] = []
    infos: list[str] = []
    conflicts: list[dict] = []
    summary: dict | None = None

    if request.method == "POST":
        entries, parse_errors = _parse_collection_lines(form["deck_lines"])
        if parse_errors:
            errors.extend(parse_errors)
            return render_template(
                "decks/deck_from_collection.html",
                form=form,
                errors=errors,
                warnings=warnings,
                infos=infos,
                conflicts=conflicts,
                summary=summary,
                deck_tag_groups=get_deck_tag_groups(),
                stage="input",
            )
        if not form["deck_name"]:
            errors.append("Deck name is required.")
        if errors:
            return render_template(
                "decks/deck_from_collection.html",
                form=form,
                errors=errors,
                warnings=warnings,
                infos=infos,
                conflicts=conflicts,
                summary=summary,
                deck_tag_groups=get_deck_tag_groups(),
                stage="input",
            )

        resolved_entries: list[dict] = []
        total_requested = sum(e["qty"] for e in entries)
        resolved_count = 0
        resolve_needed = False

        for entry in entries:
            needs_choice = not entry["set_code"] or not entry["collector_number"]
            resolve_choice = request.form.get(f"resolve_{entry['index']}")
            base_query = (
                Card.query.join(Folder).join(FolderRole, FolderRole.folder_id == Folder.id)
                .filter(
                    func.lower(Card.name) == entry["name"].strip().lower(),
                    FolderRole.role == FolderRole.ROLE_COLLECTION,
                    Folder.owner_user_id == current_user.id,
                )
            )
            if entry["set_code"]:
                base_query = base_query.filter(func.lower(Card.set_code) == entry["set_code"])
            if entry["collector_number"]:
                base_query = base_query.filter(
                    func.lower(Card.collector_number) == entry["collector_number"].lower()
                )
            candidates = base_query.all()

            if not candidates:
                warnings.append(f"Line {entry['index']}: {_fmt_entry(entry)} not found in your collection.")
                resolved_entries.append({**entry, "card": None})
                continue

            if resolve_choice:
                chosen = next((c for c in candidates if str(c.id) == str(resolve_choice)), None)
                if not chosen:
                    errors.append(
                        f"Line {entry['index']}: Selected printing not found. Please choose again."
                    )
                    resolve_needed = True
                    conflicts.append(
                        {
                            "index": entry["index"],
                            "display": _fmt_entry(entry),
                            "options": [
                                {
                                    "id": c.id,
                                    "name": c.name,
                                    "set_code": c.set_code,
                                    "collector_number": c.collector_number,
                                    "quantity": c.quantity or 0,
                                    "lang": c.lang or "en",
                                    "is_foil": bool(c.is_foil),
                                    "folder": c.folder.name if c.folder else None,
                                }
                                for c in candidates
                            ],
                            "selected": resolve_choice,
                        }
                    )
                    continue
                resolved_entries.append({**entry, "card": chosen})
                resolved_count += 1
                continue

            if len(candidates) == 1:
                if needs_choice:
                    resolve_needed = True
                    conflicts.append(
                        {
                            "index": entry["index"],
                            "display": _fmt_entry(entry),
                            "options": [
                                {
                                    "id": c.id,
                                    "name": c.name,
                                    "set_code": c.set_code,
                                    "collector_number": c.collector_number,
                                    "quantity": c.quantity or 0,
                                    "lang": c.lang or "en",
                                    "is_foil": bool(c.is_foil),
                                    "folder": c.folder.name if c.folder else None,
                                }
                                for c in candidates
                            ],
                            "selected": None,
                        }
                    )
                else:
                    resolved_entries.append({**entry, "card": candidates[0]})
                    resolved_count += 1
            else:
                resolve_needed = True
                conflicts.append(
                    {
                        "index": entry["index"],
                        "display": _fmt_entry(entry),
                        "options": [
                            {
                                "id": c.id,
                                "name": c.name,
                                "set_code": c.set_code,
                                "collector_number": c.collector_number,
                                "quantity": c.quantity or 0,
                                "lang": c.lang or "en",
                                "is_foil": bool(c.is_foil),
                                "folder": c.folder.name if c.folder else None,
                            }
                            for c in candidates
                        ],
                        "selected": None,
                    }
                )

        summary = {
            "requested": len(entries),
            "resolved": resolved_count,
            "total_move": total_requested,
        }

        if resolve_needed or conflicts:
            stage = "resolve"
            return render_template(
                "decks/deck_from_collection.html",
                form=form,
                errors=errors,
                warnings=warnings,
                infos=infos,
                conflicts=conflicts,
                summary=summary,
                deck_tag_groups=get_deck_tag_groups(),
                stage=stage,
            )

        deck_name = _generate_unique_folder_name(form["deck_name"])
        folder = Folder(
            name=deck_name,
            deck_tag=form["deck_tag"] or None,
            owner=current_user.username or current_user.email or None,
            owner_user_id=current_user.id,
            is_proxy=False,
        )
        folder.set_primary_role(Folder.CATEGORY_DECK)
        commander_warnings: list[str] = []
        commander_oid = None
        commander_clean = form["commander"]
        if commander_clean:
            try:
                commander_oid = unique_oracle_by_name(commander_clean)
            except Exception as exc:
                commander_warnings.append(f"Commander lookup failed: {exc}")
            folder.commander_name = commander_clean
            folder.commander_oracle_id = commander_oid
        db.session.add(folder)
        db.session.flush()

        moved_total = 0
        for entry in resolved_entries:
            card = entry.get("card")
            desired_qty = entry["qty"]
            remaining_qty = desired_qty
            moved_from_collection = 0

            if card:
                available_qty = card.quantity or 0
                move_qty = min(desired_qty, available_qty)
                remaining_qty = desired_qty - move_qty
                if move_qty > 0:
                    target = (
                        Card.query.filter(
                            Card.folder_id == folder.id,
                            Card.name == card.name,
                        Card.set_code == card.set_code,
                        Card.collector_number == card.collector_number,
                        Card.lang == card.lang,
                        Card.is_foil == card.is_foil,
                    )
                    .order_by(Card.id.asc())
                    .first()
                )
                    if target:
                        target.quantity = (target.quantity or 0) + move_qty
                    else:
                        db.session.add(
                            Card(
                                name=card.name,
                                set_code=card.set_code,
                                collector_number=card.collector_number,
                                folder_id=folder.id,
                                quantity=move_qty,
                                oracle_id=card.oracle_id,
                                lang=card.lang,
                                is_foil=card.is_foil,
                                type_line=card.type_line,
                                rarity=card.rarity,
                                oracle_text=card.oracle_text,
                                mana_value=card.mana_value,
                                colors=card.colors,
                                color_identity=card.color_identity,
                                color_identity_mask=card.color_identity_mask,
                                layout=card.layout,
                                faces_json=card.faces_json,
                            )
                        )
                    card.quantity = (card.quantity or 0) - move_qty
                    if card.quantity is not None and card.quantity <= 0:
                        db.session.delete(card)
                    moved_from_collection = move_qty

            if moved_from_collection < desired_qty:
                proxy_qty = remaining_qty if remaining_qty > 0 else 0
                if proxy_qty > 0:
                    warnings.append(
                        f"Line {entry['index']}: Missing {proxy_qty} copies for {_fmt_entry(entry)} "
                        f"(requested {desired_qty}, moved {moved_from_collection} from collection)."
                    )
            moved_total += moved_from_collection

        if commander_warnings:
            warnings.extend(commander_warnings)

        db.session.commit()
        infos.append(f"Created deck '{deck_name}' and moved {moved_total} card(s).")
        form["deck_lines"] = ""
        stage = "done"

    return render_template(
        "decks/deck_from_collection.html",
        form=form,
        errors=errors,
        warnings=warnings,
        infos=infos,
        conflicts=conflicts,
        summary=summary,
        deck_tag_groups=get_deck_tag_groups(),
        stage=stage,
    )


def deck_tokens_overview():
    """
    Aggregate all tokens produced by cards across every deck folder.
    """
    deck_rows = (
        Folder.query.filter(Folder.role_entries.any(FolderRole.role == FolderRole.ROLE_DECK))
        .order_by(Folder.name.asc())
        .all()
    )

    deck_map = {deck.id: deck for deck in deck_rows}
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

    print_cache_by_oracle = {}
    print_cache_by_setcn = {}
    image_cache = {}

    tokens_by_key: Dict[str, dict] = {}
    deck_token_sets: defaultdict[int, set] = defaultdict(set)
    total_sources = 0
    total_qty = 0

    for cid, name, set_code, collector_number, oracle_id, folder_id, qty, oracle_text, faces_json in card_rows:
        qty = int(qty or 0) or 1
        deck = deck_map.get(folder_id)
        if not deck:
            continue
        text = oracle_text or _oracle_text_from_faces(faces_json)
        tokens = _token_stubs_from_oracle_text(text)
        if not tokens:
            continue

        p = None
        if have_cache:
            if oracle_id:
                if oracle_id in print_cache_by_oracle:
                    p = print_cache_by_oracle[oracle_id]
                else:
                    try:
                        prints = prints_for_oracle(oracle_id) or []
                        p = prints[0] if prints else None
                    except Exception:
                        p = None
                    print_cache_by_oracle[oracle_id] = p
            if not p:
                set_key = (set_code, collector_number, (name or "").lower())
                if set_key in print_cache_by_setcn:
                    p = print_cache_by_setcn[set_key]
                else:
                    try:
                        p = find_by_set_cn(set_code, collector_number, name)
                    except Exception:
                        p = None
                    print_cache_by_setcn[set_key] = p

        src_img_url = None
        if have_cache and p:
            img_key = p.get("id") or (set_code, collector_number, (name or "").lower())
            if img_key in image_cache:
                src_img_url = image_cache[img_key]
            else:
                try:
                    img_payload = sc.image_for_print(p)
                    src_img_url = (img_payload or {}).get("small") or (img_payload or {}).get("normal")
                except Exception:
                    src_img_url = None
                image_cache[img_key] = src_img_url

        deck_name = deck.name or f"Deck {folder_id}"

        for token in tokens:
            token_name = (token.get("name") or "Token").strip()
            token_type = (token.get("type_line") or "").strip()
            token_id = token.get("id")
            token_key = token_id or f"{token_name.lower()}|{token_type.lower()}"
            imgs = token.get("images") or {}

            entry = tokens_by_key.setdefault(
                token_key,
                {
                    "id": token_id,
                    "name": token_name,
                    "type_line": token_type,
                    "small": imgs.get("small"),
                    "normal": imgs.get("normal"),
                    "sources": [],
                    "decks": {},
                    "total_qty": 0,
                },
            )

            source_entry = {
                "card_id": cid,
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
            deck_info["sources"].sort(key=lambda src: (src["name"] or "").lower())
            deck_groups.append(deck_info)
        deck_groups.sort(key=lambda d: (d["deck_name"] or "").lower())
        entry["decks"] = deck_groups
        entry["deck_count"] = len(deck_groups)
        entry["sources"].sort(key=lambda src: ((src["deck_name"] or "").lower(), (src["name"] or "").lower()))
        entry["total_sources"] = len(entry["sources"])
        entry["image"] = entry.get("small") or entry.get("normal")
        tokens_raw.append(entry)

    tokens_raw.sort(key=lambda tok: (tok["name"] or "").lower())

    token_vms: list[DeckTokenVM] = []
    for entry in tokens_raw:
        deck_ids = [deck.get("deck_id") for deck in entry.get("decks") or [] if deck.get("deck_id") is not None]
        deck_names = [
            deck.get("deck_name")
            for deck in entry.get("decks") or []
            if deck.get("deck_name")
        ]
        search_key = f"{entry.get('name') or ''} {entry.get('type_line') or ''} {' '.join(deck_names)}".lower().strip()
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
                deck_ids_csv=",".join(str(did) for did in deck_ids),
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
    )


def opening_hand():
    decks = (
        Folder.query.filter(Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES)))
        .order_by(Folder.name.asc())
        .all()
    )
    deck_options = [
        FolderOptionVM(id=deck.id, name=deck.name or f"Deck {deck.id}")
        for deck in decks
    ]

    deck_card_lookup: dict[str, list[dict]] = {}
    deck_token_lookup: dict[str, list[dict]] = {}
    deck_ids = [deck.id for deck in decks]
    if deck_ids:
        card_rows = (
            Card.query.with_entities(
                Card.folder_id,
                Card.id,
                Card.name,
                Card.set_code,
                Card.collector_number,
            Card.lang,
            Card.is_foil,
            Card.oracle_id,
            Card.type_line,
            Card.mana_value,
            Card.oracle_text,
            Card.faces_json,
        )
        .filter(Card.folder_id.in_(deck_ids))
        .order_by(Card.folder_id.asc(), Card.name.asc(), Card.collector_number.asc())
        .all()
        )
        placeholder_image = url_for("static", filename="img/card-placeholder.svg")
        seen_map: dict[str, set[str]] = {}
        token_seen: dict[str, set[str]] = {}
        for (
            folder_id,
            card_id,
            card_name,
            set_code,
            collector_number,
            lang,
            is_foil,
            oracle_id,
            type_line,
            mana_value,
            oracle_text,
            faces_json,
        ) in card_rows:
            if not card_name:
                continue
            folder_key = str(folder_id)
            entries = deck_card_lookup.setdefault(folder_key, [])
            seen = seen_map.setdefault(folder_key, set())
            value_token = f"{card_id or 0}:{set_code}:{collector_number}:{lang or 'en'}:{1 if is_foil else 0}"
            if value_token in seen:
                continue
            seen.add(value_token)

            pr = None
            try:
                pr = _lookup_print_data(set_code, collector_number, card_name, oracle_id)
            except Exception:
                pr = None

            if not pr and oracle_id:
                try:
                    prints = prints_for_oracle(oracle_id) or []
                    if prints:
                        pr = next((p for p in prints if not p.get("digital")), prints[0])
                except Exception:
                    pr = None

            if not pr:
                try:
                    pr = find_by_set_cn(set_code, collector_number, card_name)
                except Exception:
                    pr = None

            imgs = _image_from_print(pr)
            flags = _card_type_flags(type_line)
            entry_vm = OpeningHandCardVM(
                value=value_token,
                name=card_name,
                image=imgs.get("normal") or imgs.get("large") or imgs.get("small") or placeholder_image,
                hover=imgs.get("large") or imgs.get("normal") or imgs.get("small") or placeholder_image,
                type_line=type_line or "",
                mana_value=mana_value,
                is_creature=bool(flags["is_creature"]),
                is_land=bool(flags["is_land"]),
                is_instant=bool(flags["is_instant"]),
                is_sorcery=bool(flags["is_sorcery"]),
                is_permanent=bool(flags["is_permanent"]),
                zone_hint=str(flags["zone_hint"]),
            )
            entries.append(entry_vm.to_payload())

            text = oracle_text or _oracle_text_from_faces(faces_json)
            tokens = _token_stubs_from_oracle_text(text)

            if tokens:
                token_bucket = deck_token_lookup.setdefault(folder_key, [])
                seen_tokens = token_seen.setdefault(folder_key, set())
                for token in tokens:
                    token_name = (token.get("name") or "Token").strip()
                    token_type = (token.get("type_line") or "").strip()
                    token_id = token.get("id")
                    token_key = token_id or f"{token_name.lower()}|{token_type.lower()}"
                    if token_key in seen_tokens:
                        continue
                    seen_tokens.add(token_key)
                    token_imgs = token.get("images") or {}
                    token_flags = _card_type_flags(token_type)
                    token_vm = OpeningHandTokenVM(
                        id=token_id,
                        name=token_name,
                        type_line=token_type,
                        image=token_imgs.get("normal") or token_imgs.get("small") or placeholder_image,
                        hover=token_imgs.get("large") or token_imgs.get("normal") or token_imgs.get("small") or placeholder_image,
                        is_creature=bool(token_flags["is_creature"]),
                        is_land=bool(token_flags["is_land"]),
                        is_instant=bool(token_flags["is_instant"]),
                        is_sorcery=bool(token_flags["is_sorcery"]),
                        is_permanent=bool(token_flags["is_permanent"]),
                        zone_hint=str(token_flags["zone_hint"]),
                    )
                    token_bucket.append(token_vm.to_payload())

        for entries in deck_card_lookup.values():
            entries.sort(key=lambda item: (item.get("name") or "").lower())
        for token_entries in deck_token_lookup.values():
            token_entries.sort(key=lambda item: (item.get("name") or "").lower())

    for deck in decks:
        deck_card_lookup.setdefault(str(deck.id), [])
        deck_token_lookup.setdefault(str(deck.id), [])

    deck_card_lookup_json = json.dumps(deck_card_lookup, ensure_ascii=True)
    deck_token_lookup_json = json.dumps(deck_token_lookup, ensure_ascii=True)

    return render_template(
        "decks/opening_hand.html",
        deck_options=deck_options,
        deck_card_lookup_json=deck_card_lookup_json,
        deck_token_lookup_json=deck_token_lookup_json,
    )


def opening_hand_shuffle():
    payload = request.get_json(silent=True) or {}
    deck_id_raw = payload.get("deck_id")
    deck_list_text = (payload.get("deck_list") or "").strip()
    commander_hint = (payload.get("commander_name") or "").strip()

    deck_name = None
    entries: list[dict] = []
    warnings: list[str] = []

    commander_cards: list[dict] = []

    deck_id = None
    if deck_id_raw not in (None, "", False):
        try:
            deck_id = parse_positive_int(deck_id_raw, field="deck id")
        except ValidationError as exc:
            log_validation_error(exc, context="opening_hand_shuffle")
            return jsonify({"ok": False, "error": "Invalid deck selection."}), 400

    if deck_id:
        deck_name, entries, warnings, commander_cards = _deck_entries_from_folder(deck_id)
        if deck_name is None:
            return jsonify({"ok": False, "error": "Deck not found."}), 404
    elif deck_list_text:
        deck_name, entries, warnings, commander_cards = _deck_entries_from_list(deck_list_text, commander_hint)
    else:
        return jsonify({"ok": False, "error": "Select a deck or paste a deck list first."}), 400

    deck_pool = _expanded_deck_entries(entries)
    deck_size = len(deck_pool)
    if deck_size < HAND_SIZE:
        return jsonify({"ok": False, "error": f"Deck needs at least {HAND_SIZE} drawable cards.", "warnings": warnings}), 400

    random.shuffle(deck_pool)
    hand_cards = deck_pool[:HAND_SIZE]
    next_index = HAND_SIZE
    state = {
        "deck": deck_pool,
        "index": next_index,
        "deck_name": deck_name,
    }
    state_token = _encode_state(state)
    remaining = deck_size - next_index
    placeholder = url_for("static", filename="img/card-placeholder.svg")
    hand_payload = [_client_card_payload(card, placeholder) for card in hand_cards]
    commander_payload = [_client_card_payload(card, placeholder) for card in commander_cards]

    return jsonify(
        {
            "ok": True,
            "hand": hand_payload,
            "state": state_token,
            "remaining": remaining,
            "deck_name": deck_name,
            "warnings": warnings,
            "deck_size": deck_size,
            "commanders": commander_payload,
        }
    )


def opening_hand_draw():
    payload = request.get_json(silent=True) or {}
    token = payload.get("state") or ""
    state = _decode_state(token)
    if not state:
        return jsonify({"ok": False, "error": "Invalid or expired hand state."}), 400

    deck = state.get("deck") or []
    index = int(state.get("index") or 0)
    deck_name = state.get("deck_name") or "Deck"

    if index >= len(deck):
        return jsonify({"ok": False, "error": "No more cards to draw.", "remaining": 0, "deck_name": deck_name, "state": token})

    card_entry = deck[index]
    index += 1
    state["index"] = index
    new_token = _encode_state(state)
    remaining = len(deck) - index
    placeholder = url_for("static", filename="img/card-placeholder.svg")
    card_payload = _client_card_payload(card_entry, placeholder)

    return jsonify(
        {
            "ok": True,
            "card": card_payload,
            "state": new_token,
            "remaining": remaining,
            "deck_name": deck_name,
        }
    )


def _facets():
    sets = [s for (s,) in db.session.query(Card.set_code).distinct().order_by(Card.set_code.asc()).all() if s]
    langs = [lg for (lg,) in db.session.query(Card.lang).distinct().order_by(Card.lang.asc()).all() if lg]
    folders = db.session.query(Folder).order_by(Folder.name.asc()).all()
    return sets, langs, folders


def _rarity_options() -> List[Dict[str, str]]:
    rows = (
        db.session.query(func.lower(Card.rarity))
        .filter(Card.rarity.isnot(None), Card.rarity != "")
        .distinct()
        .order_by(func.lower(Card.rarity))
        .all()
    )
    present: Set[str] = set()
    for (value,) in rows:
        if not value:
            continue
        clean = value.strip().lower()
        if clean:
            present.add(clean)

    options: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for value, label in RARITY_CHOICE_ORDER:
        clean = value.strip().lower()
        if not clean or clean in seen:
            continue
        options.append({"value": clean, "label": label})
        seen.add(clean)
        present.discard(clean)

    for extra in sorted(present):
        if extra in seen:
            continue
        label = extra.replace("_", " ").replace("-", " ").title()
        options.append({"value": extra, "label": label})
        seen.add(extra)

    return options


def _set_options_with_names(codes: Iterable[str]) -> List[Dict[str, str]]:
    _ensure_cache_ready()
    options: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for code in codes or []:
        norm = (code or "").strip().lower()
        if not norm or norm in seen:
            continue
        label = norm.upper()
        set_name = None
        try:
            set_name = set_name_for_code(norm)
        except Exception:
            set_name = None
        if set_name:
            label = f"{label} ({set_name})"
        options.append({"code": norm, "label": label})
        seen.add(norm)
    return options


def card_detail(card_id):
    """
    Owned card detail:
      - unique arts (owned art first)
      - rulings, tokens, pips
      - provides scryfall_id / oracle_id / prints_search_uri / main_img_url
    """
    ensure_symbols_cache(force=False)
    _, collection_folder_names, _ = _collection_metadata()

    card = get_or_404(Card, card_id)
    have_cache = ensure_cache_loaded()
    oid = card.oracle_id

    if have_cache and not oid:
        found = _request_cached_find_by_set_cn(card.set_code, card.collector_number, card.name)
        if found and found.get("oracle_id"):
            oid = found["oracle_id"]
            card.set_oracle_id(oid)
            db.session.commit()

    prints = []
    if have_cache and oid:
        prints = _request_cached_prints_for_oracle(oid)
    elif have_cache:
        fetched = _request_cached_find_by_set_cn(card.set_code, card.collector_number, card.name)
        if fetched:
            prints = [fetched]
            oid = oid or fetched.get("oracle_id")

    if not prints:
        live = sc.fetch_live_print(card.set_code, card.collector_number, card.name)
        if live:
            prints = [live]
            oid = oid or live.get("oracle_id") or oid
            if live.get("oracle_id") and not card.oracle_id:
                card.set_oracle_id(live.get("oracle_id"))
                db.session.commit()

    owned_set = (card.set_code or "").lower()
    owned_cn = str(card.collector_number) if card.collector_number is not None else ""
    owned_lang = (card.lang or "").lower()

    best = None
    for pr in prints:
        if (
            (pr.get("set") or "").lower() == owned_set
            and str(pr.get("collector_number") or "") == owned_cn
            and ((pr.get("lang") or "").lower() == owned_lang or not owned_lang)
        ):
            best = pr
            break
    if not best:
        for pr in prints:
            if (pr.get("set") or "").lower() == owned_set and str(pr.get("collector_number") or "") == owned_cn:
                best = pr
                break
    if not best and prints:
        best = prints[0]

    def _img(obj):
        iu = obj.get("image_uris")
        if iu:
            return {"small": iu.get("small"), "normal": iu.get("normal"), "large": iu.get("large")}
        faces = obj.get("card_faces") or []
        if faces and isinstance(faces, list):
            iu2 = (faces[0] or {}).get("image_uris") or {}
            return {"small": iu2.get("small"), "normal": iu2.get("normal"), "large": iu2.get("large")}
        return {"small": None, "normal": None, "large": None}

    def _oracle_text(obj):
        faces = obj.get("card_faces") or []
        if faces:
            parts = [face.get("oracle_text") for face in faces if face.get("oracle_text")]
            return " // ".join(parts) if parts else None
        return obj.get("oracle_text")

    unique = _unique_art_variants(prints)
    owned_art_id = (best or {}).get("illustration_id") or (best or {}).get("id")
    ordered = []
    if best:
        for pr in unique:
            pid = pr.get("illustration_id") or pr.get("id")
            if pid == owned_art_id:
                ordered.append(pr)
                break
        for pr in unique:
            pid = pr.get("illustration_id") or pr.get("id")
            if pid != owned_art_id:
                ordered.append(pr)
    else:
        ordered = unique

    images = []
    for pr in ordered:
        iu = _img(pr)
        if iu["small"] or iu["normal"] or iu["large"]:
            bits = []
            if pr.get("set"):
                bits.append(pr.get("set").upper())
            if pr.get("collector_number"):
                bits.append(str(pr.get("collector_number")))
            if pr.get("lang"):
                bits.append(str(pr.get("lang")).upper())
            label = " · ".join(bits) if bits else (pr.get("name") or card.name)
            images.append({"small": iu["small"], "normal": iu["normal"], "large": iu["large"], "label": label})

    oracle_text = getattr(card, "oracle_text", None) or _oracle_text_from_faces(getattr(card, "faces_json", None))
    mana_cost = _mana_cost_from_faces(getattr(card, "faces_json", None))
    colors = _color_letters_list(getattr(card, "colors", None))
    color_identity = _color_letters_list(getattr(card, "color_identity", None)) or colors
    if not colors:
        colors = color_identity

    commander_legality = None
    legalities = {"commander": None}
    if best:
        all_leg = best.get("legalities") or {}
        commander_legality = all_leg.get("commander")
        legalities = {"commander": commander_legality}

    info = {
        "name": card.name,
        "mana_cost": mana_cost,
        "mana_cost_html": render_mana_html(mana_cost, use_local=False),
        "cmc": getattr(card, "mana_value", None),
        "type_line": getattr(card, "type_line", None),
        "oracle_text": oracle_text,
        "oracle_text_html": render_oracle_html(oracle_text, use_local=False),
        "colors": colors or [],
        "color_identity": color_identity or [],
        "keywords": [],
        "rarity": getattr(card, "rarity", None),
        "set": card.set_code,
        "set_name": set_name_for_code(card.set_code) if have_cache else None,
        "collector_number": card.collector_number,
        "scryfall_uri": (best or {}).get("scryfall_uri") or _scryfall_card_url(card.set_code, card.collector_number),
        "scryfall_set_uri": (best or {}).get("scryfall_set_uri") or _scryfall_set_url(card.set_code),
        "legalities": legalities,
        "commander_legality": commander_legality,
    }
    purchase_uris: dict = {}
    related_uris: dict = {}
    prices: dict = {}
    if best:
        purchase_uris = best.get("purchase_uris") or {}
        related_uris = best.get("related_uris") or {}
        prices = _prices_for_print(best)
        info["purchase_uris"] = purchase_uris
        info["related_uris"] = related_uris
        info["prices"] = prices
        info["price_text"] = _format_price_text(prices)
        info["tcgplayer_url"] = purchase_uris.get("tcgplayer") or related_uris.get("tcgplayer")
    else:
        info["purchase_uris"] = {}
        info["related_uris"] = {}
        info["prices"] = {}
        info["price_text"] = None
        info["tcgplayer_url"] = None

    if oid:
        info["oracle_id"] = oid
        if not info.get("prints_search_uri"):
            info["prints_search_uri"] = (
                f"https://api.scryfall.com/cards/search?order=released&q=oracleid:{oid}&unique=prints"
            )

    tokens_created = _token_stubs_from_oracle_text(oracle_text)
    pip_srcs = colors_to_icons(info.get("color_identity") or info.get("colors"), use_local=False)
    rulings = _request_cached_rulings(oid)

    selected = ordered[0] if ordered else {}

    def _pick_img(obj):
        iu = (obj or {}).get("image_uris") or {}
        faces = (obj or {}).get("card_faces") or []
        if not iu and faces and isinstance(faces, list):
            iu = (faces[0] or {}).get("image_uris") or {}
        return iu.get("large") or iu.get("normal") or iu.get("small")

    scryfall_id = selected.get("id")
    main_img_url = _pick_img(selected) or (
        (images[0].get("large") or images[0].get("normal") or images[0].get("small")) if images else None
    )
    oracle_id = oid
    display_name = info.get("name") or card.name
    role_labels = [(r.label or getattr(r, "name", None) or r.key) for r in (card.roles or [])]
    subrole_labels = [(s.label or getattr(s, "name", None) or s.key) for s in (card.subroles or [])]
    primary_role_label = _request_cached_primary_role_label(card.id)
    evergreen_labels = _request_cached_evergreen_labels(oid)

    commander_legality = info.get("commander_legality")
    commander_label = None
    commander_class = None
    if commander_legality:
        leg_norm = str(commander_legality)
        commander_label = "Not legal" if leg_norm == "not_legal" else leg_norm.replace("_", " ").capitalize()
        if leg_norm == "legal":
            commander_class = "bg-success"
        elif leg_norm == "banned":
            commander_class = "bg-danger"
        elif leg_norm == "restricted":
            commander_class = "bg-warning text-dark"
        else:
            commander_class = "bg-secondary"

    prices_json = json.dumps(prices or {}, ensure_ascii=True)
    has_oracle_text = bool(info.get("oracle_text_html")) and info.get("oracle_text_html") != "—"
    has_mana_cost = bool(info.get("mana_cost_html"))

    info_vm = CardInfoVM(
        name=info.get("name"),
        mana_cost_html=info.get("mana_cost_html"),
        cmc=info.get("cmc"),
        type_line=info.get("type_line"),
        oracle_text_html=info.get("oracle_text_html"),
        colors=info.get("colors") or [],
        color_identity=info.get("color_identity") or [],
        keywords=info.get("keywords") or [],
        rarity=info.get("rarity"),
        set_code=info.get("set"),
        set_name=info.get("set_name"),
        collector_number=info.get("collector_number"),
        scryfall_uri=info.get("scryfall_uri"),
        scryfall_set_uri=info.get("scryfall_set_uri"),
        commander_legality=commander_legality,
        commander_legality_label=commander_label,
        commander_legality_class=commander_class,
        has_commander_legality=bool(commander_label),
        price_text=info.get("price_text"),
        tcgplayer_url=info.get("tcgplayer_url"),
        prints_search_uri=info.get("prints_search_uri"),
        lang=info.get("lang"),
        oracle_id=info.get("oracle_id"),
        prices_json=prices_json,
        has_prices=bool(prices),
        has_oracle_text=has_oracle_text,
        has_mana_cost=has_mana_cost,
        has_scryfall_uri=bool(info.get("scryfall_uri")),
        has_scryfall_set_uri=bool(info.get("scryfall_set_uri")),
    )

    print_images = []
    if prints:
        ordered_prints = []
        if best:
            ordered_prints.append(best)
        ordered_prints.extend([pr for pr in prints if pr is not best])
        seen_prints: set[str] = set()
        for pr in ordered_prints:
            pid = pr.get("id") or ""
            if pid and pid in seen_prints:
                continue
            if pid:
                seen_prints.add(pid)
            img_pack = _img(pr)
            if not (img_pack.get("small") or img_pack.get("normal") or img_pack.get("large")):
                continue
            set_code = (pr.get("set") or "").upper()
            collector_number = str(pr.get("collector_number") or "")
            lang_code = str(pr.get("lang") or "").upper()
            label_bits = [val for val in (set_code, collector_number, lang_code) if val]
            label = " · ".join(label_bits) if label_bits else (pr.get("name") or card.name)
            prices = pr.get("prices") or {}
            purchase = pr.get("purchase_uris") or {}
            related = pr.get("related_uris") or {}
            set_name = pr.get("set_name") or (set_name_for_code(pr.get("set")) if have_cache and pr.get("set") else "")
            print_images.append(
                {
                    "id": pid,
                    "set": set_code,
                    "setName": set_name or "",
                    "collectorNumber": collector_number,
                    "lang": lang_code,
                    "rarity": pr.get("rarity") or "",
                    "prices": prices or {},
                    "name": pr.get("name") or card.name,
                    "scryUri": pr.get("scryfall_uri") or _scryfall_card_url(pr.get("set"), pr.get("collector_number")),
                    "tcgUri": purchase.get("tcgplayer") or related.get("tcgplayer") or "",
                    "releasedAt": pr.get("released_at") or "",
                    "small": img_pack.get("small") or img_pack.get("normal") or img_pack.get("large"),
                    "normal": img_pack.get("normal") or img_pack.get("large") or img_pack.get("small"),
                    "large": img_pack.get("large") or img_pack.get("normal") or img_pack.get("small"),
                    "label": label,
                }
            )
    print_images_json = json.dumps(print_images, ensure_ascii=True)

    image_vms = [
        ImageSetVM(
            small=img.get("small"),
            normal=img.get("normal"),
            large=img.get("large"),
            label=img.get("label"),
        )
        for img in images
    ]

    token_vms: list[CardTokenVM] = []
    for token in tokens_created or []:
        img_pack = token.get("images") or sc.image_for_print(token) or {}
        token_vms.append(
            CardTokenVM(
                id=token.get("id"),
                name=token.get("name"),
                type_line=token.get("type_line"),
                images=ImageSetVM(
                    small=img_pack.get("small"),
                    normal=img_pack.get("normal"),
                    large=img_pack.get("large"),
                ),
            )
        )

    folder_ref = None
    if getattr(card, "folder", None):
        folder_ref = FolderRefVM(id=card.folder.id, name=card.folder.name)
    card_vm = CardListItemVM(
        id=card.id,
        name=card.name,
        display_name=display_name,
        quantity=int(card.quantity or 0) or 1,
        folder=folder_ref,
        set_code=card.set_code,
        collector_number=str(card.collector_number) if card.collector_number is not None else None,
        lang=card.lang,
        is_foil=bool(card.is_foil),
    )

    return render_template(
        "cards/card_detail.html",
        card=card_vm,
        info=info_vm,
        images=image_vms,
        print_images_json=print_images_json,
        rulings=rulings,
        color_pips=pip_srcs,
        tokens_created=token_vms,
        scryfall_id=scryfall_id,
        oracle_id=oracle_id,
        main_img_url=main_img_url,
        name=display_name,
        collection_folders=collection_folder_names,
        primary_role_label=primary_role_label,
        role_labels=role_labels,
        subrole_labels=subrole_labels,
        evergreen_labels=evergreen_labels,
    )


def smart_card_detail(id_or_sid):
    """Smart redirect: integer -> owned card, else -> scryfall print detail."""
    try:
        cid = int(id_or_sid)
        return redirect(url_for("views.card_detail", card_id=cid))
    except ValueError:
        return redirect(url_for("views.scryfall_print_detail", sid=id_or_sid))


__all__ = [
    "collection_overview",
    "create_proxy_deck",
    "create_proxy_deck_bulk",
    "bulk_move_cards",
    "bulk_delete_cards",
    "api_card_printing_options",
    "api_update_card_printing",
    "api_fetch_proxy_deck",
    "deck_tokens_overview",
    "opening_hand",
    "opening_hand_shuffle",
    "opening_hand_draw",
    "decks_overview",
    "list_cards",
]
