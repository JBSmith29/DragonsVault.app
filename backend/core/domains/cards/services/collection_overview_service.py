"""Collection overview page service."""

from __future__ import annotations

import json

from flask import render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from extensions import db
from models import Card, Folder, User, UserFriend
from core.domains.cards.services.scryfall_cache import cache_epoch, cache_ready, ensure_cache_loaded, find_by_set_cn, prints_for_oracle, set_name_for_code
from core.domains.cards.viewmodels.card_vm import TypeBreakdownVM
from core.domains.decks.viewmodels.folder_vm import CollectionBucketVM, FolderOptionVM
from core.domains.games.services.stats import get_folder_stats
from shared.cache.runtime_cache import cache_fetch as _cache_fetch, user_cache_key as _user_cache_key
from shared.mtg import _collection_rows_with_fallback


def _ensure_cache_ready() -> bool:
    return cache_ready() or ensure_cache_loaded()


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
    folder_ids = [folder_id for folder_id, _ in collection_rows if folder_id is not None]
    user_key = _user_cache_key()

    if folder_ids:
        folders = Folder.query.filter(Folder.id.in_(folder_ids)).order_by(func.lower(Folder.name)).all()
    else:
        folders = []

    folder_by_id = {folder.id: folder for folder in folders}
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
    for folder_id, name in collection_rows:
        folder = folder_by_id.get(folder_id)
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
            total_rows = sum(stat["rows"] for stat in stats_list)
            total_qty = sum(stat["qty"] for stat in stats_list)
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
        stats_by_id = {stat["folder_id"]: {"rows": stat["rows"], "qty": stat["qty"]} for stat in stats_list}
    else:
        total_rows = 0
        total_qty = 0
        stats_by_id = {}
        by_set = []
        filters_key = ""

    for bucket in buckets:
        folder = bucket.folder
        if folder:
            stats = stats_by_id.get(folder.id, {"rows": 0, "qty": 0})
            bucket.rows = stats["rows"]
            bucket.qty = stats["qty"]
        else:
            bucket.rows = 0
            bucket.qty = 0

    have_cache = _ensure_cache_ready()
    sets_with_names = [
        (set_code or "", (set_name_for_code(set_code) if have_cache else None), int(qty))
        for set_code, qty in by_set
        if set_code
    ]

    base_types = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]
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
            type_totals = {value: 0 for value in base_types}
            for name, set_code, collector_number, oracle_id, qty in rows:
                qty = int(qty or 0) or 1
                key = (
                    f"oid:{oracle_id}"
                    if oracle_id
                    else f"{(set_code or '').lower()}:{(str(collector_number) or '').lower()}:{(name or '').lower()}"
                )
                if key in type_line_cache:
                    type_line = type_line_cache[key]
                else:
                    print_data = None
                    try:
                        print_data = find_by_set_cn(set_code, collector_number, name)
                    except Exception:
                        print_data = None
                    if not print_data and oracle_id:
                        try:
                            prints = prints_for_oracle(oracle_id) or []
                            if prints:
                                print_data = prints[0]
                        except Exception:
                            print_data = None
                    type_line = (print_data or {}).get("type_line")
                    type_line_cache[key] = type_line

                for value in [candidate for candidate in base_types if candidate in (type_line or "")]:
                    type_totals[value] += qty
            return [(value, type_totals.get(value, 0)) for value in base_types if type_totals.get(value, 0) > 0]

        type_breakdown = _cache_fetch(type_cache_key, 300, _type_breakdown)
    else:
        type_breakdown = []

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

    collection_names_for_template = [bucket.label for bucket in buckets]

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


__all__ = ["collection_overview"]
