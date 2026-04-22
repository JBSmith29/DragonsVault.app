"""Query, filter, and pagination helpers for the collection browser."""

from __future__ import annotations

import re
from math import ceil
from typing import Any

from flask import request, url_for
from sqlalchemy import func, or_
from sqlalchemy.orm import load_only, selectinload

from extensions import db
from models import Card, Folder, User, UserFriend
from models.role import OracleCoreRoleTag, OracleEvergreenTag, Role, SubRole
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.collection_card_list_view_service import (
    build_collection_card_list_items,
    image_from_print_payload,
    price_value_from_exact_prices,
)
from core.domains.cards.services.collection_request_service import CollectionBrowserRequest
from core.domains.cards.services.pricing import (
    prices_for_print_exact as _prices_for_print_exact,
)
from shared.mtg import (
    _bulk_print_lookup,
    _color_letters_list,
    _effective_color_identity,
    _oracle_text_from_faces,
    _type_line_from_print,
)

BASE_TYPES = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]
CARD_COLUMNS = (
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


def _resolve_card_metadata(card_obj: Card, print_payload: dict | None) -> dict[str, Any]:
    print_data = print_payload or {}
    type_line = (getattr(card_obj, "type_line", None) or "").strip() or _type_line_from_print(print_data)
    oracle_text = (
        (getattr(card_obj, "oracle_text", None) or "").strip()
        or _oracle_text_from_faces(getattr(card_obj, "faces_json", None))
        or (print_data.get("oracle_text") or _oracle_text_from_faces(print_data.get("card_faces")) or "")
    )
    color_letters = _color_letters_list(getattr(card_obj, "color_identity", None)) or _color_letters_list(
        getattr(card_obj, "colors", None)
    )
    if not color_letters:
        color_letters = _color_letters_list(print_data.get("color_identity")) or _color_letters_list(print_data.get("colors"))
    color_letters = _effective_color_identity(type_line, oracle_text, color_letters or [])
    if not color_letters:
        color_letters = []
    rarity_value = (
        (getattr(card_obj, "rarity", None) or "").strip().lower()
        or str(print_data.get("rarity") or "").strip().lower()
    )
    mask_map = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}
    color_mask = 0
    for symbol in color_letters:
        color_mask |= mask_map.get(symbol, 0)
    return {
        "type_line": type_line,
        "rarity": rarity_value,
        "color_letters": color_letters,
        "color_mask": color_mask,
    }


def _matches_metadata_filters(meta: dict[str, Any], params: CollectionBrowserRequest) -> bool:
    type_line_lc = str(meta.get("type_line") or "").lower()
    rarity_value = str(meta.get("rarity") or "").strip().lower()
    color_letters = [str(ch).upper() for ch in (meta.get("color_letters") or []) if str(ch).upper() in "WUBRG"]
    color_set = set(color_letters)

    if params.rarity and rarity_value != params.rarity:
        return False
    if params.typal and params.typal not in type_line_lc:
        return False
    if params.selected_types:
        if params.type_mode == "exact":
            if any(value not in type_line_lc for value in params.selected_types):
                return False
        else:
            if not any(value in type_line_lc for value in params.selected_types):
                return False
    if params.selected_colors:
        has_c = "c" in params.selected_colors
        non_c = {value.upper() for value in params.selected_colors if value != "c" and value}
        if params.color_mode == "exact":
            if has_c and non_c:
                return False
            if has_c:
                if color_set:
                    return False
            elif color_set != non_c:
                return False
        else:
            if has_c and not non_c:
                if color_set:
                    return False
            else:
                matches_non_c = not non_c or non_c.issubset(color_set)
                if has_c:
                    if not (not color_set or matches_non_c):
                        return False
                elif not matches_non_c:
                    return False
    return True


