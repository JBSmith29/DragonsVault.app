"""Card export service."""

from __future__ import annotations

import csv
from io import StringIO

from flask import Response, request
from flask_login import current_user
from sqlalchemy import case, or_

from extensions import db
from models import Card, Folder, UserFriend
from shared.mtg import _collector_number_numeric, _name_sort_expr
from shared.validation import ValidationError, log_validation_error, parse_positive_int_list


def export_cards() -> Response:
    """Export the current card selection as CSV."""
    q = (request.args.get("q") or "").strip()
    folder_id_raw = (request.args.get("folder") or "").strip()
    set_code = (request.args.get("set") or "").strip().lower()
    lang = (request.args.get("lang") or "").strip().lower()
    foil_arg = (request.args.get("foil_only") or request.args.get("foil") or "").strip().lower()
    foil_only = foil_arg in {"1", "true", "yes", "on", "y"}
    show_friends_arg = (request.args.get("show_friends") or "").strip().lower()
    show_friends = show_friends_arg in {"1", "true", "yes", "on", "y"}
    is_authenticated = bool(current_user and getattr(current_user, "is_authenticated", False))
    if not is_authenticated:
        show_friends = False
    folder_filters: set[int] = set()
    folder_args = request.args.getlist("folder_ids") or request.args.getlist("folders")
    try:
        parsed_ids = parse_positive_int_list(folder_args, field="folder id(s)")
        if folder_id_raw:
            parsed_ids.extend(parse_positive_int_list([folder_id_raw], field="folder id"))
    except ValidationError as exc:
        log_validation_error(exc, context="export_cards")
        return Response("Invalid folder selection.", status=400, mimetype="text/plain")
    folder_filters.update(parsed_ids)

    include_all_folders = (request.args.get("all_folders") or "").strip().lower() in {"1", "true", "yes", "on"}

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
    if q:
        for tok in [value for value in q.split() if value]:
            query = query.filter(Card.name.ilike(f"%{tok}%"))
    if folder_filters and not include_all_folders:
        query = query.filter(Card.folder_id.in_(folder_filters))
    if set_code:
        query = query.filter(Card.set_code.ilike(set_code))
    if lang:
        query = query.filter(Card.lang.ilike(lang))
    if foil_only:
        query = query.filter(Card.is_foil.is_(True))

    name_col = _name_sort_expr()
    cn_num = _collector_number_numeric()
    cn_numeric_last = case((cn_num.is_(None), 1), else_=0)
    rows = (
        query.order_by(
            name_col.asc(),
            Card.set_code.asc(),
            cn_numeric_last.asc(),
            cn_num.asc(),
            Card.collector_number.asc(),
        ).all()
    )

    export_format = (request.args.get("format") or request.args.get("style") or "").strip().lower()
    si = StringIO()
    writer = csv.writer(si)
    filename = "cards_export.csv"

    if export_format == "manavault":
        filename = "dragonsvault-manavault.csv"
        writer.writerow(["Count", "Name", "Edition", "Collector Number", "Language", "Finish"])
        for c in rows:
            writer.writerow(
                [
                    c.quantity or 1,
                    c.name,
                    (c.set_code or "").upper(),
                    c.collector_number or "",
                    (c.lang or "en").upper(),
                    "Foil" if c.is_foil else "Nonfoil",
                ]
            )
    elif export_format == "manabox":
        filename = "dragonsvault-manabox.csv"
        writer.writerow(["Count", "Name", "Edition", "Collector Number", "Finish"])
        for c in rows:
            writer.writerow(
                [
                    c.quantity or 1,
                    c.name,
                    (c.set_code or "").upper(),
                    c.collector_number or "",
                    "Foil" if c.is_foil else "Nonfoil",
                ]
            )
    elif export_format == "dragonshield":
        filename = "dragonsvault-dragonshield.csv"
        writer.writerow(["Quantity", "Name", "Set Code", "Collector Number", "Printing", "Condition", "Language"])
        for c in rows:
            writer.writerow(
                [
                    c.quantity or 1,
                    c.name,
                    (c.set_code or "").upper(),
                    c.collector_number or "",
                    "Foil" if c.is_foil else "Normal",
                    "Near Mint",
                    (c.lang or "English"),
                ]
            )
    else:
        writer.writerow(["Folder Name", "Quantity", "Card Name", "Set Code", "Collector Number", "Language", "Printing"])
        for c in rows:
            writer.writerow(
                [
                    c.folder.name if c.folder else "",
                    c.quantity or 1,
                    c.name,
                    c.set_code,
                    c.collector_number,
                    c.lang or "en",
                    "Foil" if c.is_foil else "Nonfoil",
                ]
            )

    out = si.getvalue()
    return Response(
        out,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


__all__ = ["export_cards"]
