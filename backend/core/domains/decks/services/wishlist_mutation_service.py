"""Wishlist row normalization and mutation helpers."""

from __future__ import annotations

import json
import re

from flask import current_app

from extensions import db
from models import WishlistItem
from shared.wishlist import ALLOWED_WISHLIST_STATUSES


def normalize_wishlist_rows(raw):
    """
    Normalize incoming 'rows' into a list[dict] with at least {'name': <str>}.
    Accepts strings, lists, dicts, or None.
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


def parse_int(*values, default=1):
    for value in values:
        if value in (None, ""):
            continue
        try:
            return max(int(value), 1)
        except Exception:
            continue
    return default


def normalize_order_ref(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def serialize_source_folders(value):
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


def wishlist_upsert_rows(rows) -> tuple[int, int, int]:
    """Create or update wishlist items from a row iterable."""
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

            requested_qty = parse_int(
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

            folders_json = serialize_source_folders(row.get("source_folders") or row.get("folders"))
            order_ref = None
            order_ref_present = False
            for key in ("order_ref", "order_url", "order_number", "order"):
                if key in row:
                    order_ref_present = True
                    order_ref = normalize_order_ref(row.get(key))
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
                elif item.status in {"open", "ordered", "requested", "rejected"}:
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