def _base_card_query(params: CollectionBrowserRequest):
    query = Card.query
    if params.is_authenticated:
        if params.show_friends:
            friend_ids = db.session.query(UserFriend.friend_user_id).filter(UserFriend.user_id == params.current_user_id)
            query = query.filter(
                Card.folder.has(
                    or_(
                        Folder.owner_user_id == params.current_user_id,
                        Folder.owner_user_id.in_(friend_ids),
                    )
                )
            )
        else:
            query = query.filter(Card.folder.has(Folder.owner_user_id == params.current_user_id))
    if params.role_list:
        query = query.join(Card.roles).filter(Role.label.in_(params.role_list))
    if params.subrole_list:
        query = query.join(Card.subroles).filter(SubRole.label.in_(params.subrole_list))
    if params.role_query_text:
        role_query_base = params.role_query_text.lower().strip()
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
    if params.role_list or params.subrole_list:
        query = query.distinct()
    if params.role_list:
        query = query.join(Card.roles).filter(Role.label.in_(params.role_list))
    if params.subrole_list:
        query = query.join(Card.subroles).filter(SubRole.label.in_(params.subrole_list))
    if params.q_text:
        for token in [value for value in params.q_text.split() if value]:
            query = query.filter(Card.name.ilike(f"%{token}%"))
    if params.folder_id_int is not None:
        query = query.filter(Card.folder_id == params.folder_id_int)
    if params.collection_flag:
        if params.collection_ids:
            query = query.filter(Card.folder_id.in_(params.collection_ids))
        else:
            query = query.filter(Card.id == -1)
    if params.set_code:
        query = query.filter(func.lower(Card.set_code) == params.set_code)
    if params.foil_only:
        query = query.filter(Card.is_foil.is_(True))
    return query


def _apply_metadata_filters(query, params: CollectionBrowserRequest) -> tuple[Any, dict[int, dict[str, Any]]]:
    metadata_filter_requested = bool(params.rarity or params.typal or params.selected_types or params.selected_colors)
    metadata_resolved_cache: dict[int, dict[str, Any]] = {}
    if not metadata_filter_requested:
        if params.rarity and hasattr(Card, "rarity"):
            query = query.filter(func.lower(Card.rarity) == params.rarity)
        if params.typal and hasattr(Card, "type_line"):
            query = query.filter(Card.type_line.ilike(f"%{params.typal}%"))
        use_db_types = hasattr(Card, "type_line")
        if params.selected_types and use_db_types:
            if params.type_mode == "exact":
                for value in params.selected_types:
                    query = query.filter(Card.type_line.ilike(f"%{value}%"))
            else:
                query = query.filter(or_(*[Card.type_line.ilike(f"%{value}%") for value in params.selected_types]))
        if params.selected_colors:
            has_c = "c" in params.selected_colors
            non_c = [value.upper() for value in params.selected_colors if value != "c"]
            mask_map = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}
            want_mask = 0
            for symbol in non_c:
                want_mask |= mask_map.get(symbol, 0)
            mask_expr = func.coalesce(Card.color_identity_mask, 0)

            if params.color_mode == "exact":
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
        return query, metadata_resolved_cache

    filter_cards = (
        query.options(
            load_only(
                *CARD_COLUMNS,
                Card.oracle_text,
                Card.mana_value,
                Card.faces_json,
            )
        )
        .all()
    )
    if not sc.cache_ready():
        sc.ensure_cache_loaded()
    filter_print_map = _bulk_print_lookup(filter_cards)
    keep_ids: list[int] = []
    for card_obj in filter_cards:
        print_data = filter_print_map.get(card_obj.id, {}) or {}
        meta = _resolve_card_metadata(card_obj, print_data)
        metadata_resolved_cache[card_obj.id] = meta
        if _matches_metadata_filters(meta, params):
            keep_ids.append(card_obj.id)
    if keep_ids:
        query = query.filter(Card.id.in_(keep_ids))
    else:
        query = query.filter(Card.id == -1)
    return query, metadata_resolved_cache


def _rarity_rank(value: str) -> int:
    normalized = (value or "").strip().lower()
    if normalized in {"mythic", "mythic rare"}:
        return 4
    if normalized == "rare":
        return 3
    if normalized == "uncommon":
        return 2
    if normalized == "common":
        return 1
    if normalized:
        return 0
    return -1


