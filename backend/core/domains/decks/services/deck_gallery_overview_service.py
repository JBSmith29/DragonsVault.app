"""Deck gallery overview page context builders."""

from __future__ import annotations

from math import ceil
from typing import Any


def _joined_oracle_text(print_obj: dict | None) -> str:
    if not print_obj:
        return ""
    parts = []
    text = print_obj.get("oracle_text")
    if text:
        parts.append(text)
    for face in print_obj.get("card_faces") or []:
        face_text = (face or {}).get("oracle_text")
        if face_text:
            parts.append(face_text)
    return " // ".join(part for part in parts if part)


def build_decks_overview_context(*, hooks: Any) -> dict:
    sort = (hooks.request.args.get("sort") or "").strip().lower()
    direction = (hooks.request.args.get("dir") or "").strip().lower() or "desc"
    reverse = direction == "desc"
    is_authenticated = bool(hooks.current_user and getattr(hooks.current_user, "is_authenticated", False))
    scope = (hooks.request.args.get("scope") or ("mine" if is_authenticated else "all")).strip().lower()
    if is_authenticated and scope not in {"mine", "friends", "all"}:
        scope = "mine"
    if not is_authenticated:
        scope = "all"

    per_raw = (hooks.request.args.get("per") or hooks.request.args.get("per_page") or "").strip().lower()
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
        page = max(int(hooks.request.args.get("page", 1)), 1)
    except Exception:
        page = 1

    role_filter = hooks.Folder.role_entries.any(hooks.FolderRole.role.in_(hooks.FolderRole.DECK_ROLES))
    scope_filter = None
    shared_ids = None
    shared_filter = None
    if is_authenticated:
        friend_ids = (
            hooks.db.session.query(hooks.UserFriend.friend_user_id)
            .filter(hooks.UserFriend.user_id == hooks.current_user.id)
        )
        shared_ids = (
            hooks.db.session.query(hooks.FolderShare.folder_id)
            .filter(hooks.FolderShare.shared_user_id == hooks.current_user.id)
        )
        shared_filter = hooks.Folder.id.in_(shared_ids)
        if scope == "friends":
            scope_filter = hooks.Folder.owner_user_id.in_(friend_ids)
        elif scope == "all":
            scope_filter = hooks.or_(
                hooks.Folder.owner_user_id == hooks.current_user.id,
                hooks.Folder.owner_user_id.in_(friend_ids),
                shared_filter,
            )
        else:
            scope_filter = hooks.or_(hooks.Folder.owner_user_id == hooks.current_user.id, shared_filter)
    scoped_filters = [role_filter]
    if scope_filter is not None:
        scoped_filters.append(scope_filter)
    current_user_key = hooks.user_cache_key()

    def _summary_payload():
        base_counts = (
            hooks.db.session.query(
                hooks.Folder.id.label("folder_id"),
                hooks.Folder.owner.label("owner"),
                hooks.Folder.owner_user_id.label("owner_user_id"),
                hooks.Folder.is_proxy.label("is_proxy"),
                hooks.func.coalesce(hooks.func.sum(hooks.Card.quantity), 0).label("qty_sum"),
            )
            .outerjoin(hooks.Card, hooks.Card.folder_id == hooks.Folder.id)
            .filter(*scoped_filters)
            .group_by(hooks.Folder.id, hooks.Folder.owner, hooks.Folder.owner_user_id, hooks.Folder.is_proxy)
            .subquery()
        )
        total_decks = hooks.db.session.query(hooks.func.count(base_counts.c.folder_id)).scalar() or 0
        shared_total = 0
        if is_authenticated and shared_ids is not None and scope in {"mine", "all"}:
            shared_total = (
                hooks.db.session.query(hooks.func.count(base_counts.c.folder_id))
                .filter(
                    base_counts.c.folder_id.in_(shared_ids),
                    base_counts.c.owner_user_id != hooks.current_user.id,
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
                hooks.db.session.query(hooks.func.count(base_counts.c.folder_id))
                .filter(
                    base_counts.c.owner_user_id == hooks.current_user.id,
                    base_counts.c.is_proxy.is_(True),
                )
                .scalar()
                or 0
            )
            owned_total = (
                hooks.db.session.query(hooks.func.count(base_counts.c.folder_id))
                .filter(
                    base_counts.c.owner_user_id == hooks.current_user.id,
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
                hooks.db.session.query(hooks.func.count(base_counts.c.folder_id))
                .filter(base_counts.c.is_proxy.is_(True))
                .scalar()
                or 0
            )
            owned_total = total_decks - proxy_total
            friends_total = 0
        owner_rows = hooks.db.session.query(base_counts.c.owner).group_by(base_counts.c.owner).all()
        owners = sorted(
            {
                owner.strip()
                for (owner,) in owner_rows
                if isinstance(owner, str) and owner.strip()
            }
        )
        return total_decks, proxy_total, owned_total, friends_total, shared_total, owners

    summary_cache_key = f"deck_summary:v2:{current_user_key}:{scope}"
    total_decks, proxy_total, owned_total, friends_total, shared_total, owner_names = hooks.cache_fetch(
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
        hooks.db.session.query(
            hooks.Folder.id,
            hooks.Folder.name,
            hooks.func.count(hooks.Card.id).label("row_count"),
            hooks.func.coalesce(hooks.func.sum(hooks.Card.quantity), 0).label("qty_sum"),
            hooks.Folder.commander_oracle_id,
            hooks.Folder.commander_name,
            hooks.Folder.owner,
            hooks.Folder.owner_user_id,
            hooks.Folder.is_proxy,
        )
        .outerjoin(hooks.Card, hooks.Card.folder_id == hooks.Folder.id)
        .filter(*scoped_filters)
    )
    grouped = deck_query.group_by(
        hooks.Folder.id,
        hooks.Folder.name,
        hooks.Folder.commander_oracle_id,
        hooks.Folder.commander_name,
        hooks.Folder.owner,
        hooks.Folder.owner_user_id,
        hooks.Folder.is_proxy,
    )

    sort_key = sort if sort in {"name", "owner", "qty", "tag", "ci", "pips", "bracket"} else ""
    requires_full_sort = sort_key in {"tag", "ci", "pips", "bracket"}

    if requires_full_sort:
        rows = grouped.all()
    else:
        if sort_key == "name":
            order_col = hooks.func.lower(hooks.Folder.name)
        elif sort_key == "owner":
            order_col = hooks.func.lower(hooks.func.coalesce(hooks.Folder.owner, ""))
        else:
            order_col = hooks.func.coalesce(hooks.func.sum(hooks.Card.quantity), 0)
        order_expr = order_col.desc() if reverse else order_col.asc()
        rows = grouped.order_by(order_expr, hooks.Folder.id.asc()).limit(per).offset(offset).all()

    owner_user_ids = {
        owner_user_id
        for _fid, _name, _rows, _qty, _cmd_oid, _cmd_name, _owner, owner_user_id, _is_proxy in rows
        if owner_user_id
    }
    owner_user_labels = {}
    if owner_user_ids:
        owner_rows = (
            hooks.db.session.query(hooks.User.id, hooks.User.display_name, hooks.User.username, hooks.User.email)
            .filter(hooks.User.id.in_(owner_user_ids))
            .all()
        )
        for user_id, display_name, username, email in owner_rows:
            label = display_name or username or email
            if label:
                owner_user_labels[user_id] = label

    decks = []
    for folder_id, name, _row_count, qty, commander_oracle_id, commander_name, owner, owner_user_id, is_proxy in rows:
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
        decks.append(
            {
                "id": folder_id,
                "name": name,
                "qty": int(qty or 0),
                "commander_oid": commander_oracle_id,
                "commander_name": commander_name,
                "owner": owner_display or None,
                "owner_label": owner_label,
                "owner_key": owner_key,
                "owner_user_id": owner_user_id,
                "is_proxy": bool(is_proxy),
                "bracket": {},
                "tag": None,
                "tag_label": None,
            }
        )
    deck_ids = [deck["id"] for deck in decks]

    folders = hooks.Folder.query.filter(hooks.Folder.id.in_(deck_ids)).all()
    folder_map = {folder.id: folder for folder in folders}

    for deck in decks:
        folder = folder_map.get(deck["id"])
        if not folder:
            continue
        tag = folder.deck_tag
        deck["tag"] = tag
        deck["tag_label"] = tag or None

    deck_bracket_map: dict[int, dict] = {}
    if deck_ids:
        hooks._ensure_cache_ready()
        epoch = hooks.cache_epoch() + hooks.BRACKET_RULESET_EPOCH + hooks.spellbook_dataset_epoch()

        oracle_cache: dict[str, dict | None] = {}
        set_collector_cache: dict[tuple[str, str], dict | None] = {}

        deck_cards_map: dict[int, list[dict]] = {}
        card_rows = hooks.Card.query.filter(hooks.Card.folder_id.in_(deck_ids)).all()
        for card_row in card_rows:
            folder_id = card_row.folder_id
            qty = int(getattr(card_row, "quantity", 0) or 0) or 1

            print_obj = None
            oracle_id = getattr(card_row, "oracle_id", None)
            if oracle_id:
                if oracle_id in oracle_cache:
                    print_obj = oracle_cache[oracle_id]
                else:
                    try:
                        prints = hooks.prints_for_oracle(oracle_id) or []
                        print_obj = prints[0] if prints else None
                    except Exception:
                        print_obj = None
                    oracle_cache[oracle_id] = print_obj
            if print_obj is None:
                key = (card_row.set_code or "", str(card_row.collector_number or ""))
                if key in set_collector_cache:
                    cached = set_collector_cache[key]
                else:
                    try:
                        cached = hooks.find_by_set_cn(card_row.set_code, card_row.collector_number, card_row.name)
                    except Exception:
                        cached = None
                    set_collector_cache[key] = cached
                print_obj = cached

            if print_obj:
                payload = {
                    "name": hooks.sc.display_name_for_print(print_obj) if hasattr(hooks.sc, "display_name_for_print") else print_obj.get("name") or card_row.name,
                    "type_line": hooks.sc.type_label_for_print(print_obj) if hasattr(hooks.sc, "type_label_for_print") else print_obj.get("type_line") or "",
                    "oracle_text": _joined_oracle_text(print_obj),
                    "mana_cost": print_obj.get("mana_cost"),
                    "mana_value": print_obj.get("cmc"),
                    "produced_mana": print_obj.get("produced_mana"),
                    "quantity": qty,
                    "game_changer": bool(print_obj.get("game_changer")),
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

            deck_cards_map.setdefault(folder_id, []).append(payload)

        for deck in decks:
            folder_id = deck["id"]
            folder = folder_map.get(folder_id)
            if not folder:
                continue
            cards_payload = deck_cards_map.get(folder_id, [])
            commander_stub = {
                "oracle_id": hooks.primary_commander_oracle_id(folder.commander_oracle_id),
                "name": hooks.primary_commander_name(folder.commander_name) or folder.commander_name,
            }
            ctx = None
            signature = None
            if folder_id:
                signature = hooks.compute_bracket_signature(cards_payload, commander_stub, epoch=epoch)
                ctx = hooks.get_cached_bracket(folder_id, signature, epoch)
            if not ctx:
                ctx = hooks.evaluate_commander_bracket(cards_payload, commander_stub)
                if folder_id and signature:
                    hooks.store_cached_bracket(folder_id, signature, epoch, ctx)
            deck_bracket_map[folder_id] = ctx
            deck["bracket"] = ctx

    hooks.ensure_symbols_cache(force=False)
    if not hooks.sc.cache_ready():
        hooks.sc.ensure_cache_loaded()
    thumbnail_epoch = hooks.cache_epoch()
    deck_ci_letters = {}
    deck_ci_name = {}
    deck_ci_html = {}
    deck_commanders = {}
    placeholder_thumb = hooks.static_url("img/card-placeholder.svg")

    for folder_id, _name, row_count, qty_sum, commander_oracle_id, commander_name, _owner, _owner_user_id, _is_proxy in rows:
        letters, label = hooks.compute_folder_color_identity(folder_id, "20260311a")
        letters = letters or ["C"]
        letters_str = "".join(ch for ch in "WUBRG" if ch in set(letters)) or "C"
        deck_ci_letters[folder_id] = letters_str
        deck_ci_name[folder_id] = label or hooks.color_identity_name(letters)
        mana_str = "".join(f"{{{ch}}}" for ch in (letters_str if letters_str else "C"))
        deck_ci_html[folder_id] = hooks.render_mana_html(mana_str, use_local=False)

        folder = folder_map.get(folder_id)
        cmd_card = None
        try:
            oracle_ids = [
                (oracle_id or "").strip().lower()
                for oracle_id in hooks.split_commander_oracle_ids(folder.commander_oracle_id)
                if (oracle_id or "").strip()
            ] if folder else []
            if oracle_ids:
                cmd_card = (
                    hooks.Card.query.filter(
                        hooks.Card.folder_id == folder_id,
                        hooks.Card.oracle_id.isnot(None),
                        hooks.func.lower(hooks.Card.oracle_id).in_(oracle_ids),
                    )
                    .order_by(hooks.Card.quantity.desc(), hooks.Card.id.asc())
                    .first()
                )
            if not cmd_card and folder and folder.commander_name:
                name_candidates = [name.strip().lower() for name in hooks.split_commander_names(folder.commander_name) if name.strip()]
                if name_candidates:
                    cmd_card = (
                        hooks.Card.query.filter(
                            hooks.Card.folder_id == folder_id,
                            hooks.func.lower(hooks.Card.name).in_(name_candidates),
                        )
                        .order_by(hooks.Card.quantity.desc(), hooks.Card.id.asc())
                        .first()
                    )
        except Exception:
            cmd_card = None

        images = []
        print_obj = None
        final_name = commander_name

        def add_image_from_print(print_data, name_hint=None):
            if not print_data:
                return
            image_pack = hooks._image_pack_from_print(print_data)
            small = image_pack.get("small") or image_pack.get("normal") or image_pack.get("large")
            normal = image_pack.get("normal") or image_pack.get("large") or image_pack.get("small")
            large = image_pack.get("large") or image_pack.get("normal") or image_pack.get("small")
            name_value = name_hint or getattr(folder, "commander_name", None) or (cmd_card.name if cmd_card else print_data.get("name"))
            images.append(
                {
                    "name": name_value,
                    "small": small or large or placeholder_thumb,
                    "normal": normal or small or placeholder_thumb,
                    "large": large or normal or placeholder_thumb,
                    "alt": name_value or "Commander",
                }
            )

        if cmd_card:
            final_name = final_name or getattr(folder, "commander_name", None) or cmd_card.name
            try:
                print_obj = hooks.find_by_set_cn(cmd_card.set_code, cmd_card.collector_number, cmd_card.name)
            except Exception:
                print_obj = None
            if not print_obj:
                print_obj = hooks._lookup_print_data(cmd_card.set_code, cmd_card.collector_number, cmd_card.name, cmd_card.oracle_id)
            if print_obj:
                add_image_from_print(print_obj, final_name)

        if folder:
            try:
                oracle_ids = [
                    (oracle_id or "").strip().lower()
                    for oracle_id in hooks.split_commander_oracle_ids(folder.commander_oracle_id)
                    if (oracle_id or "").strip()
                ]
            except Exception:
                oracle_ids = []
            for oracle_id in oracle_ids:
                if cmd_card and cmd_card.oracle_id and cmd_card.oracle_id.lower() == oracle_id:
                    continue
                try:
                    prints = hooks.prints_for_oracle(oracle_id) or []
                except Exception:
                    prints = []
                if not prints:
                    continue
                add_image_from_print(prints[0], final_name or prints[0].get("name"))

        if not images:
            target_oracle_id = hooks.primary_commander_oracle_id(commander_oracle_id) if commander_oracle_id else None
            if not target_oracle_id and folder:
                target_oracle_id = hooks.primary_commander_oracle_id(folder.commander_oracle_id)
            thumb_payload = hooks._commander_thumbnail_payload(
                folder_id,
                target_oracle_id,
                commander_name,
                int(row_count or 0),
                int(qty_sum or 0),
                thumbnail_epoch,
            )
            final_name = thumb_payload.get("name") or commander_name
            images.append(
                {
                    "name": final_name,
                    "small": thumb_payload.get("small") or placeholder_thumb,
                    "large": thumb_payload.get("large") or placeholder_thumb,
                    "alt": thumb_payload.get("alt") or (final_name or "Commander"),
                }
            )

        primary = images[0] if images else None
        if primary:
            payload = dict(primary)
            payload["images"] = images
            deck_commanders[folder_id] = payload

    if requires_full_sort:
        if sort_key == "ci":
            decks.sort(key=lambda deck: (deck_ci_name.get(deck["id"]) or "Colorless"), reverse=reverse)
        elif sort_key == "pips":
            decks.sort(key=lambda deck: (deck_ci_letters.get(deck["id"]) or "C"), reverse=reverse)
        elif sort_key == "bracket":
            decks.sort(key=lambda deck: (deck_bracket_map.get(deck["id"], {}).get("level") or 0), reverse=reverse)
        elif sort_key == "tag":
            def _tag_sort_key(deck):
                tag = (deck.get("tag_label") or deck.get("tag") or "").strip()
                return (not tag, tag.lower())

            decks.sort(key=_tag_sort_key, reverse=reverse)
        decks = decks[offset : offset + per]

    owner_summary = [
        hooks.DeckOwnerSummaryVM(
            key=item.get("key") or "",
            owner=item.get("owner"),
            label=item.get("label") or "Unassigned",
            deck_count=int(item.get("deck_count") or 0),
            card_total=int(item.get("card_total") or 0),
            proxy_count=int(item.get("proxy_count") or 0),
        )
        for item in hooks._owner_summary(decks)
    ]

    deck_vms: list[hooks.DeckVM] = []
    for deck in decks:
        folder_id = deck.get("id")
        commander_payload = deck_commanders.get(folder_id)
        commander_vm = None
        if commander_payload:
            images = [
                hooks.ImageSetVM(
                    small=image.get("small"),
                    normal=image.get("normal"),
                    large=image.get("large"),
                    label=image.get("label"),
                )
                for image in commander_payload.get("images", [])
            ]
            commander_vm = hooks.DeckCommanderVM(
                name=commander_payload.get("name"),
                small=commander_payload.get("small"),
                large=commander_payload.get("large"),
                alt=commander_payload.get("alt"),
                images=images,
            )
        bracket = deck.get("bracket") or {}
        deck_vms.append(
            hooks.DeckVM(
                id=folder_id,
                name=deck.get("name") or "",
                qty=int(deck.get("qty") or 0),
                owner=deck.get("owner"),
                owner_key=(deck.get("owner_key") or (deck.get("owner") or "").strip().lower()),
                is_proxy=bool(deck.get("is_proxy")),
                is_owner=bool(
                    hooks.current_user
                    and getattr(hooks.current_user, "is_authenticated", False)
                    and deck.get("owner_user_id") == hooks.current_user.id
                ),
                tag=deck.get("tag"),
                tag_label=deck.get("tag_label"),
                ci_name=deck_ci_name.get(folder_id) or "Colorless",
                ci_html=deck_ci_html.get(folder_id) or "",
                ci_letters=deck_ci_letters.get(folder_id) or "C",
                commander=commander_vm,
                bracket_level=str(bracket.get("level")) if bracket.get("level") is not None else None,
                bracket_label=bracket.get("label"),
            )
        )

    deck_tag_groups = hooks.get_deck_tag_groups()

    def _wizard_payload():
        wizard_query = (
            hooks.Folder.query.options(
                hooks.load_only(
                    hooks.Folder.id,
                    hooks.Folder.name,
                    hooks.Folder.commander_name,
                    hooks.Folder.commander_oracle_id,
                    hooks.Folder.deck_tag,
                ),
                hooks.selectinload(hooks.Folder.role_entries),
            )
            .filter(role_filter)
        )
        if hooks.current_user and getattr(hooks.current_user, "is_authenticated", False):
            wizard_query = wizard_query.filter(hooks.Folder.owner_user_id == hooks.current_user.id)
        wizard_folders = wizard_query.all()
        return hooks.build_deck_metadata_wizard_payload(wizard_folders, tag_groups=deck_tag_groups)

    wizard_payload = hooks.cache_fetch(f"deck_wizard:{current_user_key}", 120, _wizard_payload)

    def _url_with(page_num: int):
        args = hooks.request.args.to_dict(flat=False)
        args["page"] = [str(page_num)]
        if "per" not in args and "per_page" not in args:
            args["per"] = [str(per)]
        return hooks.url_for("views.decks_overview", **{k: v if len(v) > 1 else v[0] for k, v in args.items()})

    page_urls = [(page_number, _url_with(page_number)) for page_number in range(1, pages + 1)]
    page_url_map = {page_number: url for page_number, url in page_urls}

    return {
        "decks": deck_vms,
        "owner_summary": owner_summary,
        "owner_names": owner_names,
        "proxy_count": sum(1 for deck in decks if deck.get("is_proxy")),
        "proxy_total": proxy_total,
        "owned_total": owned_total,
        "friends_total": friends_total,
        "shared_total": shared_total,
        "total_decks": total_decks,
        "scope": scope,
        "show_scope_toggle": is_authenticated,
        "page": page,
        "pages": pages,
        "per_page": per,
        "page_url_map": page_url_map,
        "deck_tag_groups": deck_tag_groups,
        "deck_metadata_wizard": wizard_payload,
    }


__all__ = ["build_decks_overview_context"]
