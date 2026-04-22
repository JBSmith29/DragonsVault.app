"""Wishlist management services."""

from __future__ import annotations

import csv
import json
from io import StringIO
from math import ceil

from flask import abort, current_app, flash, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from extensions import db
from models import Card, Folder, FolderRole, FriendCardRequest, UserFriend, WishlistItem
from core.domains.decks.services import wishlist_display_service as display
from core.domains.decks.services import wishlist_mutation_service as mutation
from core.shared.database import get_or_404
from shared.wishlist import ALLOWED_WISHLIST_STATUSES


def _normalize_wishlist_rows(raw):
    return mutation.normalize_wishlist_rows(raw)


def _parse_int(*values, default=1):
    return mutation.parse_int(*values, default=default)


def _normalize_order_ref(value):
    return mutation.normalize_order_ref(value)


def _color_identity_for_oracle(oracle_id):
    return display.color_identity_for_oracle(oracle_id)


def _color_identity_for_item(item):
    return display.color_identity_for_item(item)


def _type_line_for_oracle(oracle_id):
    return display.type_line_for_oracle(oracle_id)


def _type_line_for_item(item):
    return display.type_line_for_item(item)


def _format_color_identity(value):
    return display.format_color_identity(value)


def _split_folder_label(raw_name):
    return display.split_folder_label(raw_name)


def _folder_is_collection(folder):
    return display.folder_is_collection(folder)


def _collection_folder_meta(folder_names, current_user_id, friend_ids):
    return display._collection_folder_meta(folder_names, current_user_id, friend_ids)


def _build_wishlist_source_entries(items):
    return display.build_wishlist_source_entries(items)


def _owner_rank(owner_user_id, current_user_id, friend_ids):
    return display._owner_rank(owner_user_id, current_user_id, friend_ids)


def _folder_owner_aliases(folder):
    return display._folder_owner_aliases(folder)


def _normalize_rarity(value):
    return display._normalize_rarity(value)


def _rarity_label(value):
    return display._rarity_label(value)


def _rarity_badge_class(value):
    return display._rarity_badge_class(value)


def _collection_folders_for_names(folder_names, current_user_id, friend_ids):
    return display._collection_folders_for_names(folder_names, current_user_id, friend_ids)


def _collection_card_lookup(folder_ids, oracle_ids, name_keys):
    return display._collection_card_lookup(folder_ids, oracle_ids, name_keys)


def _pick_collection_display_card(item, folders_by_name, cards_by_folder_oracle, cards_by_folder_name):
    return display._pick_collection_display_card(item, folders_by_name, cards_by_folder_oracle, cards_by_folder_name)


def _enrich_wishlist_display_prints(items):
    return display.enrich_wishlist_display_prints(items)


def _serialize_source_folders(value):
    return mutation.serialize_source_folders(value)


def _wishlist_upsert_rows(rows) -> tuple[int, int, int]:
    return mutation.wishlist_upsert_rows(rows)


