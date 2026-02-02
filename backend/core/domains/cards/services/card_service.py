"""Card browsing, collection summaries, and deck-centric routes."""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections import defaultdict
from math import ceil
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import quote
from sqlalchemy.exc import IntegrityError

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import load_only, selectinload

from extensions import cache, db
from models import (
    BuildSession,
    BuildSessionCard,
    Card,
    Folder,
    FolderRole,
    FolderShare,
    FriendCardRequest,
    User,
    UserSetting,
    UserFriend,
    UserFriendRequest,
    WishlistItem,
)
from models.role import Role, SubRole, CardRole, OracleCoreRoleTag, OracleEvergreenTag, OracleRole
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.proxy_decks import fetch_proxy_deck, resolve_proxy_cards
from core.domains.decks.services.commander_brackets import BRACKET_RULESET_EPOCH, evaluate_commander_bracket, spellbook_dataset_epoch
from core.domains.decks.services.commander_cache import compute_bracket_signature, get_cached_bracket, store_cached_bracket
from core.domains.decks.services.deck_tags import (
    get_all_deck_tags,
    get_deck_tag_category,
    get_deck_tag_groups,
    is_valid_deck_tag,
    sync_folder_deck_tag_map,
)
from core.domains.decks.services.deck_metadata_wizard_service import build_deck_metadata_wizard_payload
from core.domains.decks.services.deck_service import deck_curve_rows, deck_land_mana_sources, deck_mana_pip_dist
from shared.cache.request_cache import request_cached
from core.domains.cards.services.scryfall_cache import (
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
from core.domains.decks.services.commander_utils import (
    primary_commander_name,
    primary_commander_oracle_id,
    split_commander_names,
    split_commander_oracle_ids,
)
from core.domains.games.services.stats import get_folder_stats

RE_CREATE_TOKEN = re.compile(r"\bcreate\b.*\btoken\b", flags=re.IGNORECASE | re.DOTALL)
from core.shared.utils.symbols_cache import (
    ensure_symbols_cache,
    render_mana_html,
    render_oracle_html,
    colors_to_icons,
)
from core.domains.users.services.audit import record_audit_event
from shared.auth import ensure_folder_access
from core.shared.utils.assets import static_url
from shared.database import get_or_404
from shared.validation import (
    ValidationError,
    log_validation_error,
    parse_positive_int,
    parse_positive_int_list,
)

from core.routes.base import (
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
from core.domains.cards.viewmodels.card_vm import (
    CardInfoVM,
    CardListItemVM,
    CardTokenVM,
    FolderRefVM,
    ImageSetVM,
    format_role_label,
    slice_badges,
    TypeBreakdownVM,
)
from core.domains.decks.viewmodels.deck_vm import (
    DeckCommanderVM,
    DeckOwnerSummaryVM,
    DeckTokenDeckSummaryVM,
    DeckTokenDeckVM,
    DeckTokenSourceVM,
    DeckTokenVM,
    DeckVM,
)
from core.domains.users.viewmodels.dashboard_vm import (
    DashboardActionVM,
    DashboardModeOptionVM,
    DashboardStatTileVM,
    DashboardViewModel,
)
from core.domains.decks.viewmodels.folder_vm import CollectionBucketVM, FolderOptionVM, FolderVM, SharedFolderEntryVM
from core.domains.decks.viewmodels.opening_hand_vm import OpeningHandCardVM, OpeningHandTokenVM

HAND_SIZE = 7
OPENING_HAND_STATE_SALT = "opening-hand-state-v1"
OPENING_HAND_STATE_MAX_AGE_SECONDS = 6 * 60 * 60

def _user_cache_key() -> str:
    return str(getattr(current_user, "id", None) or "anon")


def _cache_fetch(key: str, ttl_seconds: int, factory):
    """Enhanced cache fetch with memory management."""
    if not cache:
        return factory()
    try:
        cached = cache.get(key)
    except Exception:
        cached = None
    if cached is not None:
        return cached
    
    value = factory()
    
    # Implement cache size limits to prevent memory issues
    try:
        # Only cache if value is reasonable size (< 1MB serialized)
        import sys
        if sys.getsizeof(value) < 1024 * 1024:
            cache.set(key, value, timeout=ttl_seconds)
    except Exception:
        pass
    return value

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


def _request_cached_core_role_labels(oracle_id: str | None) -> list[str]:
    if not oracle_id:
        return []
    key = ("card_view", "core_roles", oracle_id)

    def _load() -> list[str]:
        rows = (
            db.session.query(OracleCoreRoleTag.role)
            .filter(OracleCoreRoleTag.oracle_id == oracle_id)
            .order_by(OracleCoreRoleTag.role.asc())
            .all()
        )
        labels: list[str] = []
        for row in rows:
            role = row[0] if row else None
            if not role:
                continue
            label = format_role_label(role)
            if label not in labels:
                labels.append(label)
        return labels

    return request_cached(key, _load)


def _request_cached_primary_oracle_role_label(oracle_id: str | None) -> str | None:
    if not oracle_id:
        return None
    key = ("card_view", "primary_oracle_role", oracle_id)

    def _load() -> str | None:
        row = (
            db.session.query(OracleRole.primary_role)
            .filter(OracleRole.oracle_id == oracle_id)
            .first()
        )
        if not row or not row[0]:
            return None
        return format_role_label(str(row[0]))

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
    def _lookup_token(label: str) -> dict | None:
        if not label or label.lower() == "token":
            return None
        try:
            matches = sc.search_tokens(label, limit=6) or []
        except Exception:
            return None
        label_norm = label.strip().casefold()
        for match in matches:
            if (match.get("name") or "").strip().casefold() == label_norm:
                return match
        return matches[0] if matches else None

    if "token" in lower:
        for key, label in _COMMON_TOKEN_KINDS:
            if f"{key} token" in lower:
                matched = _lookup_token(label)
                found.append(
                    {
                        "id": (matched or {}).get("id"),
                        "name": (matched or {}).get("name") or label,
                        "type_line": (matched or {}).get("type_line") or f"Token - {label}",
                        "images": (matched or {}).get("images") or {"small": None, "normal": None, "large": None},
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



def _dashboard_card_stats(
    user_key: str,
    folder_ids: tuple[int, ...],
    collection_ids: tuple[int, ...],
) -> dict:
    """Aggregate per-user collection stats with optimized queries."""
    cache_key = ("dashboard_stats", user_key, folder_ids, collection_ids)

    def _load() -> dict:
        if not folder_ids:
            return {
                "rows": 0,
                "qty": 0,
                "unique_names": 0,
                "sets": 0,
                "collection_qty": 0,
            }
        
        # Use single optimized query without load_only for aggregate queries
        totals = (
            db.session.query(
                func.count(Card.id),
                func.coalesce(func.sum(Card.quantity), 0),
                func.count(func.distinct(Card.name)),
                func.count(func.distinct(func.lower(Card.set_code))),
            )
            .filter(Card.folder_id.in_(folder_ids))
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
    serializer = _opening_hand_state_serializer()
    return serializer.dumps(payload)


def _decode_state(token: str) -> Optional[dict]:
    if not token:
        return None
    try:
        serializer = _opening_hand_state_serializer()
        max_age = current_app.config.get(
            "OPENING_HAND_STATE_MAX_AGE_SECONDS",
            OPENING_HAND_STATE_MAX_AGE_SECONDS,
        )
        payload = serializer.loads(token, max_age=max_age)
    except BadSignature:
        return None
    return _normalize_opening_hand_state(payload)


def _opening_hand_state_serializer() -> URLSafeTimedSerializer:
    secret = current_app.secret_key or current_app.config.get("SECRET_KEY") or "dev"
    return URLSafeTimedSerializer(secret, salt=OPENING_HAND_STATE_SALT)


def _normalize_opening_hand_state(payload: object) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    deck = payload.get("deck")
    if not isinstance(deck, list):
        return None
    if any(not isinstance(entry, dict) for entry in deck):
        return None
    try:
        index = int(payload.get("index") or 0)
    except Exception:
        return None
    if index < 0 or index > len(deck):
        return None
    deck_name = payload.get("deck_name") or "Deck"
    if not isinstance(deck_name, str):
        deck_name = str(deck_name)
    user_id = payload.get("user_id")
    try:
        user_id = int(user_id) if user_id is not None else None
    except Exception:
        return None
    if not current_user or not getattr(current_user, "is_authenticated", False):
        return None
    if user_id != current_user.id:
        return None
    return {
        "deck": deck,
        "index": index,
        "deck_name": deck_name,
        "user_id": user_id,
    }


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

    placeholder = static_url("img/card-placeholder.svg")
    imgs = _image_from_print(pr)
    back_imgs = _back_image_from_print(pr)
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
        "back_small": back_imgs.get("small"),
        "back_normal": back_imgs.get("normal"),
        "back_large": back_imgs.get("large"),
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
        back_imgs = _back_image_from_print(pr)
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
                "back_small": back_imgs.get("small"),
                "back_normal": back_imgs.get("normal"),
                "back_large": back_imgs.get("large"),
                "detail_url": detail_url,
                "external_url": external_url,
                "type_line": getattr(card, "type_line", "") or "",
            }
        )
    if not entries:
        warnings.append("No drawable cards found in this deck.")

    commander_cards = _commander_card_payloads(folder.commander_name, folder.commander_oracle_id)

    return deck_name, entries, warnings, commander_cards


def _opening_hand_build_key(session_id: int) -> str:
    return f"build:{session_id}"


def _opening_hand_deck_key(source: str, deck_id: int) -> str:
    if source == "build":
        return _opening_hand_build_key(deck_id)
    return str(deck_id)


def _opening_hand_build_label(session: BuildSession) -> str:
    base = session.build_name or session.commander_name or f"Build {session.id}"
    return f"Proxy Build - {base}"


def _parse_opening_hand_deck_ref(raw_value) -> tuple[str, int] | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    if text.startswith("build:"):
        suffix = text.split(":", 1)[1].strip()
        deck_id = parse_positive_int(suffix, field="build session id")
        return "build", deck_id
    deck_id = parse_positive_int(text, field="deck id")
    return "folder", deck_id


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

    entries: list[dict] = []
    warnings: list[str] = []
    deck_name = _opening_hand_build_label(session)

    for entry in session.cards:
        oracle_id = (entry.card_oracle_id or "").strip()
        qty = int(entry.quantity or 0)
        if not oracle_id or qty <= 0:
            continue
        if oracle_id in commander_oracle_ids:
            continue

        pr = None
        try:
            prints = prints_for_oracle(oracle_id) or []
        except Exception:
            prints = []
        if prints:
            pr = next((p for p in prints if not p.get("digital")), prints[0])
        card_name = (pr or {}).get("name") or "Card"
        if commander_names and card_name.strip().lower() in commander_names:
            continue
        imgs = _image_from_print(pr)
        back_imgs = _back_image_from_print(pr)

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
                "type_line": (pr or {}).get("type_line") or "",
            }
        )

    if not entries:
        warnings.append("No drawable cards found in this build.")

    commander_cards = _commander_card_payloads(session.commander_name, session.commander_oracle_id)

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
        back_imgs = _back_image_from_print(pr)
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
    back_image = entry.get("back_large") or entry.get("back_normal") or entry.get("back_small") or entry.get("back_image")
    back_hover = entry.get("back_large") or entry.get("back_normal") or entry.get("back_small") or entry.get("back_hover")
    detail_url = entry.get("detail_url") or entry.get("external_url")
    flags = _card_type_flags(entry.get("type_line"))
    payload = {
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
    if back_image or back_hover:
        payload["back_image"] = back_image or back_hover
        payload["back_hover"] = back_hover or back_image
    return payload


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
        sync_folder_deck_tag_map(folder)
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
    return sorted(
        summary.values(),
        key=lambda item: (item["label"].lower(), item["key"]),
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
        fetched_name, fetched_owner, fetched_commander, fetched_lines, errors = fetch_proxy_deck(deck_url)
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

    signature_source = "\n".join(deck_lines).strip().lower()
    if deck_url:
        signature_source = f"url:{deck_url.strip().lower()}\n{signature_source}"
    if deck_name:
        signature_source = f"name:{deck_name.strip().lower()}\n{signature_source}"
    if owner:
        signature_source = f"owner:{owner.strip().lower()}\n{signature_source}"
    if commander_input:
        signature_source = f"commander:{commander_input.strip().lower()}\n{signature_source}"
    signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()
    last_signature = session.get("proxy_deck_signature")
    last_ts = session.get("proxy_deck_signature_ts")
    last_id = session.get("proxy_deck_signature_id")
    if last_signature == signature and isinstance(last_ts, (int, float)):
        if (time.time() - float(last_ts)) < 15:
            existing_folder = db.session.get(Folder, last_id) if last_id else None
            if existing_folder and existing_folder.is_proxy:
                redirect_url = url_for("views.folder_detail", folder_id=existing_folder.id)
                if expects_json:
                    return jsonify({"ok": True, "folder_id": existing_folder.id, "redirect": redirect_url}), 200
                flash('Proxy deck already created. Redirecting to the existing deck.', "info")
                return redirect(redirect_url)

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
    session["proxy_deck_signature"] = signature
    session["proxy_deck_signature_ts"] = time.time()
    session["proxy_deck_signature_id"] = folder.id

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
        fetched_name, fetched_owner, fetched_commander, fetched_lines, fetch_errors = fetch_proxy_deck(url)
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

    name, owner, commander, lines, errors = fetch_proxy_deck(deck_url)
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

    placeholder_thumb = static_url("img/card-placeholder.svg")
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

_DASHBOARD_SETTING_KEY = "dashboard_mode"
_DEFAULT_DASHBOARD_MODE = "collection"
_DASHBOARD_MODES = {
    "collection": {
        "label": "Collection",
        "description": "Collection insights and ownership.",
        "partial": "dashboard/_collection.html",
    },
    "decks": {
        "label": "Decks",
        "description": "Deck overview and maintenance.",
        "partial": "dashboard/_decks.html",
    },
}
_DASHBOARD_MODE_SEQUENCE = ("collection", "decks")


def _normalize_dashboard_mode(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in _DASHBOARD_MODES:
        return normalized
    return _DEFAULT_DASHBOARD_MODE


def _is_missing_table_error(exc: Exception, table_name: str) -> bool:
    message = str(exc).lower()
    return table_name.lower() in message and ("does not exist" in message or "undefinedtable" in message)


def _ensure_user_settings_table() -> bool:
    try:
        db.session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
        )
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to ensure user_settings table.")
        return False


def _load_dashboard_mode() -> str:
    try:
        setting = db.session.get(UserSetting, _DASHBOARD_SETTING_KEY)
    except Exception as exc:
        db.session.rollback()
        if _is_missing_table_error(exc, "user_settings") and _ensure_user_settings_table():
            try:
                setting = db.session.get(UserSetting, _DASHBOARD_SETTING_KEY)
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to load dashboard mode preference after ensuring table.")
                return _DEFAULT_DASHBOARD_MODE
        else:
            current_app.logger.exception("Failed to load dashboard mode preference.")
            return _DEFAULT_DASHBOARD_MODE
    if setting and setting.value:
        return _normalize_dashboard_mode(setting.value)
    return _DEFAULT_DASHBOARD_MODE


def _persist_dashboard_mode(mode: str) -> None:
    mode = _normalize_dashboard_mode(mode)
    try:
        setting = db.session.get(UserSetting, _DASHBOARD_SETTING_KEY)
        if setting:
            setting.value = mode
        else:
            db.session.add(UserSetting(key=_DASHBOARD_SETTING_KEY, value=mode))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if _is_missing_table_error(exc, "user_settings") and _ensure_user_settings_table():
            try:
                setting = db.session.get(UserSetting, _DASHBOARD_SETTING_KEY)
                if setting:
                    setting.value = mode
                else:
                    db.session.add(UserSetting(key=_DASHBOARD_SETTING_KEY, value=mode))
                db.session.commit()
                return
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to update dashboard mode preference after ensuring table.")
                return
        current_app.logger.exception("Failed to update dashboard mode preference.")


def dashboard():
    if request.method == "POST":
        selected_mode = _normalize_dashboard_mode(request.form.get("dashboard_mode"))
        _persist_dashboard_mode(selected_mode)
        return redirect(url_for("views.dashboard"))

    mode = _load_dashboard_mode()
    mode_meta = _DASHBOARD_MODES.get(mode, _DASHBOARD_MODES[_DEFAULT_DASHBOARD_MODE])
    owner_id = current_user.id if current_user and getattr(current_user, "is_authenticated", False) else None

    folder_ids: list[int] = []
    collection_ids: list[int] = []
    if owner_id:
        folder_ids = [
            fid
            for (fid,) in db.session.query(Folder.id)
            .filter(Folder.owner_user_id == owner_id)
            .all()
        ]
        collection_ids = [
            fid
            for (fid,) in db.session.query(Folder.id)
            .join(FolderRole, FolderRole.folder_id == Folder.id)
            .filter(
                FolderRole.role == FolderRole.ROLE_COLLECTION,
                Folder.owner_user_id == owner_id,
            )
            .all()
        ]

    stats_key = str(session.get("_user_id") or "anon")
    stats = _dashboard_card_stats(stats_key, tuple(sorted(folder_ids)), tuple(sorted(collection_ids)))
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
    if owner_id:
        deck_query = deck_query.filter(Folder.owner_user_id == owner_id)
    deck_rows = (
        deck_query.group_by(Folder.id, Folder.name)
        .order_by(func.coalesce(func.sum(Card.quantity), 0).desc(), Folder.name.asc())
        .all()
    )
    decks = [
        {"id": rid, "name": rname, "rows": int(rrows or 0), "qty": int(rqty or 0)}
        for (rid, rname, rrows, rqty) in deck_rows
    ]
    collection_qty = stats["collection_qty"]

    deck_vms: list[DeckVM] = []
    placeholder_thumb = static_url("img/card-placeholder.svg")

    def ci_html_from_letters(letters: str) -> str:
        if not letters:
            return f'<span class="pip-row"><img class="mana mana-sm" src="{static_url("symbols/C.svg")}" alt="{{C}}"></span>'
        return (
            '<span class="pip-row">'
            + "".join(
                f'<img class="mana mana-sm" src="{static_url(f"symbols/{c}.svg")}" alt="{{{c}}}">' for c in letters
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
                    is_owner=bool(f and current_user and getattr(current_user, "is_authenticated", False) and f.owner_user_id == current_user.id),
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

    deck_count = len(deck_vms)
    def _format_stat(value: int | None) -> str:
        if value is None:
            return "—"
        return f"{value:,}"

    deck_tiles = [
        DashboardStatTileVM(
            label="Decks",
            value=_format_stat(deck_count),
            href=url_for("views.decks_overview"),
            icon="bi bi-collection",
        ),
        DashboardStatTileVM(
            label="Total Cards",
            value=_format_stat(total_qty),
            href=url_for("views.list_cards"),
            icon="bi bi-stack",
        ),
        DashboardStatTileVM(
            label="Collection Cards",
            value=_format_stat(collection_qty),
            href=url_for("views.collection_overview"),
            icon="bi bi-box-seam",
        ),
        DashboardStatTileVM(
            label="Sets",
            value=_format_stat(set_count),
            href=url_for("views.sets_overview"),
            icon="bi bi-grid-3x3-gap",
        ),
    ]

    collection_tiles = [
        DashboardStatTileVM(
            label="Collection Cards",
            value=_format_stat(collection_qty),
            href=url_for("views.collection_overview"),
            icon="bi bi-box-seam",
        ),
        DashboardStatTileVM(
            label="Total Cards",
            value=_format_stat(total_qty),
            href=url_for("views.list_cards"),
            icon="bi bi-stack",
        ),
        DashboardStatTileVM(
            label="Unique Cards",
            value=_format_stat(unique_names),
            href=url_for("views.list_cards"),
            icon="bi bi-card-list",
        ),
        DashboardStatTileVM(
            label="Sets",
            value=_format_stat(set_count),
            href=url_for("views.sets_overview"),
            icon="bi bi-grid-3x3-gap",
        ),
    ]


    collection_actions = [
        DashboardActionVM(
            label="Collection",
            href=url_for("views.collection_overview"),
            icon="bi bi-box-seam",
        ),
        DashboardActionVM(
            label="Browse Cards",
            href=url_for("views.list_cards"),
            icon="bi bi-stack",
        ),
        DashboardActionVM(
            label="Sets",
            href=url_for("views.sets_overview"),
            icon="bi bi-grid-3x3-gap",
        ),
        DashboardActionVM(
            label="Import CSV",
            href=url_for("views.import_csv"),
            icon="bi bi-file-earmark-arrow-up",
        ),
        DashboardActionVM(
            label="Wishlist",
            href=url_for("views.wishlist"),
            icon="bi bi-heart",
        ),
        DashboardActionVM(
            label="List Checker",
            href=url_for("views.list_checker"),
            icon="bi bi-list-check",
        ),
    ]

    deck_actions = [
        DashboardActionVM(
            label="Opening Hand",
            href=url_for("views.opening_hand"),
            icon="bi bi-hand-thumbs-up",
        ),
        DashboardActionVM(
            label="Deck Tokens",
            href=url_for("views.deck_tokens_overview"),
            icon="bi bi-layers",
        ),
        DashboardActionVM(
            label="Commander Bracket",
            href=url_for("views.commander_brackets_info"),
            icon="bi bi-trophy",
        ),
        DashboardActionVM(
            label="Spellbook Combos",
            href=url_for("views.commander_spellbook_combos"),
            icon="bi bi-lightning-charge",
        ),
    ]

    mode_options = [
        DashboardModeOptionVM(
            value=mode_key,
            label=_DASHBOARD_MODES[mode_key]["label"],
            selected=mode_key == mode,
        )
        for mode_key in _DASHBOARD_MODE_SEQUENCE
    ]

    dashboard_vm = DashboardViewModel(
        mode=mode,
        mode_label=mode_meta["label"],
        mode_description=mode_meta["description"],
        content_partial=mode_meta["partial"],
        mode_options=mode_options,
        collection_tiles=collection_tiles,
        deck_tiles=deck_tiles,
        collection_actions=collection_actions,
        deck_actions=deck_actions,
        decks=deck_vms,
    )

    return render_template("dashboard.html", dashboard=dashboard_vm)


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
    is_authenticated = bool(current_user and getattr(current_user, "is_authenticated", False))
    show_friends_arg = (request.args.get("show_friends") or "").strip().lower()
    show_friends = show_friends_arg in {"1", "true", "yes", "on", "y"}
    if not is_authenticated:
        show_friends = False

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
    if is_authenticated:
        if show_friends:
            friend_ids = (
                db.session.query(UserFriend.friend_user_id)
                .filter(UserFriend.user_id == current_user.id)
            )
            query = query.filter(
                Card.folder.has(
                    or_(
                        Folder.owner_user_id == current_user.id,
                        Folder.owner_user_id.in_(friend_ids),
                    )
                )
            )
        else:
            query = query.filter(Card.folder.has(Folder.owner_user_id == current_user.id))
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
                show_friends=show_friends,
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
        order_col = Card.id
    elif sort == "folder":
        query = query.outerjoin(Folder, Folder.id == Card.folder_id)
        order_col = func.lower(Folder.name)
    elif sort == "owner":
        query = query.outerjoin(Folder, Folder.id == Card.folder_id)
        query = query.outerjoin(User, User.id == Folder.owner_user_id)
        order_col = func.lower(
            func.coalesce(
                User.display_name,
                User.username,
                User.email,
                Folder.owner,
                "",
            )
        )
    else:
        order_col = func.lower(Card.name)

    ordered_ids: list[int] = []
    total = 0
    cards: list[Card] = []
    if sort in {"price", "art"}:
        all_cards = (
            query.order_by(Card.id.asc())
            .options(load_only(*card_columns))
            .all()
        )
        total = len(all_cards)

        if not sc.cache_ready():
            sc.ensure_cache_loaded()
        full_print_map = _bulk_print_lookup(all_cards)

        if sort == "price":
            price_values = {}
            for c in all_cards:
                pr = full_print_map.get(c.id, {})
                prices = _prices_for_print_exact(pr) if pr else {}
                price_values[c.id] = _price_value_from_prices(prices, bool(c.is_foil))

            def _price_sort_key(card):
                value = price_values.get(card.id)
                missing = value is None
                if reverse:
                    return (missing, -(value or 0.0))
                return (missing, value or 0.0)

            ordered_ids = [card.id for card in sorted(all_cards, key=_price_sort_key)]
        else:
            art_missing = {}
            for c in all_cards:
                pr = full_print_map.get(c.id, {})
                img_package = sc.image_for_print(pr) if pr else {}
                thumb_src = img_package.get("small") or img_package.get("normal") or img_package.get("large")
                if not thumb_src:
                    thumb_src = _image_from_print(pr)
                art_missing[c.id] = 0 if thumb_src else 1

            ordered_ids = [
                card.id
                for card in sorted(
                    all_cards,
                    key=lambda card: art_missing.get(card.id, 1),
                    reverse=reverse,
                )
            ]

        pages = max(1, ceil(total / per)) if per else 1
        page = min(page, pages)
        start = (page - 1) * per + 1 if total else 0
        end = min(start + per - 1, total) if total else 0
        offset = (page - 1) * per
        page_ids = ordered_ids[offset: offset + per]
        if page_ids:
            page_cards = (
                query.options(
                    load_only(*card_columns),
                    selectinload(Card.folder).load_only(
                        Folder.id,
                        Folder.name,
                        Folder.category,
                        Folder.is_proxy,
                        Folder.owner_user_id,
                        Folder.owner,
                    ),
                )
                .filter(Card.id.in_(page_ids))
                .all()
            )
            page_map = {c.id: c for c in page_cards}
            cards = [page_map[card_id] for card_id in page_ids if card_id in page_map]
    else:
        order_expr = order_col.desc() if reverse else order_col.asc()
        total = query.order_by(None).count()
        pages = max(1, ceil(total / per)) if per else 1
        page = min(page, pages)
        start = (page - 1) * per + 1 if total else 0
        end = min(start + per - 1, total) if total else 0
        offset = (page - 1) * per
        cards = (
            query.options(
                load_only(*card_columns),
                selectinload(Card.folder).load_only(
                    Folder.id,
                    Folder.name,
                    Folder.category,
                    Folder.is_proxy,
                    Folder.owner_user_id,
                    Folder.owner,
                ),
            )
            .order_by(order_expr, Card.id.asc())
            .limit(per)
            .offset(offset)
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

    current_user_id = current_user.id if is_authenticated else None
    owner_label_map: dict[int, str] = {}
    owner_ids: set[int] = set()
    for c in cards:
        folder = getattr(c, "folder", None)
        owner_id = getattr(folder, "owner_user_id", None)
        if isinstance(owner_id, int):
            owner_ids.add(owner_id)
    if owner_ids:
        owner_rows = (
            db.session.query(User.id, User.display_name, User.username, User.email)
            .filter(User.id.in_(owner_ids))
            .all()
        )
        for uid, display_name, username, email in owner_rows:
            label = display_name or username or email
            if label:
                owner_label_map[uid] = label

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
        owner_label = None
        if getattr(c, "folder", None):
            folder_ref = FolderRefVM(id=c.folder.id, name=c.folder.name)
            owner_id = getattr(c.folder, "owner_user_id", None)
            if owner_id is not None:
                if current_user_id and owner_id == current_user_id:
                    owner_label = "You"
                else:
                    owner_label = owner_label_map.get(owner_id)
            owner_label = owner_label or getattr(c.folder, "owner", None)
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
                owner_label=owner_label,
            )
        )

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
        show_friends=show_friends,
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
    category_labels = {
        Folder.CATEGORY_DECK: "Deck",
        Folder.CATEGORY_COLLECTION: "Collection",
    }
    friend_rows = (
        UserFriend.query.options(selectinload(UserFriend.friend))
        .join(User, User.id == UserFriend.friend_user_id)
        .filter(UserFriend.user_id == current_user.id)
        .order_by(func.lower(User.username))
        .all()
    )
    friends = []
    friend_ids: list[int] = []
    for friendship in friend_rows:
        user = friendship.friend
        if not user:
            continue
        label = user.display_name or user.username or user.email
        friends.append(
            {
                "user_id": user.id,
                "label": label,
                "email": user.email,
            }
        )
        friend_ids.append(user.id)

    incoming_rows = (
        UserFriendRequest.query.options(selectinload(UserFriendRequest.requester))
        .join(User, User.id == UserFriendRequest.requester_user_id)
        .filter(UserFriendRequest.recipient_user_id == current_user.id)
        .order_by(UserFriendRequest.created_at.desc())
        .all()
    )
    incoming_requests = []
    for req in incoming_rows:
        user = req.requester
        if not user:
            continue
        label = user.display_name or user.username or user.email
        incoming_requests.append(
            {
                "id": req.id,
                "user_id": user.id,
                "label": label,
                "email": user.email,
            }
        )

    outgoing_rows = (
        UserFriendRequest.query.options(selectinload(UserFriendRequest.recipient))
        .join(User, User.id == UserFriendRequest.recipient_user_id)
        .filter(UserFriendRequest.requester_user_id == current_user.id)
        .order_by(UserFriendRequest.created_at.desc())
        .all()
    )
    outgoing_requests = []
    for req in outgoing_rows:
        user = req.recipient
        if not user:
            continue
        label = user.display_name or user.username or user.email
        outgoing_requests.append(
            {
                "id": req.id,
                "user_id": user.id,
                "label": label,
                "email": user.email,
            }
        )

    incoming_card_rows = (
        FriendCardRequest.query.options(
            selectinload(FriendCardRequest.requester),
            selectinload(FriendCardRequest.wishlist_item),
        )
        .join(User, User.id == FriendCardRequest.requester_user_id)
        .filter(FriendCardRequest.recipient_user_id == current_user.id)
        .filter(FriendCardRequest.status == "pending")
        .order_by(FriendCardRequest.created_at.desc())
        .all()
    )
    incoming_card_requests = []
    for req in incoming_card_rows:
        user = req.requester
        label = None
        if user:
            label = user.display_name or user.username or user.email
        item = req.wishlist_item
        card_name = item.name if item else "Unknown card"
        incoming_card_requests.append(
            {
                "id": req.id,
                "label": label or "Unknown",
                "email": user.email if user else None,
                "card_name": card_name,
                "qty": req.requested_qty,
            }
        )

    outgoing_card_rows = (
        FriendCardRequest.query.options(
            selectinload(FriendCardRequest.recipient),
            selectinload(FriendCardRequest.wishlist_item),
        )
        .join(User, User.id == FriendCardRequest.recipient_user_id)
        .filter(FriendCardRequest.requester_user_id == current_user.id)
        .order_by(FriendCardRequest.created_at.desc())
        .all()
    )
    outgoing_card_requests = []
    for req in outgoing_card_rows:
        user = req.recipient
        label = None
        if user:
            label = user.display_name or user.username or user.email
        item = req.wishlist_item
        card_name = item.name if item else "Unknown card"
        outgoing_card_requests.append(
            {
                "id": req.id,
                "label": label or "Unknown",
                "email": user.email if user else None,
                "card_name": card_name,
                "qty": req.requested_qty,
                "status": req.status,
            }
        )

    friend_entries: list[SharedFolderEntryVM] = []
    friend_folder_ids: set[int] = set()
    if friend_ids:
        friend_folders = (
            Folder.query.options(selectinload(Folder.owner_user))
            .filter(Folder.owner_user_id.in_(friend_ids))
            .order_by(func.lower(Folder.name))
            .all()
        )
        for folder in friend_folders:
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
                is_proxy=bool(getattr(folder, "is_proxy", False)),
                is_public=bool(getattr(folder, "is_public", False)),
                deck_tag=folder.deck_tag,
                deck_tag_label=folder.deck_tag,
                commander_name=folder.commander_name,
                commander_oracle_id=folder.commander_oracle_id,
                commander_slot_count=len(folder.commander_name.split("//")) if folder.commander_name else 0,
            )
            friend_entries.append(
                SharedFolderEntryVM(
                    folder=folder_vm,
                    owner_label=owner_label or "Unknown",
                )
            )
            if folder.id is not None:
                friend_folder_ids.add(folder.id)

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
    for share in shared_rows:
        folder = share.folder
        if folder and folder.id in friend_folder_ids:
            continue
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
        elif folder.id not in shared_ids and folder.id not in friend_folder_ids:
            other_public.append(folder_vm)

    return render_template(
        "cards/shared_folders.html",
        shared_with_me=shared_with_me,
        friend_folders=friend_entries,
        friends=friends,
        incoming_requests=incoming_requests,
        outgoing_requests=outgoing_requests,
        incoming_card_requests=incoming_card_requests,
        outgoing_card_requests=outgoing_card_requests,
        my_public_folders=my_public,
        other_public_folders=other_public,
    )


def shared_follow():
    action = (request.form.get("action") or "").strip().lower()
    def _ensure_friendship(user_id: int, friend_id: int) -> None:
        if not UserFriend.query.filter_by(user_id=user_id, friend_user_id=friend_id).first():
            db.session.add(UserFriend(user_id=user_id, friend_user_id=friend_id))
        if not UserFriend.query.filter_by(user_id=friend_id, friend_user_id=user_id).first():
            db.session.add(UserFriend(user_id=friend_id, friend_user_id=user_id))

    if action == "request":
        identifier = (request.form.get("friend_identifier") or "").strip().lower()
        if not identifier:
            flash("Enter a username or email to send a request.", "warning")
            return redirect(url_for("views.shared_folders"))
        target = (
            User.query.filter(func.lower(User.username) == identifier).first()
            or User.query.filter(func.lower(User.email) == identifier).first()
        )
        if not target:
            flash("No user found with that username or email.", "warning")
            return redirect(url_for("views.shared_folders"))
        if target.id == current_user.id:
            flash("You cannot friend yourself.", "warning")
            return redirect(url_for("views.shared_folders"))
        if UserFriend.query.filter_by(user_id=current_user.id, friend_user_id=target.id).first():
            flash("You are already friends.", "info")
            return redirect(url_for("views.shared_folders"))

        incoming = UserFriendRequest.query.filter_by(
            requester_user_id=target.id,
            recipient_user_id=current_user.id,
        ).first()
        if incoming:
            _ensure_friendship(target.id, current_user.id)
            db.session.delete(incoming)
            try:
                db.session.commit()
                flash(f"Friend request accepted for {target.username or target.email}.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Unable to accept the friend request right now.", "danger")
            return redirect(url_for("views.shared_folders"))

        existing = UserFriendRequest.query.filter_by(
            requester_user_id=current_user.id,
            recipient_user_id=target.id,
        ).first()
        if existing:
            flash("Friend request already sent.", "info")
            return redirect(url_for("views.shared_folders"))

        db.session.add(UserFriendRequest(requester_user_id=current_user.id, recipient_user_id=target.id))
        try:
            db.session.commit()
            flash(f"Friend request sent to {target.username or target.email}.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Unable to send that request right now.", "danger")
        return redirect(url_for("views.shared_folders"))

    if action == "accept":
        request_id_raw = request.form.get("request_id")
        try:
            request_id = parse_positive_int(request_id_raw, field="request id")
        except ValidationError as exc:
            log_validation_error(exc, context="shared_friend_accept")
            flash("Invalid request selection.", "warning")
            return redirect(url_for("views.shared_folders"))
        req = UserFriendRequest.query.filter_by(
            id=request_id,
            recipient_user_id=current_user.id,
        ).first()
        if not req:
            flash("Friend request not found.", "warning")
            return redirect(url_for("views.shared_folders"))
        _ensure_friendship(req.requester_user_id, req.recipient_user_id)
        db.session.delete(req)
        try:
            db.session.commit()
            flash("Friend request accepted.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Unable to accept that request right now.", "danger")
        return redirect(url_for("views.shared_folders"))

    if action == "reject":
        request_id_raw = request.form.get("request_id")
        try:
            request_id = parse_positive_int(request_id_raw, field="request id")
        except ValidationError as exc:
            log_validation_error(exc, context="shared_friend_reject")
            flash("Invalid request selection.", "warning")
            return redirect(url_for("views.shared_folders"))
        req = UserFriendRequest.query.filter_by(
            id=request_id,
            recipient_user_id=current_user.id,
        ).first()
        if req:
            db.session.delete(req)
            db.session.commit()
            flash("Friend request declined.", "info")
        return redirect(url_for("views.shared_folders"))

    if action == "cancel":
        request_id_raw = request.form.get("request_id")
        try:
            request_id = parse_positive_int(request_id_raw, field="request id")
        except ValidationError as exc:
            log_validation_error(exc, context="shared_friend_cancel")
            flash("Invalid request selection.", "warning")
            return redirect(url_for("views.shared_folders"))
        req = UserFriendRequest.query.filter_by(
            id=request_id,
            requester_user_id=current_user.id,
        ).first()
        if req:
            db.session.delete(req)
            db.session.commit()
            flash("Friend request canceled.", "info")
        return redirect(url_for("views.shared_folders"))

    if action == "remove":
        friend_id_raw = request.form.get("friend_user_id")
        try:
            friend_id = parse_positive_int(friend_id_raw, field="friend id")
        except ValidationError as exc:
            log_validation_error(exc, context="shared_friend_remove")
            flash("Invalid friend selection.", "warning")
            return redirect(url_for("views.shared_folders"))
        friendships = UserFriend.query.filter(
            or_(
                and_(UserFriend.user_id == current_user.id, UserFriend.friend_user_id == friend_id),
                and_(UserFriend.user_id == friend_id, UserFriend.friend_user_id == current_user.id),
            )
        ).all()
        if friendships:
            for friendship in friendships:
                db.session.delete(friendship)
            db.session.commit()
            flash("Friend removed.", "info")
        return redirect(url_for("views.shared_folders"))

    flash("Unknown friend action.", "warning")
    return redirect(url_for("views.shared_folders"))


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
    is_authenticated = bool(current_user and getattr(current_user, "is_authenticated", False))
    show_friends_arg = (request.args.get("show_friends") or "").strip().lower()
    show_friends = show_friends_arg in {"1", "true", "yes", "on", "y"}
    if not is_authenticated:
        show_friends = False
    owner_ids: list[int] = []
    if is_authenticated:
        owner_ids.append(current_user.id)
        if show_friends:
            friend_ids = (
                db.session.query(UserFriend.friend_user_id)
                .filter(UserFriend.user_id == current_user.id)
                .all()
            )
            owner_ids.extend([friend_id for (friend_id,) in friend_ids if friend_id])

    collection_rows = _collection_rows_with_fallback(owner_user_ids=owner_ids or None)
    folder_ids = [fid for fid, _ in collection_rows if fid is not None]
    user_key = _user_cache_key()

    if folder_ids:
        folders = Folder.query.filter(Folder.id.in_(folder_ids)).order_by(func.lower(Folder.name)).all()
    else:
        folders = []

    folder_by_id = {f.id: f for f in folders}
    owner_label_map: dict[int, str] = {}
    owner_ids_for_label = {
        folder.owner_user_id
        for folder in folders
        if isinstance(folder.owner_user_id, int)
    }
    if owner_ids_for_label:
        owner_rows = (
            db.session.query(User.id, User.display_name, User.username, User.email)
            .filter(User.id.in_(owner_ids_for_label))
            .all()
        )
        for uid, display_name, username, email in owner_rows:
            label = display_name or username or email
            if label:
                owner_label_map[uid] = label

    buckets: list[CollectionBucketVM] = []
    for fid, name in collection_rows:
        folder = folder_by_id.get(fid)
        label = folder.name if folder else (name or "Collection")
        folder_option = FolderOptionVM(id=folder.id, name=folder.name) if folder else None
        owner_label = None
        if folder:
            owner_id = folder.owner_user_id
            if is_authenticated and owner_id == current_user.id:
                owner_label = "You"
            else:
                owner_label = owner_label_map.get(owner_id)
            owner_label = owner_label or folder.owner or "Unknown"
        buckets.append(
            CollectionBucketVM(
                label=label,
                folder=folder_option,
                owner_label=owner_label,
                rows=0,
                qty=0,
            )
        )

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
        filters_key = json.dumps(
            {**filters, "folder_ids": sorted(folder_ids)},
            sort_keys=True,
            separators=(",", ":"),
        )

        def _collection_stats():
            stats_list = get_folder_stats(filters)
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
            return stats_list, total_rows, total_qty, by_set

        stats_list, total_rows, total_qty, by_set = _cache_fetch(
            f"collection_stats:{user_key}:{filters_key}",
            120,
            _collection_stats,
        )
        stats_by_id = {s["folder_id"]: {"rows": s["rows"], "qty": s["qty"]} for s in stats_list}
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
        type_cache_key = f"collection_types:{user_key}:{filters_key}:{cache_epoch()}"

        def _type_breakdown():
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
            return [(t, type_counts.get(t, 0)) for t in base_types if type_counts.get(t, 0) > 0]

        type_breakdown = _cache_fetch(type_cache_key, 300, _type_breakdown)
    else:
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
            url=url_for(
                "views.list_cards",
                type=label.lower(),
                collection=1,
                show_friends=1 if show_friends else None,
            ),
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
        show_friends=show_friends,
    )


def api_deck_insight(deck_id: int):
    folder = get_or_404(Folder, deck_id)
    cache_key = f"deck_drawer:{_user_cache_key()}:{folder.id}"
    payload = _cache_fetch(cache_key, 60, lambda: _deck_drawer_summary(folder))
    return jsonify(payload)


def decks_overview():
    """Render the deck gallery with commander thumbnails and color identity badges."""
    sort = (request.args.get("sort") or "").strip().lower()
    direction = (request.args.get("dir") or "").strip().lower() or "desc"
    reverse = direction == "desc"
    is_authenticated = bool(current_user and getattr(current_user, "is_authenticated", False))
    scope = (request.args.get("scope") or ("mine" if is_authenticated else "all")).strip().lower()
    if is_authenticated and scope not in {"mine", "friends", "all"}:
        scope = "mine"
    if not is_authenticated:
        scope = "all"

    per_raw = (request.args.get("per") or request.args.get("per_page") or "").strip().lower()
    allowed_per_page = (25, 50, 100, 250, 500)
    per = None
    if per_raw and per_raw not in {"all", "0", "-1"}:
        try:
            per = int(per_raw)
        except Exception:
            per = None
        if per not in allowed_per_page:
            per = None
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except Exception:
        page = 1

    role_filter = Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES))
    scope_filter = None
    shared_ids = None
    shared_filter = None
    if is_authenticated:
        friend_ids = (
            db.session.query(UserFriend.friend_user_id)
            .filter(UserFriend.user_id == current_user.id)
        )
        shared_ids = (
            db.session.query(FolderShare.folder_id)
            .filter(FolderShare.shared_user_id == current_user.id)
        )
        shared_filter = Folder.id.in_(shared_ids)
        if scope == "friends":
            scope_filter = Folder.owner_user_id.in_(friend_ids)
        elif scope == "all":
            scope_filter = or_(
                Folder.owner_user_id == current_user.id,
                Folder.owner_user_id.in_(friend_ids),
                shared_filter,
            )
        else:
            scope_filter = or_(Folder.owner_user_id == current_user.id, shared_filter)
    scoped_filters = [role_filter]
    if scope_filter is not None:
        scoped_filters.append(scope_filter)
    user_key = _user_cache_key()

    def _summary_payload():
        base_counts = (
            db.session.query(
                Folder.id.label("folder_id"),
                Folder.owner.label("owner"),
                Folder.owner_user_id.label("owner_user_id"),
                Folder.is_proxy.label("is_proxy"),
                func.coalesce(func.sum(Card.quantity), 0).label("qty_sum"),
            )
            .outerjoin(Card, Card.folder_id == Folder.id)
            .filter(*scoped_filters)
            .group_by(Folder.id, Folder.owner, Folder.owner_user_id, Folder.is_proxy)
            .subquery()
        )
        total_decks = db.session.query(func.count(base_counts.c.folder_id)).scalar() or 0
        shared_total = 0
        if is_authenticated and shared_ids is not None and scope in {"mine", "all"}:
            shared_total = (
                db.session.query(func.count(base_counts.c.folder_id))
                .filter(
                    base_counts.c.folder_id.in_(shared_ids),
                    base_counts.c.owner_user_id != current_user.id,
                )
                .scalar()
                or 0
            )
        if is_authenticated and scope == "friends":
            proxy_total = 0
            owned_total = 0
            friends_total = total_decks
        elif is_authenticated and scope in {"mine", "all"}:
            proxy_total = (
                db.session.query(func.count(base_counts.c.folder_id))
                .filter(
                    base_counts.c.owner_user_id == current_user.id,
                    base_counts.c.is_proxy.is_(True),
                )
                .scalar()
                or 0
            )
            owned_total = (
                db.session.query(func.count(base_counts.c.folder_id))
                .filter(
                    base_counts.c.owner_user_id == current_user.id,
                    base_counts.c.is_proxy.is_(False),
                )
                .scalar()
                or 0
            )
            if scope == "all":
                friends_total = total_decks - owned_total - proxy_total - shared_total
            else:
                friends_total = 0
        else:
            proxy_total = (
                db.session.query(func.count(base_counts.c.folder_id))
                .filter(base_counts.c.is_proxy.is_(True))
                .scalar()
                or 0
            )
            owned_total = total_decks - proxy_total
            friends_total = 0
        owner_rows = db.session.query(base_counts.c.owner).group_by(base_counts.c.owner).all()
        owner_names = sorted(
            {
                owner.strip()
                for (owner,) in owner_rows
                if isinstance(owner, str) and owner.strip()
            }
        )
        return total_decks, proxy_total, owned_total, friends_total, shared_total, owner_names

    summary_cache_key = f"deck_summary:v2:{user_key}:{scope}"
    total_decks, proxy_total, owned_total, friends_total, shared_total, owner_names = _cache_fetch(
        summary_cache_key,
        120,
        _summary_payload,
    )
    total_decks = int(total_decks or 0)
    proxy_total = int(proxy_total or 0)
    owned_total = int(owned_total or 0)
    friends_total = int(friends_total or 0)
    shared_total = int(shared_total or 0)

    if per is None:
        per = total_decks if total_decks else 1
    pages = max(1, ceil(total_decks / per)) if per else 1
    page = min(page, pages)
    offset = (page - 1) * per

    deck_query = (
        db.session.query(
            Folder.id,
            Folder.name,
            func.count(Card.id).label("row_count"),
            func.coalesce(func.sum(Card.quantity), 0).label("qty_sum"),
            Folder.commander_oracle_id,
            Folder.commander_name,
            Folder.owner,
            Folder.owner_user_id,
            Folder.is_proxy,
        )
        .outerjoin(Card, Card.folder_id == Folder.id)
        .filter(*scoped_filters)
    )
    grouped = deck_query.group_by(
        Folder.id,
        Folder.name,
        Folder.commander_oracle_id,
        Folder.commander_name,
        Folder.owner,
        Folder.owner_user_id,
        Folder.is_proxy,
    )

    sort_key = sort if sort in {"name", "owner", "qty", "tag", "ci", "pips", "bracket"} else ""
    requires_full_sort = sort_key in {"tag", "ci", "pips", "bracket"}

    if requires_full_sort:
        rows = grouped.all()
    else:
        if sort_key == "name":
            order_col = func.lower(Folder.name)
        elif sort_key == "owner":
            order_col = func.lower(func.coalesce(Folder.owner, ""))
        else:
            order_col = func.coalesce(func.sum(Card.quantity), 0)
        order_expr = order_col.desc() if reverse else order_col.asc()
        rows = (
            grouped.order_by(order_expr, Folder.id.asc())
            .limit(per)
            .offset(offset)
            .all()
        )

    owner_user_ids = {owner_user_id for _fid, _name, _rows, _qty, _cmd_oid, _cmd_name, _owner, owner_user_id, _is_proxy in rows if owner_user_id}
    owner_user_labels = {}
    if owner_user_ids:
        owner_rows = (
            db.session.query(User.id, User.display_name, User.username, User.email)
            .filter(User.id.in_(owner_user_ids))
            .all()
        )
        for uid, display_name, username, email in owner_rows:
            label = display_name or username or email
            if label:
                owner_user_labels[uid] = label

    # normalize for the template
    decks = []
    for fid, name, _rows, qty, cmd_oid, cmd_name, owner, owner_user_id, is_proxy in rows:
        raw_owner = (owner or "").strip()
        owner_label = (owner_user_labels.get(owner_user_id) or "").strip()
        if owner_user_id and not owner_label:
            owner_label = raw_owner or "Unknown User"
        if not owner_user_id:
            owner_label = raw_owner or "Unassigned"
        owner_display = raw_owner or (owner_label if owner_label != "Unassigned" else "")
        if owner_user_id:
            owner_key = f"user:{owner_user_id}"
        else:
            owner_key = f"owner:{(raw_owner.lower() if raw_owner else 'unassigned')}"
        decks.append({
            "id": fid,
            "name": name,
            "qty": int(qty or 0),
            "commander_oid": cmd_oid,
            "commander_name": cmd_name,
            "owner": owner_display or None,
            "owner_label": owner_label,
            "owner_key": owner_key,
            "owner_user_id": owner_user_id,
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
    placeholder_thumb = static_url("img/card-placeholder.svg")

    for (fid, _name, _rows, _qty, cmd_oid, cmd_name, _owner, _owner_user_id, _is_proxy) in rows:
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
    if requires_full_sort:
        if sort_key == "ci":
            decks.sort(key=lambda d: (deck_ci_name.get(d["id"]) or "Colorless"), reverse=reverse)
        elif sort_key == "pips":
            decks.sort(key=lambda d: (deck_ci_letters.get(d["id"]) or "C"), reverse=reverse)
        elif sort_key == "bracket":
            decks.sort(
                key=lambda d: (
                    deck_bracket_map.get(d["id"], {}).get("level") or 0,
                ),
                reverse=reverse,
            )
        elif sort_key == "tag":
            def _tag_sort_key(deck):
                tag = (deck.get("tag_label") or deck.get("tag") or "").strip()
                return (not tag, tag.lower())
            decks.sort(key=_tag_sort_key, reverse=reverse)
        decks = decks[offset: offset + per]

    owner_summary_raw = _owner_summary(decks)
    owner_summary = [
        DeckOwnerSummaryVM(
            key=item.get("key") or "",
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
                owner_key=(deck.get("owner_key") or (deck.get("owner") or "").strip().lower()),
                is_proxy=bool(deck.get("is_proxy")),
                is_owner=bool(
                    current_user
                    and getattr(current_user, "is_authenticated", False)
                    and deck.get("owner_user_id") == current_user.id
                ),
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

    deck_tag_groups = get_deck_tag_groups()

    def _wizard_payload():
        wizard_query = (
            Folder.query.options(
                load_only(
                    Folder.id,
                    Folder.name,
                    Folder.commander_name,
                    Folder.commander_oracle_id,
                    Folder.deck_tag,
                ),
                selectinload(Folder.role_entries),
            )
            .filter(role_filter)
        )
        if current_user and getattr(current_user, "is_authenticated", False):
            wizard_query = wizard_query.filter(Folder.owner_user_id == current_user.id)
        wizard_folders = wizard_query.all()
        return build_deck_metadata_wizard_payload(wizard_folders, tag_groups=deck_tag_groups)

    wizard_payload = _cache_fetch(f"deck_wizard:{user_key}", 120, _wizard_payload)

    def _url_with(page_num: int):
        args = request.args.to_dict(flat=False)
        args["page"] = [str(page_num)]
        if "per" not in args and "per_page" not in args:
            args["per"] = [str(per)]
        return url_for("views.decks_overview", **{k: v if len(v) > 1 else v[0] for k, v in args.items()})

    page_urls = [(n, _url_with(n)) for n in range(1, pages + 1)]
    page_url_map = {n: url for n, url in page_urls}

    return render_template(
        "decks/decks.html",
        decks=deck_vms,
        owner_summary=owner_summary,
        owner_names=owner_names,
        proxy_count=sum(1 for deck in decks if deck.get("is_proxy")),
        proxy_total=proxy_total,
        owned_total=owned_total,
        friends_total=friends_total,
        shared_total=shared_total,
        total_decks=total_decks,
        scope=scope,
        show_scope_toggle=is_authenticated,
        page=page,
        pages=pages,
        per_page=per,
        page_url_map=page_url_map,
        deck_tag_groups=deck_tag_groups,
        deck_metadata_wizard=wizard_payload,
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

    print_cache_by_oracle = {}
    print_cache_by_setcn = {}
    image_cache = {}
    token_cache_by_oracle: dict[str, list[dict]] = {}

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
        if not src_img_url and name:
            src_img_url = (
                "https://api.scryfall.com/cards/named?format=image&version=normal&exact="
                + quote(name)
            )

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
            imgs = token.get("images") or {}

            entry = tokens_by_key.setdefault(
                token_key,
                {
                    "id": token_id,
                    "name": token_name,
                    "type_line": token_type or "Token",
                    "small": imgs.get("small"),
                    "normal": imgs.get("normal"),
                    "sources": [],
                    "decks": {},
                    "total_qty": 0,
                },
            )
            if entry.get("id") is None and token_id:
                entry["id"] = token_id
            if not entry.get("small") and imgs.get("small"):
                entry["small"] = imgs.get("small")
            if not entry.get("normal") and imgs.get("normal"):
                entry["normal"] = imgs.get("normal")
            if (entry.get("name") or "").lower() == "token" and token_name.lower() != "token":
                entry["name"] = token_name
            if not entry.get("type_line") or entry.get("type_line") == "Token":
                if token_type:
                    entry["type_line"] = token_type

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

    tokens_raw.sort(key=lambda tok: (tok["name"] or "").lower())

    token_vms: list[DeckTokenVM] = []
    for entry in tokens_raw:
        deck_ids = [deck.get("deck_id") for deck in entry.get("decks") or [] if deck.get("deck_id") is not None]
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
                deck_ids_csv=",".join(str(did) for did in deck_ids),
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
    owner_options = [
        {"id": owner_id, "label": owner_options_map[owner_id]}
        for owner_id in owner_options_map
    ]
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


def _opening_hand_deck_options() -> tuple[dict[str, dict], list[dict]]:
    role_filter = Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES))
    deck_query = Folder.query.filter(role_filter)
    if current_user and getattr(current_user, "is_authenticated", False):
        friend_ids = (
            db.session.query(UserFriend.friend_user_id)
            .filter(UserFriend.user_id == current_user.id)
        )
        shared_ids = (
            db.session.query(FolderShare.folder_id)
            .filter(FolderShare.shared_user_id == current_user.id)
        )
        deck_query = deck_query.filter(
            or_(
                Folder.owner_user_id == current_user.id,
                Folder.owner_user_id.in_(friend_ids),
                Folder.id.in_(shared_ids),
            )
        )
    else:
        deck_query = deck_query.filter(text("1=0"))
    decks = deck_query.order_by(Folder.name.asc()).all()
    deck_lookup: dict[str, dict] = {}
    deck_options: list[dict] = []

    for deck in decks:
        key = str(deck.id)
        label = deck.name or f"Deck {deck.id}"
        deck_lookup[key] = {"source": "folder", "deck": deck, "label": label}
        deck_options.append({"id": key, "name": label})

    build_sessions: list[BuildSession] = []
    if current_user.is_authenticated:
        build_sessions = (
            BuildSession.query.filter_by(owner_user_id=current_user.id, status="active")
            .order_by(BuildSession.updated_at.desc(), BuildSession.created_at.desc())
            .all()
        )
    for session in build_sessions:
        key = _opening_hand_build_key(session.id)
        label = _opening_hand_build_label(session)
        deck_lookup[key] = {"source": "build", "deck": session, "label": label}
        deck_options.append({"id": key, "name": label})

    return deck_lookup, deck_options


def _opening_hand_lookups(deck_refs: Iterable[str]) -> tuple[str, str]:
    deck_card_lookup: dict[str, list[dict]] = {}
    deck_token_lookup: dict[str, list[dict]] = {}
    raw_refs = [str(deck_ref).strip() for deck_ref in (deck_refs or []) if deck_ref]
    normalized_refs: list[str] = []
    folder_ids: list[int] = []
    build_ids: list[int] = []

    for raw in raw_refs:
        try:
            parsed = _parse_opening_hand_deck_ref(raw)
        except ValidationError:
            continue
        if not parsed:
            continue
        source, deck_id = parsed
        key = _opening_hand_deck_key(source, deck_id)
        if key not in normalized_refs:
            normalized_refs.append(key)
        if source == "build":
            build_ids.append(deck_id)
        else:
            folder_ids.append(deck_id)

    if normalized_refs:
        have_cache = _ensure_cache_ready()
        token_cache: dict[str, list[dict]] = {}
        placeholder_image = static_url("img/card-placeholder.svg")

        if folder_ids:
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
                .filter(Card.folder_id.in_(folder_ids))
                .order_by(Card.folder_id.asc(), Card.name.asc(), Card.collector_number.asc())
                .all()
            )
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
                back_imgs = _back_image_from_print(pr)
                flags = _card_type_flags(type_line)
                entry_vm = OpeningHandCardVM(
                    value=value_token,
                    name=card_name,
                    image=imgs.get("normal") or imgs.get("large") or imgs.get("small") or placeholder_image,
                    hover=imgs.get("large") or imgs.get("normal") or imgs.get("small") or placeholder_image,
                    back_image=back_imgs.get("normal") or back_imgs.get("large") or back_imgs.get("small"),
                    back_hover=back_imgs.get("large") or back_imgs.get("normal") or back_imgs.get("small"),
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

                tokens: list[dict] = []
                if have_cache and oracle_id:
                    cached_tokens = token_cache.get(oracle_id)
                    if cached_tokens is None:
                        try:
                            cached_tokens = sc.tokens_from_oracle(oracle_id) or []
                        except Exception:
                            cached_tokens = []
                        token_cache[oracle_id] = cached_tokens
                    tokens = cached_tokens

                if not tokens:
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

        if build_ids:
            build_rows = (
                db.session.query(BuildSessionCard.session_id, BuildSessionCard.card_oracle_id)
                .join(BuildSession, BuildSessionCard.session_id == BuildSession.id)
                .filter(
                    BuildSessionCard.session_id.in_(build_ids),
                    BuildSession.owner_user_id == current_user.id,
                    BuildSession.status == "active",
                )
                .all()
            )
            seen_map: dict[str, set[str]] = {}
            token_seen: dict[str, set[str]] = {}
            for session_id, oracle_id in build_rows:
                oracle_id = (oracle_id or "").strip()
                if not oracle_id:
                    continue
                session_key = _opening_hand_build_key(session_id)
                entries = deck_card_lookup.setdefault(session_key, [])
                seen = seen_map.setdefault(session_key, set())
                if oracle_id in seen:
                    continue
                seen.add(oracle_id)

                pr = None
                try:
                    prints = prints_for_oracle(oracle_id) or []
                except Exception:
                    prints = []
                if prints:
                    pr = next((p for p in prints if not p.get("digital")), prints[0])

                imgs = _image_from_print(pr)
                back_imgs = _back_image_from_print(pr)
                type_line = (pr or {}).get("type_line") or ""
                mana_value = (pr or {}).get("cmc")
                flags = _card_type_flags(type_line)
                name = (pr or {}).get("name") or oracle_id or "Card"
                entry_vm = OpeningHandCardVM(
                    value=oracle_id,
                    name=name,
                    image=imgs.get("normal") or imgs.get("large") or imgs.get("small") or placeholder_image,
                    hover=imgs.get("large") or imgs.get("normal") or imgs.get("small") or placeholder_image,
                    back_image=back_imgs.get("normal") or back_imgs.get("large") or back_imgs.get("small"),
                    back_hover=back_imgs.get("large") or back_imgs.get("normal") or back_imgs.get("small"),
                    type_line=type_line,
                    mana_value=mana_value,
                    is_creature=bool(flags["is_creature"]),
                    is_land=bool(flags["is_land"]),
                    is_instant=bool(flags["is_instant"]),
                    is_sorcery=bool(flags["is_sorcery"]),
                    is_permanent=bool(flags["is_permanent"]),
                    zone_hint=str(flags["zone_hint"]),
                )
                entries.append(entry_vm.to_payload())

                tokens: list[dict] = []
                if have_cache and oracle_id:
                    cached_tokens = token_cache.get(oracle_id)
                    if cached_tokens is None:
                        try:
                            cached_tokens = sc.tokens_from_oracle(oracle_id) or []
                        except Exception:
                            cached_tokens = []
                        token_cache[oracle_id] = cached_tokens
                    tokens = cached_tokens

                if not tokens:
                    faces = (pr or {}).get("card_faces") or []
                    oracle_text = (pr or {}).get("oracle_text") or _oracle_text_from_faces(faces)
                    tokens = _token_stubs_from_oracle_text(oracle_text)

                if tokens:
                    token_bucket = deck_token_lookup.setdefault(session_key, [])
                    seen_tokens = token_seen.setdefault(session_key, set())
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

    for deck_key in normalized_refs:
        deck_card_lookup.setdefault(deck_key, [])
        deck_token_lookup.setdefault(deck_key, [])

    deck_card_lookup_json = json.dumps(deck_card_lookup, ensure_ascii=True)
    deck_token_lookup_json = json.dumps(deck_token_lookup, ensure_ascii=True)
    return deck_card_lookup_json, deck_token_lookup_json


def opening_hand():
    _, deck_options = _opening_hand_deck_options()
    return render_template(
        "decks/opening_hand_landing.html",
        deck_options=deck_options,
    )


def opening_hand_play():
    if request.method == "GET":
        return redirect(url_for("views.opening_hand"))

    deck_id_raw = (request.form.get("deck_id") or "").strip()
    deck_list_text = (request.form.get("deck_list") or "").strip()
    commander_hint = (request.form.get("commander_name") or "").strip()

    deck_lookup, deck_options = _opening_hand_deck_options()
    deck_key = ""
    selected_deck_name = ""
    commander_cards: list[dict] = []
    deck_refs: list[str] = []

    custom_token_entries_json = json.dumps([], ensure_ascii=True)

    if deck_id_raw:
        try:
            parsed = _parse_opening_hand_deck_ref(deck_id_raw)
        except ValidationError as exc:
            log_validation_error(exc, context="opening_hand_play")
            flash("Invalid deck selection.", "danger")
            return redirect(url_for("views.opening_hand"))
        if parsed:
            source, deck_id = parsed
            deck_key = _opening_hand_deck_key(source, deck_id)
            selected = deck_lookup.get(deck_key)
            if not selected:
                flash("Deck not found.", "warning")
                return redirect(url_for("views.opening_hand"))
            selected_deck_name = selected.get("label") or "Deck"
            deck_list_text = ""
            commander_hint = ""
            deck_refs = [deck_key]
            if source == "build":
                session = selected.get("deck")
                commander_cards = _commander_card_payloads(
                    session.commander_name,
                    session.commander_oracle_id,
                )
            else:
                selected_deck = selected.get("deck")
                commander_cards = _commander_card_payloads(
                    selected_deck.commander_name,
                    selected_deck.commander_oracle_id,
                )
    elif deck_list_text:
        selected_deck_name = "Custom list"
        _, entries_from_list, _, commander_cards = _deck_entries_from_list(deck_list_text, commander_hint)
        oracle_ids = {
            entry.get("oracle_id")
            for entry in entries_from_list
            if entry.get("oracle_id")
        }
        for cmd in commander_cards:
            cmd_oid = cmd.get("oracle_id")
            if cmd_oid:
                oracle_ids.add(cmd_oid)
        if oracle_ids and _ensure_cache_ready():
            placeholder_image = static_url("img/card-placeholder.svg")
            token_seen: set[str] = set()
            token_payloads: list[dict] = []
            for oracle_id in sorted(oracle_ids):
                try:
                    tokens = sc.tokens_from_oracle(oracle_id) or []
                except Exception:
                    tokens = []
                for token in tokens:
                    token_name = (token.get("name") or "Token").strip()
                    token_type = (token.get("type_line") or "").strip()
                    token_id = token.get("id")
                    token_key = token_id or f"{token_name.lower()}|{token_type.lower()}"
                    if token_key in token_seen:
                        continue
                    token_seen.add(token_key)
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
                    token_payloads.append(token_vm.to_payload())
            token_payloads.sort(key=lambda item: (item.get("name") or "").lower())
            custom_token_entries_json = json.dumps(token_payloads, ensure_ascii=True)
    else:
        flash("Select a deck or paste a deck list to continue.", "warning")
        return redirect(url_for("views.opening_hand"))

    deck_card_lookup_json, deck_token_lookup_json = _opening_hand_lookups(deck_refs)
    placeholder = static_url("img/card-placeholder.svg")
    commander_payload = [_client_card_payload(card, placeholder) for card in commander_cards]
    selected_commander_cards_json = json.dumps(commander_payload, ensure_ascii=True)

    return render_template(
        "decks/opening_hand.html",
        deck_options=deck_options,
        deck_card_lookup_json=deck_card_lookup_json,
        deck_token_lookup_json=deck_token_lookup_json,
        selected_deck_id=deck_key,
        selected_deck_name=selected_deck_name,
        selected_deck_list=deck_list_text,
        selected_commander_name=commander_hint,
        selected_commander_cards_json=selected_commander_cards_json,
        custom_token_entries_json=custom_token_entries_json,
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

    deck_source = None
    deck_id = None
    if deck_id_raw not in (None, "", False):
        try:
            parsed = _parse_opening_hand_deck_ref(deck_id_raw)
        except ValidationError as exc:
            log_validation_error(exc, context="opening_hand_shuffle")
            return jsonify({"ok": False, "error": "Invalid deck selection."}), 400
        if parsed:
            deck_source, deck_id = parsed

    if deck_id:
        if deck_source == "build":
            deck_name, entries, warnings, commander_cards = _deck_entries_from_build_session(deck_id)
        else:
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
        "user_id": current_user.id if current_user and getattr(current_user, "is_authenticated", False) else None,
    }
    state_token = _encode_state(state)
    remaining = deck_size - next_index
    placeholder = static_url("img/card-placeholder.svg")
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

    deck = state["deck"]
    index = state["index"]
    deck_name = state["deck_name"]

    if index >= len(deck):
        return jsonify({"ok": False, "error": "No more cards to draw.", "remaining": 0, "deck_name": deck_name, "state": token})

    card_entry = deck[index]
    index += 1
    state["index"] = index
    new_token = _encode_state(state)
    remaining = len(deck) - index
    placeholder = static_url("img/card-placeholder.svg")
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


def opening_hand_token_search():
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"ok": True, "tokens": []})

    if not _ensure_cache_ready():
        return jsonify({"ok": False, "error": "Token search is unavailable."}), 503

    def _token_search():
        try:
            return sc.search_tokens(query, limit=36) or []
        except Exception:
            return []

    tokens = _cache_fetch(f"token_search:{query.lower()}", 300, _token_search)

    placeholder = static_url("img/card-placeholder.svg")
    payloads: list[dict] = []
    for token in tokens:
        token_name = (token.get("name") or "Token").strip()
        token_type = (token.get("type_line") or "").strip()
        token_id = token.get("id")
        token_imgs = token.get("images") or {}
        token_flags = _card_type_flags(token_type)
        token_vm = OpeningHandTokenVM(
            id=token_id,
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
        )
        payloads.append(token_vm.to_payload())

    return jsonify({"ok": True, "tokens": payloads})


def _facets():
    user_key = _user_cache_key()

    def _build():
        sets = [s for (s,) in db.session.query(Card.set_code).distinct().order_by(Card.set_code.asc()).all() if s]
        langs = [lg for (lg,) in db.session.query(Card.lang).distinct().order_by(Card.lang.asc()).all() if lg]
        folders = db.session.query(Folder.id, Folder.name).order_by(Folder.name.asc()).all()
        return sets, langs, folders

    return _cache_fetch(f"facets:{user_key}", 300, _build)


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
    core_role_labels = _request_cached_core_role_labels(oid)
    if core_role_labels:
        if not role_labels:
            role_labels = core_role_labels
        else:
            for label in core_role_labels:
                if label not in role_labels:
                    role_labels.append(label)
    if not primary_role_label:
        primary_role_label = _request_cached_primary_oracle_role_label(oid)

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
    "opening_hand_play",
    "opening_hand_shuffle",
    "opening_hand_draw",
    "opening_hand_token_search",
    "decks_overview",
    "list_cards",
    "shared_folders",
    "shared_follow",
]
