"""Wishlist display and collection crosswalk helpers."""

from __future__ import annotations

from collections import defaultdict

from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from extensions import db
from models import Card, Folder, FolderRole, UserFriend
from core.domains.cards.services import scryfall_cache as sc


def color_identity_for_oracle(oracle_id):
    if not oracle_id:
        return None
    try:
        prints = sc.prints_for_oracle(oracle_id)
    except Exception:
        return None
    if not prints:
        return None
    color_identity = prints[0].get("color_identity") or prints[0].get("colors")
    if isinstance(color_identity, list):
        color_identity = "".join(color_identity)
    return color_identity or None


def color_identity_for_item(item):
    card = getattr(item, "card", None)
    if card:
        direct = card.color_identity or card.colors or None
        if direct:
            return direct
    oracle_id = item.oracle_id
    color_identity = color_identity_for_oracle(oracle_id)
    if color_identity:
        return color_identity
    try:
        oracle_id = sc.unique_oracle_by_name(item.name)
    except Exception:
        oracle_id = None
    if oracle_id:
        return color_identity_for_oracle(oracle_id)
    return None


def type_line_for_oracle(oracle_id):
    if not oracle_id:
        return None
    try:
        prints = sc.prints_for_oracle(oracle_id)
    except Exception:
        return None
    if not prints:
        return None
    type_line = prints[0].get("type_line")
    if not type_line:
        return None
    return str(type_line).strip() or None


def type_line_for_item(item):
    card = getattr(item, "card", None)
    if card and getattr(card, "type_line", None):
        return card.type_line
    type_line = type_line_for_oracle(item.oracle_id)
    if type_line:
        return type_line
    try:
        oracle_id = sc.unique_oracle_by_name(item.name)
    except Exception:
        oracle_id = None
    if oracle_id:
        return type_line_for_oracle(oracle_id)
    return None


