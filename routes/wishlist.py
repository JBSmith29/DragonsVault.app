"""Wishlist management routes and helpers."""

from __future__ import annotations

import csv
import json
import re
from io import StringIO

from flask import abort, flash, jsonify, make_response, redirect, render_template, request, url_for, current_app
from sqlalchemy import func

from extensions import db
from models import WishlistItem

from .base import ALLOWED_WISHLIST_STATUSES, views


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
    items = (
        WishlistItem.query.order_by(
            WishlistItem.status.asc(), WishlistItem.created_at.desc(), WishlistItem.name.asc()
        ).all()
    )

    rows = db.session.query(WishlistItem.status, func.count(WishlistItem.id)).group_by(WishlistItem.status).all()
    counts = {status: count for status, count in rows}
    open_count = counts.get("open", 0)
    to_fetch_count = counts.get("to_fetch", 0)
    ordered_count = counts.get("ordered", 0)
    acquired_count = counts.get("acquired", 0)
    removed_count = counts.get("removed", 0)
    total = open_count + to_fetch_count + ordered_count + acquired_count + removed_count

    return render_template(
        "decks/wishlist.html",
        items=items,
        open_count=open_count,
        to_fetch_count=to_fetch_count,
        ordered_count=ordered_count,
        acquired=acquired_count,
        removed=removed_count,
        total=total,
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
    item = WishlistItem.query.get_or_404(item_id)
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
    item = WishlistItem.query.get_or_404(item_id)
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
    return redirect(url_for("views.wishlist"))


@views.route("/wishlist/export", methods=["GET"])
def wishlist_export():
    items = (
        WishlistItem.query.order_by(
            WishlistItem.status.asc(), WishlistItem.created_at.desc(), WishlistItem.name.asc()
        ).all()
    )
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["name", "requested_qty", "status", "added_at", "oracle_id", "scryfall_id", "source_folders"]
    )
    for item in items:
        writer.writerow(
            [
                item.name,
                item.requested_qty,
                item.status,
                (item.created_at.isoformat(sep=" ", timespec="minutes") if item.created_at else ""),
                item.oracle_id or "",
                item.scryfall_id or "",
                item.source_folders or "",
            ]
        )
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
    return redirect(url_for("views.wishlist"))


__all__ = [
    "wishlist",
    "wishlist_add",
    "wishlist_add_form",
    "wishlist_delete",
    "wishlist_export",
    "wishlist_mark",
    "wishlist_update",
]