def _ordered_cards_page(query, params: CollectionBrowserRequest, metadata_resolved_cache: dict[int, dict[str, Any]]):
    if params.sort == "qty":
        order_col = func.coalesce(Card.quantity, 0)
    elif params.sort == "set":
        order_col = func.lower(Card.set_code)
    elif params.sort == "cn":
        order_col = func.lower(Card.collector_number)
    elif params.sort == "foil":
        order_col = Card.is_foil
    elif params.sort in {"ctype", "type"}:
        order_col = func.lower(func.coalesce(Card.type_line, ""))
    elif params.sort in {"rar", "rarity"}:
        order_col = func.lower(func.coalesce(Card.rarity, ""))
    elif params.sort in {"colors", "colour"}:
        order_col = func.coalesce(Card.color_identity_mask, 0)
    elif params.sort in {"core_role", "core"}:
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
    elif params.sort in {"evergreen", "evergreen_tag"}:
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
    elif params.sort in {"price", "art"}:
        order_col = Card.id
    elif params.sort == "folder":
        query = query.outerjoin(Folder, Folder.id == Card.folder_id)
        order_col = func.lower(Folder.name)
    elif params.sort == "owner":
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

    cards: list[Card] = []
    ordered_ids: list[int] = []
    total = 0
    full_sort_keys = {"price", "art", "ctype", "type", "rar", "rarity", "colors", "colour"}
    if params.sort in full_sort_keys:
        all_cards = (
            query.order_by(Card.id.asc())
            .options(
                load_only(
                    *CARD_COLUMNS,
                    Card.oracle_text,
                    Card.mana_value,
                    Card.faces_json,
                )
            )
            .all()
        )
        total = len(all_cards)

        if not sc.cache_ready():
            sc.ensure_cache_loaded()
        full_print_map = _bulk_print_lookup(all_cards)

        if params.sort == "price":
            price_values = {}
            for card_obj in all_cards:
                print_data = full_print_map.get(card_obj.id, {})
                prices = _prices_for_print_exact(print_data) if print_data else {}
                price_values[card_obj.id] = price_value_from_exact_prices(prices, bool(card_obj.is_foil))

            def _price_sort_key(card_obj):
                value = price_values.get(card_obj.id)
                missing = value is None
                if params.reverse:
                    return (missing, -(value or 0.0))
                return (missing, value or 0.0)

            ordered_ids = [card_obj.id for card_obj in sorted(all_cards, key=_price_sort_key)]
        elif params.sort == "art":
            art_missing = {}
            for card_obj in all_cards:
                print_data = full_print_map.get(card_obj.id, {})
                image_package = sc.image_for_print(print_data) if print_data else {}
                thumb_src = image_package.get("small") or image_package.get("normal") or image_package.get("large")
                if not thumb_src:
                    thumb_src = image_from_print_payload(print_data)
                art_missing[card_obj.id] = 0 if thumb_src else 1

            ordered_ids = [
                card_obj.id
                for card_obj in sorted(
                    all_cards,
                    key=lambda card_obj: art_missing.get(card_obj.id, 1),
                    reverse=params.reverse,
                )
            ]
        else:
            resolved_meta_by_id: dict[int, dict[str, Any]] = {}
            for card_obj in all_cards:
                cached_meta = metadata_resolved_cache.get(card_obj.id)
                if cached_meta is not None:
                    resolved_meta_by_id[card_obj.id] = cached_meta
                    continue
                print_data = full_print_map.get(card_obj.id, {}) or {}
                resolved_meta_by_id[card_obj.id] = _resolve_card_metadata(card_obj, print_data)

            if params.sort in {"ctype", "type"}:
                ordered = sorted(
                    all_cards,
                    key=lambda card_obj: (
                        (resolved_meta_by_id.get(card_obj.id, {}).get("type_line") or "").lower(),
                        (card_obj.name or "").lower(),
                        card_obj.id,
                    ),
                    reverse=params.reverse,
                )
            elif params.sort in {"rar", "rarity"}:
                ordered = sorted(
                    all_cards,
                    key=lambda card_obj: (
                        _rarity_rank(str(resolved_meta_by_id.get(card_obj.id, {}).get("rarity") or "")),
                        (card_obj.name or "").lower(),
                        card_obj.id,
                    ),
                    reverse=params.reverse,
                )
            else:
                ordered = sorted(
                    all_cards,
                    key=lambda card_obj: (
                        int(resolved_meta_by_id.get(card_obj.id, {}).get("color_mask") or 0),
                        "".join(resolved_meta_by_id.get(card_obj.id, {}).get("color_letters") or []),
                        (card_obj.name or "").lower(),
                        card_obj.id,
                    ),
                    reverse=params.reverse,
                )
            ordered_ids = [card_obj.id for card_obj in ordered]

        pages = max(1, ceil(total / params.per)) if params.per else 1
        page = min(params.page, pages)
        start = (page - 1) * params.per + 1 if total else 0
        end = min(start + params.per - 1, total) if total else 0
        offset = (page - 1) * params.per
        page_ids = ordered_ids[offset: offset + params.per]
        if page_ids:
            page_cards = (
                query.options(
                    load_only(*CARD_COLUMNS),
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
            page_map = {card_obj.id: card_obj for card_obj in page_cards}
            cards = [page_map[card_id] for card_id in page_ids if card_id in page_map]
        return cards, total, page, pages, start, end

    order_expr = order_col.desc() if params.reverse else order_col.asc()
    total = query.order_by(None).count()
    pages = max(1, ceil(total / params.per)) if params.per else 1
    page = min(params.page, pages)
    start = (page - 1) * params.per + 1 if total else 0
    end = min(start + params.per - 1, total) if total else 0
    offset = (page - 1) * params.per
    cards = (
        query.options(
            load_only(*CARD_COLUMNS),
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
        .limit(params.per)
        .offset(offset)
        .all()
    )
    return cards, total, page, pages, start, end


def _page_url(page_num: int, per: int) -> str:
    args = request.args.to_dict(flat=False)
    args["page"] = [str(page_num)]
    if "per" not in args and "per_page" not in args:
        args["per"] = [str(per)]
    return url_for("views.list_cards", **{key: value if len(value) > 1 else value[0] for key, value in args.items()})


def build_collection_browser_context(params: CollectionBrowserRequest) -> dict[str, Any]:
    query = _base_card_query(params)
    query, metadata_resolved_cache = _apply_metadata_filters(query, params)
    cards, total, page, pages, start, end = _ordered_cards_page(query, params, metadata_resolved_cache)

    cards_vm = build_collection_card_list_items(
        cards,
        base_types=BASE_TYPES,
        current_user_id=params.current_user_id,
    )
    prev_url = _page_url(page - 1, params.per) if page > 1 else None
    next_url = _page_url(page + 1, params.per) if page < pages else None
    page_urls = [(number, _page_url(number, params.per)) for number in range(1, pages + 1)]
    page_url_map = {number: url for number, url in page_urls}

    return {
        "cards": cards_vm,
        "total": total,
        "page": page,
        "per": params.per,
        "pages": pages,
        "prev_url": prev_url,
        "next_url": next_url,
        "page_urls": page_urls,
        "page_url_map": page_url_map,
        "start": start,
        "end": end,
        "q": params.q_text,
        "folder_id": params.folder_arg,
        "folder_is_proxy": params.folder_is_proxy,
        "set_code": params.set_code,
        "tribe": params.typal,
        "foil_only": params.foil_only,
        "rarity": params.rarity,
        "role_list": params.role_list,
        "subrole_list": params.subrole_list,
        "selected_types": params.selected_types,
        "selected_colors": params.selected_colors,
        "color_mode": params.color_mode,
        "type_mode": params.type_mode,
        "collection_flag": params.collection_flag,
        "show_friends": params.show_friends,
        "sort": params.sort,
        "direction": params.direction,
        "role_query_text": params.role_query_text,
        "per_page": params.per,
        "is_deck_folder": params.is_deck_folder,
        "collection_folders": params.collection_names,
    }


__all__ = ["build_collection_browser_context"]