def format_color_identity(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        tokens = [str(token).strip().upper() for token in value if str(token).strip()]
        letters = [token for token in tokens if token in {"W", "U", "B", "R", "G"}]
        if letters:
            return "".join(letters)
        if "C" in tokens:
            return "C"
        return "".join(tokens)
    text = str(value).strip().upper()
    if not text:
        return ""
    letters = [ch for ch in text if ch in "WUBRGC"]
    if letters:
        if any(ch in "WUBRG" for ch in letters):
            return "".join([ch for ch in letters if ch in "WUBRG"])
        return "C"
    return text


def split_folder_label(raw_name):
    text = (str(raw_name) if raw_name is not None else "").strip()
    if not text:
        return None, ""
    if ":" in text:
        owner_part, folder_part = text.split(":", 1)
        owner_part = owner_part.strip()
        folder_part = folder_part.strip()
        return owner_part or None, folder_part
    return None, text


def folder_is_collection(folder):
    if not folder:
        return False
    try:
        if folder.is_collection:
            return True
    except Exception:
        pass
    return ((getattr(folder, "category", None) or "").strip().lower() == Folder.CATEGORY_COLLECTION)


def _collection_folder_meta(folder_names, current_user_id, friend_ids):
    meta = {}
    if not folder_names:
        return meta
    lower_names = {name.lower() for name in folder_names if name}
    if not lower_names:
        return meta
    query = (
        Folder.query.options(selectinload(Folder.owner_user))
        .join(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(FolderRole.role == FolderRole.ROLE_COLLECTION)
        .filter(func.lower(Folder.name).in_(lower_names))
    )
    if current_user_id:
        owner_ids = {current_user_id} | set(friend_ids or [])
        query = query.filter(Folder.owner_user_id.in_(owner_ids))
    else:
        query = query.filter(Folder.is_public.is_(True))
    for folder in query.all():
        name_key = (folder.name or "").strip().lower()
        if not name_key:
            continue
        entry = meta.setdefault(
            name_key,
            {"labels": set(), "has_user": False, "has_friend": False},
        )
        owner_id = folder.owner_user_id
        owner_label = None
        if folder.owner_user:
            owner_label = folder.owner_user.display_name or folder.owner_user.username or folder.owner_user.email
        if not owner_label:
            owner_label = folder.owner
        if owner_label:
            entry["labels"].add(owner_label)
        if current_user_id and owner_id == current_user_id:
            entry["has_user"] = True
        elif owner_id in (friend_ids or set()):
            entry["has_friend"] = True
    return meta


def build_wishlist_source_entries(items):
    current_user_id = current_user.id if current_user.is_authenticated else None
    friend_ids = set()
    if current_user_id:
        friend_rows = (
            db.session.query(UserFriend.friend_user_id)
            .filter(UserFriend.user_id == current_user_id)
            .all()
        )
        friend_ids = {friend_id for (friend_id,) in friend_rows if friend_id}

    name_candidates = set()
    for item in items:
        for entry in item.source_folders_list:
            raw_name = entry.get("name") if isinstance(entry, dict) else ""
            _owner_hint, folder_name = split_folder_label(raw_name)
            if folder_name:
                name_candidates.add(folder_name)

    meta = _collection_folder_meta(name_candidates, current_user_id, friend_ids)

    entries_by_item = []
    max_sources = 0
    for item in items:
        entries = []
        for entry in item.source_folders_list:
            raw_name = entry.get("name") if isinstance(entry, dict) else ""
            owner_hint, folder_name = split_folder_label(raw_name)
            if not folder_name:
                continue
            info = meta.get(folder_name.lower())
            if not info:
                continue
            rank = 0 if info.get("has_user") else 1 if info.get("has_friend") else 2
            label = folder_name
            if rank > 0:
                owner_label = owner_hint
                if not owner_label:
                    labels = info.get("labels") or set()
                    if len(labels) == 1:
                        owner_label = next(iter(labels))
                if owner_label:
                    label = f"{owner_label}: {folder_name}"
            entries.append({"label": label, "qty": entry.get("qty"), "rank": rank})

        if not entries:
            folder = item.card.folder if item.card and item.card.folder else None
            if folder_is_collection(folder):
                owner_id = folder.owner_user_id
                label = None
                rank = None
                if current_user_id and owner_id == current_user_id:
                    label = folder.name
                    rank = 0
                elif owner_id in friend_ids:
                    owner_label = None
                    if folder.owner_user:
                        owner_label = folder.owner_user.display_name or folder.owner_user.username or folder.owner_user.email
                    if not owner_label:
                        owner_label = folder.owner
                    label = f"{owner_label}: {folder.name}" if owner_label else folder.name
                    rank = 1
                if label:
                    entries.append({"label": label, "qty": None, "rank": rank})

        entries.sort(key=lambda entry: (entry.get("rank", 2), (entry.get("label") or "").lower()))
        entries_by_item.append(entries)
        if len(entries) > max_sources:
            max_sources = len(entries)

    return entries_by_item, max_sources


def _owner_rank(owner_user_id, current_user_id, friend_ids):
    if current_user_id and owner_user_id == current_user_id:
        return 0
    if owner_user_id in (friend_ids or set()):
        return 1
    return 2


def _folder_owner_aliases(folder):
    aliases = set()
    if not folder:
        return aliases
    if folder.owner_user:
        for value in (folder.owner_user.display_name, folder.owner_user.username, folder.owner_user.email):
            text = (value or "").strip().lower()
            if text:
                aliases.add(text)
    owner_fallback = (folder.owner or "").strip().lower()
    if owner_fallback:
        aliases.add(owner_fallback)
    return aliases


def _normalize_rarity(value):
    text = (str(value or "")).strip().lower()
    if not text:
        return None
    text = text.replace("_", " ").replace("-", " ")
    if "mythic" in text:
        return "mythic"
    if text in {"common", "uncommon", "rare", "special"}:
        return text
    return text


def _rarity_label(value):
    rarity = _normalize_rarity(value)
    if not rarity:
        return None
    if rarity == "mythic":
        return "Mythic"
    return rarity.title()


def _rarity_badge_class(value):
    rarity = _normalize_rarity(value)
    if rarity in {"common", "uncommon", "rare", "mythic"}:
        return rarity
    return None


def _collection_folders_for_names(folder_names, current_user_id, friend_ids):
    if not folder_names:
        return {}
    lower_names = {(name or "").strip().lower() for name in folder_names if (name or "").strip()}
    if not lower_names:
        return {}
    query = (
        Folder.query.options(selectinload(Folder.owner_user))
        .join(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(FolderRole.role == FolderRole.ROLE_COLLECTION)
        .filter(func.lower(Folder.name).in_(lower_names))
    )
    if current_user_id:
        owner_ids = {current_user_id} | set(friend_ids or set())
        query = query.filter(Folder.owner_user_id.in_(owner_ids))
    else:
        query = query.filter(Folder.is_public.is_(True))
    by_name = defaultdict(list)
    for folder in query.all():
        name_key = (folder.name or "").strip().lower()
        if not name_key:
            continue
        by_name[name_key].append(folder)
    for name_key, folders in by_name.items():
        by_name[name_key] = sorted(
            folders,
            key=lambda folder: (
                _owner_rank(folder.owner_user_id, current_user_id, friend_ids),
                (folder.name or "").strip().lower(),
                int(folder.id or 0),
            ),
        )
    return by_name


def _collection_card_lookup(folder_ids, oracle_ids, name_keys):
    by_folder_oracle = defaultdict(list)
    by_folder_name = defaultdict(list)
    if not folder_ids:
        return by_folder_oracle, by_folder_name

    query = (
        Card.query.options(selectinload(Card.folder).selectinload(Folder.owner_user))
        .filter(Card.folder_id.in_(folder_ids))
    )
    filters = []
    if oracle_ids:
        filters.append(Card.oracle_id.in_(oracle_ids))
    if name_keys:
        filters.append(func.lower(Card.name).in_(name_keys))
    if filters:
        query = query.filter(or_(*filters))

    for card in query.all():
        folder_id = int(card.folder_id or 0)
        if folder_id <= 0:
            continue
        oracle_id = (card.oracle_id or "").strip()
        if oracle_id:
            by_folder_oracle[(folder_id, oracle_id)].append(card)
        name_key = (card.name or "").strip().lower()
        if name_key:
            by_folder_name[(folder_id, name_key)].append(card)

    def _card_sort_key(card):
        return (
            -int(card.quantity or 0),
            (card.set_code or "").strip().lower(),
            str(card.collector_number or ""),
            int(card.id or 0),
        )

    for key, cards in by_folder_oracle.items():
        by_folder_oracle[key] = sorted(cards, key=_card_sort_key)
    for key, cards in by_folder_name.items():
        by_folder_name[key] = sorted(cards, key=_card_sort_key)
    return by_folder_oracle, by_folder_name


def _pick_collection_display_card(item, folders_by_name, cards_by_folder_oracle, cards_by_folder_name):
    folder_candidates = []
    seen_folder_ids = set()
    for entry in item.source_folders_list:
        raw_name = entry.get("name") if isinstance(entry, dict) else ""
        owner_hint, folder_name = split_folder_label(raw_name)
        if not folder_name:
            continue
        matches = list(folders_by_name.get(folder_name.lower()) or [])
        if owner_hint:
            hint = owner_hint.strip().lower()
            narrowed = [folder for folder in matches if hint in _folder_owner_aliases(folder)]
            if narrowed:
                matches = narrowed
        for folder in matches:
            folder_id = int(folder.id or 0)
            if folder_id <= 0 or folder_id in seen_folder_ids:
                continue
            seen_folder_ids.add(folder_id)
            folder_candidates.append(folder)

    linked_card = item.card if item.card and folder_is_collection(item.card.folder) else None
    if linked_card and linked_card.folder_id not in seen_folder_ids:
        folder_candidates.append(linked_card.folder)
        seen_folder_ids.add(linked_card.folder_id)

    oracle_id = (item.oracle_id or "").strip()
    name_key = (item.name or "").strip().lower()

    if oracle_id:
        for folder in folder_candidates:
            candidates = cards_by_folder_oracle.get((int(folder.id or 0), oracle_id)) or []
            if candidates:
                return candidates[0]

    if name_key:
        for folder in folder_candidates:
            candidates = cards_by_folder_name.get((int(folder.id or 0), name_key)) or []
            if candidates:
                return candidates[0]

    return linked_card


def enrich_wishlist_display_prints(items):
    if not items:
        return

    current_user_id = current_user.id if current_user.is_authenticated else None
    friend_ids = set()
    if current_user_id:
        friend_rows = (
            db.session.query(UserFriend.friend_user_id)
            .filter(UserFriend.user_id == current_user_id)
            .all()
        )
        friend_ids = {friend_id for (friend_id,) in friend_rows if friend_id}

    folder_name_candidates = set()
    for item in items:
        for entry in item.source_folders_list:
            raw_name = entry.get("name") if isinstance(entry, dict) else ""
            _owner_hint, folder_name = split_folder_label(raw_name)
            if folder_name:
                folder_name_candidates.add(folder_name)
        linked_card = item.card if item.card and folder_is_collection(item.card.folder) else None
        if linked_card and linked_card.folder and linked_card.folder.name:
            folder_name_candidates.add(linked_card.folder.name)

    folders_by_name = _collection_folders_for_names(folder_name_candidates, current_user_id, friend_ids)
    folder_ids = set()
    for folders in folders_by_name.values():
        for folder in folders:
            folder_id = int(folder.id or 0)
            if folder_id > 0:
                folder_ids.add(folder_id)

    oracle_ids = {(item.oracle_id or "").strip() for item in items if (item.oracle_id or "").strip()}
    name_keys = {(item.name or "").strip().lower() for item in items if (item.name or "").strip()}
    cards_by_folder_oracle, cards_by_folder_name = _collection_card_lookup(folder_ids, oracle_ids, name_keys)

    for item in items:
        display_card = _pick_collection_display_card(item, folders_by_name, cards_by_folder_oracle, cards_by_folder_name)
        item.display_card_id = int(display_card.id) if display_card else None
        item.display_scryfall_id = item.scryfall_id
        item.display_image_url = None

        rarity = _normalize_rarity(getattr(display_card, "rarity", None)) if display_card else None
        if display_card:
            try:
                print_data = sc.find_by_set_cn(display_card.set_code, display_card.collector_number, display_card.name)
            except Exception:
                print_data = None
            if print_data:
                item.display_scryfall_id = print_data.get("id") or item.display_scryfall_id
                try:
                    image_data = sc.image_for_print(print_data) or {}
                except Exception:
                    image_data = {}
                item.display_image_url = image_data.get("normal") or image_data.get("large") or image_data.get("small")
                if not rarity:
                    rarity = _normalize_rarity(print_data.get("rarity"))

        item.display_rarity = rarity
        item.display_rarity_label = _rarity_label(rarity)
        item.display_rarity_badge_class = _rarity_badge_class(rarity)
