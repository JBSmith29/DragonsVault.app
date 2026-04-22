"""Inventory lookup and face-rescue helpers for list checker results."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from flask_login import current_user
from sqlalchemy import func, or_

from extensions import db
from models import Card, Folder, UserFriend
from core.domains.cards.services import list_checker_parsing_service as parsing_service
from shared.mtg import _collection_rows_with_fallback, _normalize_name


@dataclass(slots=True)
class ListCheckerInventorySnapshot:
    current_user_id: int | None
    friend_ids: set[int]
    collection_id_set: set[int]
    folder_meta: dict[int, dict]
    owner_user_ids: set[int]
    per_folder_counts: defaultdict[str, defaultdict[int, int]]
    collection_counts: defaultdict[str, defaultdict[int, int]]
    deck_counts: defaultdict[str, defaultdict[int, int]]
    available_per_folder_counts: defaultdict[str, defaultdict[int, int]]
    available_count: defaultdict[str, int]
    rep_card_map: dict[str, Card]


def _load_collection_scope() -> tuple[int | None, set[int], set[int]]:
    current_user_id = current_user.id if current_user.is_authenticated else None
    friend_ids: set[int] = set()
    if current_user_id:
        friend_rows = (
            db.session.query(UserFriend.friend_user_id)
            .filter(UserFriend.user_id == current_user_id)
            .all()
        )
        friend_ids = {friend_id for (friend_id,) in friend_rows if friend_id}
        owner_ids = [current_user_id] + list(friend_ids)
        collection_rows = _collection_rows_with_fallback(owner_user_ids=owner_ids)
    else:
        collection_rows = _collection_rows_with_fallback()
    return current_user_id, friend_ids, {folder_id for folder_id, _ in collection_rows if folder_id is not None}


def _record_card_occurrence(
    *,
    normalized_name: str,
    card: Card,
    folder_id: int | None,
    folder_name: str | None,
    owner_user_id: int | None,
    owner_name: str | None,
    collection_id_set: set[int],
    folder_meta: dict[int, dict],
    owner_user_ids: set[int],
    per_folder_counts,
    collection_counts,
    deck_counts,
    available_per_folder_counts,
    best_card_for_name: dict[str, tuple[tuple[int, int, str], Card]],
) -> None:
    if folder_id is None or not folder_name:
        return

    if folder_id not in folder_meta:
        folder_meta[folder_id] = {
            "name": folder_name or "",
            "owner_user_id": owner_user_id,
            "owner": owner_name or "",
        }
    if owner_user_id:
        owner_user_ids.add(owner_user_id)

    per_folder_counts[normalized_name][folder_id] += 1
    is_collection_folder = folder_id in collection_id_set
    if is_collection_folder:
        collection_counts[normalized_name][folder_id] += 1
        available_per_folder_counts[normalized_name][folder_id] += 1
    else:
        deck_counts[normalized_name][folder_id] += 1

    current_viewer_id = current_user.id if current_user.is_authenticated else None
    lowered_label = (folder_name or "").strip().lower()
    rank = (
        0 if is_collection_folder else 1,
        0 if owner_user_id == current_viewer_id else 1,
        lowered_label,
    )
    previous = best_card_for_name.get(normalized_name)
    candidate = (rank, card)
    if previous is None or rank < previous[0]:
        best_card_for_name[normalized_name] = candidate


def _load_exact_rows(keys: list[str]):
    return (
        db.session.query(
            Card,
            Folder.id.label("folder_id"),
            Folder.name.label("folder_name"),
            Folder.owner_user_id.label("owner_user_id"),
            Folder.owner.label("owner_name"),
        )
        .join(Folder, Folder.id == Card.folder_id, isouter=True)
        .filter(func.lower(Card.name).in_(keys))
        .all()
    )


def _apply_face_rescue(
    *,
    keys: list[str],
    display_by_nkey: dict[str, str],
    collection_id_set: set[int],
    folder_meta: dict[int, dict],
    owner_user_ids: set[int],
    per_folder_counts,
    collection_counts,
    deck_counts,
    available_per_folder_counts,
    available_count,
    rep_card_map,
    best_card_for_name,
) -> None:
    for normalized_name in keys:
        if per_folder_counts[normalized_name] or available_count[normalized_name]:
            continue

        display_name = display_by_nkey.get(normalized_name, "")
        patterns = parsing_service.face_like_patterns(display_name)
        canonical_lower = None
        face_card = None

        if not patterns:
            face_card = parsing_service.find_card_by_name_or_face(display_name)
            if not face_card:
                continue
            canonical_lower = face_card.name.lower()

        if canonical_lower:
            add_rows = (
                db.session.query(
                    Card,
                    Folder.id.label("folder_id"),
                    Folder.name.label("folder_name"),
                    Folder.owner_user_id.label("owner_user_id"),
                    Folder.owner.label("owner_name"),
                )
                .join(Folder, Folder.id == Card.folder_id, isouter=True)
                .filter(func.lower(Card.name) == canonical_lower)
                .all()
            )
            for card, folder_id, folder_name, owner_user_id, owner_name in add_rows:
                _record_card_occurrence(
                    normalized_name=normalized_name,
                    card=card,
                    folder_id=folder_id,
                    folder_name=folder_name,
                    owner_user_id=owner_user_id,
                    owner_name=owner_name,
                    collection_id_set=collection_id_set,
                    folder_meta=folder_meta,
                    owner_user_ids=owner_user_ids,
                    per_folder_counts=per_folder_counts,
                    collection_counts=collection_counts,
                    deck_counts=deck_counts,
                    available_per_folder_counts=available_per_folder_counts,
                    best_card_for_name=best_card_for_name,
                )

            add_rows2 = (
                db.session.query(Card.folder_id)
                .join(Folder, Folder.id == Card.folder_id, isouter=True)
                .filter(func.lower(Card.name) == canonical_lower)
                .all()
            )
            for (folder_id,) in add_rows2:
                if folder_id and folder_id in collection_id_set:
                    available_count[normalized_name] += 1
            rep_card_map[normalized_name] = face_card
            continue

        if not patterns:
            continue

        add_rows = (
            db.session.query(
                Card,
                Folder.id.label("folder_id"),
                Folder.name.label("folder_name"),
                Folder.owner_user_id.label("owner_user_id"),
                Folder.owner.label("owner_name"),
            )
            .join(Folder, Folder.id == Card.folder_id, isouter=True)
            .filter(or_(*[Card.name.ilike(pattern) for pattern in patterns]))
            .all()
        )
        for card, folder_id, folder_name, owner_user_id, owner_name in add_rows:
            _record_card_occurrence(
                normalized_name=normalized_name,
                card=card,
                folder_id=folder_id,
                folder_name=folder_name,
                owner_user_id=owner_user_id,
                owner_name=owner_name,
                collection_id_set=collection_id_set,
                folder_meta=folder_meta,
                owner_user_ids=owner_user_ids,
                per_folder_counts=per_folder_counts,
                collection_counts=collection_counts,
                deck_counts=deck_counts,
                available_per_folder_counts=available_per_folder_counts,
                best_card_for_name=best_card_for_name,
            )

        add_rows2 = (
            db.session.query(Card.folder_id)
            .join(Folder, Folder.id == Card.folder_id, isouter=True)
            .filter(or_(*[Card.name.ilike(pattern) for pattern in patterns]))
            .all()
        )
        for (folder_id,) in add_rows2:
            if folder_id and folder_id in collection_id_set:
                available_count[normalized_name] += 1

        representative = (
            Card.query.filter(or_(*[Card.name.ilike(pattern) for pattern in patterns]))
            .order_by(func.length(Card.name))
            .first()
        )
        if representative:
            rep_card_map[normalized_name] = representative


def build_inventory_snapshot(want, display_by_nkey: dict[str, str]) -> ListCheckerInventorySnapshot:
    keys = list(want.keys())
    current_user_id, friend_ids, collection_id_set = _load_collection_scope()

    per_folder_counts = defaultdict(lambda: defaultdict(int))
    collection_counts = defaultdict(lambda: defaultdict(int))
    deck_counts = defaultdict(lambda: defaultdict(int))
    available_per_folder_counts = defaultdict(lambda: defaultdict(int))
    available_count = defaultdict(int)
    folder_meta: dict[int, dict] = {}
    owner_user_ids: set[int] = set()
    best_card_for_name: dict[str, tuple[tuple[int, int, str], Card]] = {}

    exact_rows = _load_exact_rows(keys)
    for card, folder_id, folder_name, owner_user_id, owner_name in exact_rows:
        normalized_name = _normalize_name(card.name)
        _record_card_occurrence(
            normalized_name=normalized_name,
            card=card,
            folder_id=folder_id,
            folder_name=folder_name,
            owner_user_id=owner_user_id,
            owner_name=owner_name,
            collection_id_set=collection_id_set,
            folder_meta=folder_meta,
            owner_user_ids=owner_user_ids,
            per_folder_counts=per_folder_counts,
            collection_counts=collection_counts,
            deck_counts=deck_counts,
            available_per_folder_counts=available_per_folder_counts,
            best_card_for_name=best_card_for_name,
        )

    exact_available_rows = (
        db.session.query(Card.name, Card.folder_id)
        .join(Folder, Folder.id == Card.folder_id, isouter=True)
        .filter(func.lower(Card.name).in_(keys))
        .all()
    )
    for name, folder_id in exact_available_rows:
        if folder_id and folder_id in collection_id_set:
            available_count[_normalize_name(name)] += 1

    rep_card_map = {normalized_name: best_card_for_name[normalized_name][1] for normalized_name in best_card_for_name}

    _apply_face_rescue(
        keys=keys,
        display_by_nkey=display_by_nkey,
        collection_id_set=collection_id_set,
        folder_meta=folder_meta,
        owner_user_ids=owner_user_ids,
        per_folder_counts=per_folder_counts,
        collection_counts=collection_counts,
        deck_counts=deck_counts,
        available_per_folder_counts=available_per_folder_counts,
        available_count=available_count,
        rep_card_map=rep_card_map,
        best_card_for_name=best_card_for_name,
    )

    return ListCheckerInventorySnapshot(
        current_user_id=current_user_id,
        friend_ids=friend_ids,
        collection_id_set=collection_id_set,
        folder_meta=folder_meta,
        owner_user_ids=owner_user_ids,
        per_folder_counts=per_folder_counts,
        collection_counts=collection_counts,
        deck_counts=deck_counts,
        available_per_folder_counts=available_per_folder_counts,
        available_count=available_count,
        rep_card_map=rep_card_map,
    )


__all__ = [
    "ListCheckerInventorySnapshot",
    "build_inventory_snapshot",
]