def wishlist():
    try:
        page = int(request.args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    try:
        per = int(request.args.get("per") or request.args.get("per_page") or 100)
    except (TypeError, ValueError):
        per = 100

    page = max(page, 1)
    per = max(1, min(per, 500))

    sort = (request.args.get("sort") or "name").strip().lower()
    direction = (request.args.get("dir") or "asc").strip().lower()
    if direction not in {"asc", "desc"}:
        direction = "asc"
    reverse = direction == "desc"
    allowed_sorts = {"name", "color", "location", "requested", "missing", "status", "order"}
    if sort not in allowed_sorts:
        sort = "name"
    manual_color_sort = sort == "color"

    status_filter = (request.args.get("status") or "all").strip().lower()
    if status_filter not in ALLOWED_WISHLIST_STATUSES and status_filter != "all":
        status_filter = "all"

    base_query = (
        WishlistItem.query.options(
            selectinload(WishlistItem.card).selectinload(Card.folder).selectinload(Folder.owner_user)
        )
    )
    if sort == "location":
        base_query = base_query.outerjoin(Card, WishlistItem.card_id == Card.id).outerjoin(Folder, Card.folder_id == Folder.id)

    if status_filter != "all":
        base_query = base_query.filter(WishlistItem.status == status_filter)

    if sort == "name":
        order_col = func.lower(WishlistItem.name)
    elif sort == "requested":
        order_col = func.coalesce(WishlistItem.requested_qty, 0)
    elif sort == "missing":
        order_col = func.coalesce(WishlistItem.missing_qty, 0)
    elif sort == "status":
        order_col = func.lower(WishlistItem.status)
    elif sort == "order":
        order_col = func.lower(func.coalesce(WishlistItem.order_ref, ""))
    elif sort == "location":
        order_col = func.lower(func.coalesce(func.nullif(WishlistItem.source_folders, ""), Folder.name, ""))
    elif sort == "color":
        order_col = func.lower(WishlistItem.name)
    else:
        order_col = func.lower(WishlistItem.name)

    order_expr = order_col.desc() if reverse else order_col.asc()
    base_query = base_query.order_by(order_expr, func.lower(WishlistItem.name), WishlistItem.id.asc())
    if manual_color_sort:
        all_items = base_query.order_by(func.lower(WishlistItem.name), WishlistItem.id.asc()).all()
        for item in all_items:
            item.display_color_identity = _color_identity_for_item(item)
        all_items.sort(
            key=lambda item: (
                _format_color_identity(getattr(item, "display_color_identity", None)),
                (item.name or "").lower(),
                int(item.id or 0),
            ),
            reverse=reverse,
        )
        total_items = len(all_items)
        pages = max(1, ceil(total_items / per)) if per else 1
        page = min(page, pages) if total_items else 1
        start = (page - 1) * per + 1 if total_items else 0
        end = min(start + per - 1, total_items) if total_items else 0
        offset = (page - 1) * per
        items = all_items[offset : offset + per]
    else:
        total_items = base_query.order_by(None).count()
        pages = max(1, ceil(total_items / per)) if per else 1
        page = min(page, pages) if total_items else 1
        start = (page - 1) * per + 1 if total_items else 0
        end = min(start + per - 1, total_items) if total_items else 0
        items = base_query.limit(per).offset((page - 1) * per).all()
        for item in items:
            item.display_color_identity = _color_identity_for_item(item)

    _enrich_wishlist_display_prints(items)

    source_entries, _ = _build_wishlist_source_entries(items)
    for item, entries in zip(items, source_entries):
        item.display_source_folders = entries
        has_user = any(entry.get("rank") == 0 for entry in entries)
        has_friend = any(entry.get("rank") == 1 for entry in entries)
        item.in_friends_collection = bool(has_friend and not has_user)

    def _url_with(page_num: int):
        args = request.args.to_dict(flat=False)
        args["page"] = [str(page_num)]
        if "per" not in args and "per_page" not in args:
            args["per"] = [str(per)]
        return url_for("views.wishlist", **{k: v if len(v) > 1 else v[0] for k, v in args.items()})

    prev_url = _url_with(page - 1) if page > 1 else None
    next_url = _url_with(page + 1) if page < pages else None
    page_urls = [(n, _url_with(n)) for n in range(1, pages + 1)]

    rows = db.session.query(WishlistItem.status, func.count(WishlistItem.id)).group_by(WishlistItem.status).all()
    counts = {status: count for status, count in rows}
    open_count = counts.get("open", 0)
    to_fetch_count = counts.get("to_fetch", 0)
    ordered_count = counts.get("ordered", 0)
    acquired_count = counts.get("acquired", 0)
    removed_count = counts.get("removed", 0)
    requested_count = counts.get("requested", 0)
    rejected_count = counts.get("rejected", 0)
    return render_template(
        "decks/wishlist.html",
        items=items,
        page=page,
        pages=pages,
        per_page=per,
        prev_url=prev_url,
        next_url=next_url,
        page_urls=page_urls,
        start=start,
        end=end,
        open_count=open_count,
        to_fetch_count=to_fetch_count,
        ordered_count=ordered_count,
        acquired=acquired_count,
        removed=removed_count,
        requested=requested_count,
        rejected=rejected_count,
        total=total_items,
        sort=sort,
        direction=direction,
        status_filter=status_filter,
    )


def wishlist_add_form():
    rows_raw = request.form.get("rows", "")
    try:
        rows = json.loads(rows_raw) if rows_raw else []
    except Exception:
        rows = _normalize_wishlist_rows(rows_raw)

    created, updated, skipped = _wishlist_upsert_rows(rows)
    added = created + updated
    flash(f"Added {added} item(s) to wishlist.", "success" if added else "warning")
    return redirect(url_for("views.wishlist"))


def wishlist_request_friend():
    if not current_user.is_authenticated:
        flash("Sign in to request cards from friends.", "warning")
        return redirect(url_for("views.list_checker"))

    rows_raw = request.form.get("rows", "")
    try:
        rows = json.loads(rows_raw) if rows_raw else []
    except Exception:
        rows = _normalize_wishlist_rows(rows_raw)

    if not rows:
        flash("No friend requests to send.", "warning")
        return redirect(url_for("views.wishlist"))

    friend_ids = {
        fid for (fid,) in db.session.query(UserFriend.friend_user_id).filter(UserFriend.user_id == current_user.id).all()
    }
    if not friend_ids:
        flash("You don't have any friends to request from yet.", "warning")
        return redirect(url_for("views.wishlist"))

    created = updated = skipped = 0
    request_sent = 0

    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue

        friend_user_raw = row.get("friend_user_id") or row.get("recipient_user_id") or row.get("friend_id")
        try:
            friend_user_id = int(friend_user_raw)
        except Exception:
            skipped += 1
            continue
        if friend_user_id not in friend_ids:
            skipped += 1
            continue

        raw_name = row.get("name") or row.get("card") or ""
        if not isinstance(raw_name, str):
            raw_name = str(raw_name or "")
        name = raw_name.strip()
        if not name or name.lower() == "[object object]":
            skipped += 1
            continue

        requested_qty = _parse_int(
            row.get("requested_qty"),
            row.get("missing_qty"),
            row.get("requested"),
            row.get("qty"),
            row.get("quantity"),
            default=1,
        )
        if requested_qty <= 0:
            skipped += 1
            continue

        scryfall_id = (row.get("scryfall_id") or row.get("scry_id")) or None
        oracle_id = row.get("oracle_id") or None
        try:
            card_id = int(row.get("card_id"))
        except Exception:
            card_id = None

        folders_json = _serialize_source_folders(row.get("source_folders") or row.get("folders"))

        query = WishlistItem.query
        item = None
        if scryfall_id:
            item = query.filter_by(scryfall_id=scryfall_id).first()
        if item is None and oracle_id:
            item = query.filter_by(oracle_id=oracle_id).first()
        if item is None:
            item = query.filter_by(name=name).first()

        if item:
            prev_requested = int(item.requested_qty or 0)
            if requested_qty > prev_requested:
                item.requested_qty = requested_qty
            current_requested = int(item.requested_qty or requested_qty)
            item.status = "requested"
            if card_id:
                item.card_id = card_id
            if folders_json is not None:
                item.source_folders = folders_json
            item.missing_qty = current_requested
            updated += 1
        else:
            item = WishlistItem(
                name=name,
                requested_qty=requested_qty,
                missing_qty=requested_qty,
                scryfall_id=scryfall_id,
                oracle_id=oracle_id,
                card_id=card_id,
                status="requested",
                source_folders=folders_json,
            )
            db.session.add(item)
            db.session.flush()
            created += 1

        if item and item.id:
            req = FriendCardRequest.query.filter_by(
                requester_user_id=current_user.id,
                recipient_user_id=friend_user_id,
                wishlist_item_id=item.id,
            ).first()
            if req:
                req.status = "pending"
                req.requested_qty = requested_qty
            else:
                db.session.add(
                    FriendCardRequest(
                        requester_user_id=current_user.id,
                        recipient_user_id=friend_user_id,
                        wishlist_item_id=item.id,
                        requested_qty=requested_qty,
                        status="pending",
                    )
                )
            request_sent += 1
        else:
            skipped += 1

    db.session.commit()
    if request_sent:
        flash(f"Sent {request_sent} friend request(s).", "success")
    else:
        flash("No friend requests were sent.", "warning")
    return redirect(url_for("views.wishlist"))


def friend_card_request_action():
    if not current_user.is_authenticated:
        flash("Sign in to respond to friend requests.", "warning")
        return redirect(url_for("views.shared_folders"))

    action = (request.form.get("action") or "").strip().lower()
    request_id_raw = request.form.get("request_id")
    try:
        request_id = int(request_id_raw)
    except Exception:
        flash("Invalid request selection.", "warning")
        return redirect(url_for("views.shared_folders"))

    if action not in {"accept", "reject"}:
        flash("Unknown request action.", "warning")
        return redirect(url_for("views.shared_folders"))

    req = FriendCardRequest.query.filter_by(
        id=request_id,
        recipient_user_id=current_user.id,
    ).first()
    if not req:
        flash("Request not found.", "warning")
        return redirect(url_for("views.shared_folders"))

    item = WishlistItem.query.get(req.wishlist_item_id) if req.wishlist_item_id else None
    if action == "accept":
        req.status = "accepted"
        if item and item.status == "requested":
            item.status = "to_fetch"
            item.missing_qty = item.requested_qty or 0
        flash("Request accepted.", "success")
    elif action == "reject":
        req.status = "rejected"
        if item and item.status == "requested":
            item.status = "rejected"
            item.missing_qty = item.requested_qty or 0
        flash("Request declined.", "info")

    db.session.commit()
    return redirect(url_for("views.shared_folders"))


def wishlist_add():
    payload = request.get_json(silent=True) if request.is_json else None
    if payload:
        raw_rows = payload.get("rows") or payload.get("items") or payload.get("data")
    else:
        raw_rows = request.form.get("rows") or request.form.get("items") or request.form.get("data")
        if isinstance(raw_rows, str):
            try:
                raw_rows = json.loads(raw_rows)
            except Exception:
                pass

    rows = _normalize_wishlist_rows(raw_rows)

    if not rows:
        return jsonify({"ok": True, "added": 0, "created": 0, "updated": 0, "skipped": 0})

    created, updated, skipped = _wishlist_upsert_rows(rows)
    added = created + updated
    return jsonify({"ok": True, "added": added, "created": created, "updated": updated, "skipped": skipped})


def wishlist_mark(item_id: int):
    item = get_or_404(WishlistItem, item_id)
    status = (request.form.get("status") or "").strip().lower()

    if status not in ALLOWED_WISHLIST_STATUSES:
        abort(400, f"Invalid status: {status}")

    item.status = status

    if status in {"acquired", "removed"}:
        item.missing_qty = 0
    else:
        item.missing_qty = item.requested_qty or 0

    db.session.commit()
    return redirect(request.referrer or url_for("views.wishlist"))


def wishlist_update(item_id: int):
    item = get_or_404(WishlistItem, item_id)
    try:
        new_qty = int((request.form.get("requested_qty") or "").strip() or 0)
    except ValueError:
        new_qty = 0
    if new_qty < 0:
        new_qty = 0

    item.requested_qty = new_qty
    if item.status in {"open", "ordered", "to_fetch", "requested", "rejected"}:
        item.missing_qty = new_qty
    else:
        item.missing_qty = 0

    db.session.commit()
    return redirect(request.referrer or url_for("views.wishlist"))


def wishlist_order_ref(item_id: int):
    item = get_or_404(WishlistItem, item_id)
    order_ref = _normalize_order_ref(request.form.get("order_ref"))
    item.order_ref = order_ref
    db.session.commit()
    return redirect(request.referrer or url_for("views.wishlist"))


def wishlist_export():
    items = (
        WishlistItem.query.options(
            selectinload(WishlistItem.card).selectinload(Card.folder).selectinload(Folder.owner_user)
        )
        .order_by(
            WishlistItem.status.asc(), WishlistItem.created_at.desc(), WishlistItem.name.asc()
        )
        .all()
    )

    source_entries, max_sources = _build_wishlist_source_entries(items)
    source_col_count = max(1, max_sources)

    buf = StringIO()
    writer = csv.writer(buf)
    header = ["Card Name", "Qty Requested", "Card Type", "Color"]
    header.extend([f"Source Folder {idx}" for idx in range(1, source_col_count + 1)])
    header.append("Status")
    writer.writerow(header)

    for item, entries in zip(items, source_entries):
        color_value = _format_color_identity(_color_identity_for_item(item))
        type_line = _type_line_for_item(item) or ""
        labels = []
        for entry in entries:
            label = entry.get("label") or ""
            labels.append(label.replace(": ", ":"))
        row = [item.name, item.requested_qty or 0, type_line, color_value]
        row.extend(labels)
        if len(labels) < source_col_count:
            row.extend([""] * (source_col_count - len(labels)))
        row.append(item.status)
        writer.writerow(row)

    response = make_response(buf.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=wishlist.csv"
    return response


def wishlist_delete(item_id):
    item = db.session.get(WishlistItem, item_id)
    if not item:
        if request.headers.get("HX-Request"):
            return ("", 204)
        flash("Wishlist item not found.", "warning")
        return redirect(url_for("views.wishlist"))

    db.session.delete(item)
    db.session.commit()

    if request.headers.get("HX-Request"):
        return ("", 204)

    flash(f'Removed "{item.name}" from wishlist.', "success")
    return redirect(request.referrer or url_for("views.wishlist"))


__all__ = [
    "friend_card_request_action",
    "wishlist",
    "wishlist_add",
    "wishlist_add_form",
    "wishlist_delete",
    "wishlist_export",
    "wishlist_mark",
    "wishlist_order_ref",
    "wishlist_request_friend",
    "wishlist_update",
]
