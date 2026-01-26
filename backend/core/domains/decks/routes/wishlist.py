"""Wishlist management routes and helpers."""

from __future__ import annotations

import csv
import json
import re
from io import StringIO
from collections import defaultdict
from math import ceil

from flask import abort, flash, jsonify, make_response, redirect, render_template, request, url_for, current_app
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from extensions import db
from models import Card, Folder, FolderRole, UserFriend, WishlistItem
from core.domains.cards.services import scryfall_cache as sc
from core.shared.database import get_or_404

from core.routes.base import ALLOWED_WISHLIST_STATUSES, views


def _normalize_wishlist_rows(raw):
    """
    Normalize incoming 'rows' into a list[dict] with at least {'name': <str>}.
    Accepts strings (newline/comma separated), lists, dicts, or None.
    """
    if raw is None:
        return []

    if isinstance(raw, str):
        parts = [part.strip() for part in re.split(r"[\r\n,]+", raw) if part.strip()]
        return [{"name": part} for part in parts]

    if isinstance(raw, dict):
        return [raw]

    if isinstance(raw, list):
        out = []
        for item in raw:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    out.append({"name": stripped})
            else:
                current_app.logger.warning("wishlist_add: skipping unsupported row type: %r", type(item))
        return out

    current_app.logger.warning("wishlist_add: unsupported payload type: %r", type(raw))
    return []


def _parse_int(*values, default=1):
    for value in values:
        if value in (None, ""):
            continue
        try:
            return max(int(value), 1)
        except Exception:
            continue
    return default


def _normalize_order_ref(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _color_identity_for_oracle(oracle_id):
    if not oracle_id:
        return None
    try:
        prints = sc.prints_for_oracle(oracle_id)
    except Exception:
        return None
    if not prints:
        return None
    ci = prints[0].get("color_identity") or prints[0].get("colors")
    if isinstance(ci, list):
        ci = "".join(ci)
    return ci or None


def _color_identity_for_item(item):
    card = getattr(item, "card", None)
    if card:
        return card.color_identity or card.colors or None
    oracle_id = item.oracle_id
    ci = _color_identity_for_oracle(oracle_id)
    if ci:
        return ci
    try:
        oid = sc.unique_oracle_by_name(item.name)
    except Exception:
        oid = None
    if oid:
        return _color_identity_for_oracle(oid)
    return None




def _type_line_for_oracle(oracle_id):
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


def _type_line_for_item(item):
    card = getattr(item, "card", None)
    if card and getattr(card, "type_line", None):
        return card.type_line
    type_line = _type_line_for_oracle(item.oracle_id)
    if type_line:
        return type_line
    try:
        oid = sc.unique_oracle_by_name(item.name)
    except Exception:
        oid = None
    if oid:
        return _type_line_for_oracle(oid)
    return None


def _format_color_identity(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        tokens = [str(v).strip().upper() for v in value if str(v).strip()]
        letters = [tok for tok in tokens if tok in {"W", "U", "B", "R", "G"}]
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




def _split_folder_label(raw_name):
    text = (str(raw_name) if raw_name is not None else "").strip()
    if not text:
        return None, ""
    if ":" in text:
        owner_part, folder_part = text.split(":", 1)
        owner_part = owner_part.strip()
        folder_part = folder_part.strip()
        return owner_part or None, folder_part
    return None, text


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


def _build_wishlist_source_entries(items):
    current_user_id = current_user.id if current_user.is_authenticated else None
    friend_ids = set()
    if current_user_id:
        friend_rows = (
            db.session.query(UserFriend.friend_user_id)
            .filter(UserFriend.user_id == current_user_id)
            .all()
        )
        friend_ids = {fid for (fid,) in friend_rows if fid}

    name_candidates = set()
    for item in items:
        for entry in item.source_folders_list:
            raw_name = entry.get("name") if isinstance(entry, dict) else ""
            _owner_hint, folder_name = _split_folder_label(raw_name)
            if folder_name:
                name_candidates.add(folder_name)

    meta = _collection_folder_meta(name_candidates, current_user_id, friend_ids)

    entries_by_item = []
    max_sources = 0
    for item in items:
        entries = []
        for entry in item.source_folders_list:
            raw_name = entry.get("name") if isinstance(entry, dict) else ""
            owner_hint, folder_name = _split_folder_label(raw_name)
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
            if folder and folder.is_collection:
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

        entries.sort(key=lambda e: (e.get("rank", 2), (e.get("label") or "").lower()))
        for entry in entries:
            entry.pop("rank", None)
        entries_by_item.append(entries)
        if len(entries) > max_sources:
            max_sources = len(entries)

    return entries_by_item, max_sources


def _serialize_source_folders(value):
    if not value:
        return None
    data = value
    if isinstance(data, str):
        data = data.strip()
        if not data:
            return None
        try:
            parsed = json.loads(data)
            data = parsed if isinstance(parsed, (list, dict)) else [{"name": data}]
        except Exception:
            data = [{"name": data}]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return None
    cleaned = []
    for entry in data:
        if isinstance(entry, dict):
            name = str(entry.get("name", "")).strip()
            if not name:
                continue
            qty_raw = entry.get("qty")
            if qty_raw is None:
                qty_raw = entry.get("quantity") or entry.get("count")
            try:
                qty = int(qty_raw)
            except Exception:
                qty = None
            cleaned.append({"name": name, "qty": qty})
        elif isinstance(entry, str):
            name = entry.strip()
            if name:
                cleaned.append({"name": name, "qty": None})
    if not cleaned:
        return None
    return json.dumps(cleaned, ensure_ascii=False)


def _wishlist_upsert_rows(rows) -> tuple[int, int, int]:
    """
    Create/update wishlist items from a rows iterable.
    Returns (created, updated, skipped).
    Accepts each row as dict or str (name).
    """
    created = updated = skipped = 0

    for row in (rows or []):
        try:
            if isinstance(row, str):
                try:
                    row = json.loads(row)
                    if not isinstance(row, dict):
                        row = {"name": str(row)}
                except Exception:
                    row = {"name": row}

            if not isinstance(row, dict):
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

            scryfall_id = (row.get("scryfall_id") or row.get("scry_id")) or None
            oracle_id = row.get("oracle_id") or None
            try:
                card_id = int(row.get("card_id"))
            except Exception:
                card_id = None

            status_value = (row.get("status") or row.get("state") or "").strip().lower()
            if status_value and status_value not in ALLOWED_WISHLIST_STATUSES:
                status_value = None

            folders_json = _serialize_source_folders(row.get("source_folders") or row.get("folders"))
            order_ref = None
            order_ref_present = False
            for key in ("order_ref", "order_url", "order_number", "order"):
                if key in row:
                    order_ref_present = True
                    order_ref = _normalize_order_ref(row.get(key))
                    break

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
                if status_value:
                    item.status = status_value
                if card_id:
                    item.card_id = card_id
                if folders_json is not None:
                    item.source_folders = folders_json
                if order_ref_present:
                    item.order_ref = order_ref
                if item.status in {"acquired", "removed"}:
                    item.missing_qty = 0
                elif item.status == "to_fetch":
                    item.missing_qty = current_requested
                elif item.status in {"open", "ordered"}:
                    if item.missing_qty is None or item.missing_qty < current_requested:
                        item.missing_qty = current_requested
                else:
                    item.missing_qty = max(int(item.missing_qty or 0), current_requested)
                updated += 1
            else:
                effective_status = status_value if status_value in ALLOWED_WISHLIST_STATUSES else "open"
                missing_qty = 0 if effective_status in {"acquired", "removed"} else requested_qty
                item = WishlistItem(
                    name=name,
                    requested_qty=requested_qty,
                    missing_qty=missing_qty,
                    scryfall_id=scryfall_id,
                    oracle_id=oracle_id,
                    card_id=card_id,
                    status=effective_status,
                    source_folders=folders_json,
                    order_ref=order_ref,
                )
                db.session.add(item)
                created += 1

        except Exception:
            current_app.logger.exception("wishlist_upsert: failed to process row: %r", row)
            skipped += 1

    db.session.commit()
    return created, updated, skipped


@views.route("/wishlist", methods=["GET"])
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

    status_filter = (request.args.get("status") or "all").strip().lower()
    if status_filter not in ALLOWED_WISHLIST_STATUSES and status_filter != "all":
        status_filter = "all"

    base_query = (
        WishlistItem.query.options(
            selectinload(WishlistItem.card).selectinload(Card.folder).selectinload(Folder.owner_user)
        )
    )
    if sort in {"color", "location"}:
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
        order_col = func.lower(func.coalesce(Card.color_identity, Card.colors, ""))
    else:
        order_col = func.lower(WishlistItem.name)

    order_expr = order_col.desc() if reverse else order_col.asc()
    base_query = base_query.order_by(order_expr, func.lower(WishlistItem.name), WishlistItem.id.asc())
    total_items = base_query.order_by(None).count()
    pages = max(1, ceil(total_items / per)) if per else 1
    page = min(page, pages) if total_items else 1
    start = (page - 1) * per + 1 if total_items else 0
    end = min(start + per - 1, total_items) if total_items else 0

    items = base_query.limit(per).offset((page - 1) * per).all()
    for item in items:
        item.display_color_identity = _color_identity_for_item(item)

    source_entries, _ = _build_wishlist_source_entries(items)
    for item, entries in zip(items, source_entries):
        item.display_source_folders = entries

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
        total=total_items,
        sort=sort,
        direction=direction,
        status_filter=status_filter,
    )


@views.route("/wishlist/add-form", methods=["POST"])
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


@views.route("/wishlist/add", methods=["POST"])
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


@views.route("/wishlist/mark/<int:item_id>", methods=["POST"])
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


@views.route("/wishlist/update/<int:item_id>", methods=["POST"])
def wishlist_update(item_id: int):
    item = get_or_404(WishlistItem, item_id)
    try:
        new_qty = int((request.form.get("requested_qty") or "").strip() or 0)
    except ValueError:
        new_qty = 0
    if new_qty < 0:
        new_qty = 0

    item.requested_qty = new_qty
    if item.status in {"open", "ordered", "to_fetch"}:
        item.missing_qty = new_qty
    else:
        item.missing_qty = 0

    db.session.commit()
    return redirect(request.referrer or url_for("views.wishlist"))


@views.route("/wishlist/order/<int:item_id>", methods=["POST"])
def wishlist_order_ref(item_id: int):
    item = get_or_404(WishlistItem, item_id)
    order_ref = _normalize_order_ref(request.form.get("order_ref"))
    item.order_ref = order_ref
    db.session.commit()
    return redirect(request.referrer or url_for("views.wishlist"))


@views.route("/wishlist/export", methods=["GET"])
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


@views.route("/wishlist/delete/<int:item_id>", methods=["POST"])
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
    "wishlist",
    "wishlist_add",
    "wishlist_add_form",
    "wishlist_delete",
    "wishlist_export",
    "wishlist_mark",
    "wishlist_order_ref",
    "wishlist_update",
]
