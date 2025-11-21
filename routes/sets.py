"""Set overview, gallery, and detail routes."""

from __future__ import annotations

import re
from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import case, func
from sqlalchemy.orm import load_only

from extensions import db
from models import Card
from services import scryfall_cache as sc
from services.scryfall_cache import (
    all_set_codes,
    ensure_cache_loaded,
    search_prints,
    set_name_for_code,
    set_release_for_code,
    set_profiles,
)
from .base import (
    _bulk_print_lookup,
    _collector_number_numeric,
    _folder_id_name_map,
    _lookup_print_data,
    _name_sort_expr,
    _prices_for_print,
    views,
)


@views.route("/sets")
def sets_overview():
    q = (request.args.get("q") or "").strip().lower()
    sort = (request.args.get("sort") or "").strip().lower()
    direction = (request.args.get("dir") or "asc").strip().lower()
    reverse = direction == "desc"
    color_filter = (request.args.get("color") or "").strip().lower()
    curve_filter = (request.args.get("curve") or "").strip().lower()

    agg = (
        db.session.query(
            Card.set_code.label("set_code"),
            func.count(Card.id).label("row_count"),
            func.coalesce(func.sum(Card.quantity), 0).label("qty_sum"),
        )
        .filter(Card.set_code != None)  # noqa: E711
        .filter(Card.is_proxy.is_(False))
        .group_by(Card.set_code)
        .order_by(func.coalesce(func.sum(Card.quantity), 0).desc())
        .all()
    )

    have_cache = ensure_cache_loaded()

    owned_stats = {}
    for scode, rows, qty in agg:
        if not scode:
            continue
        owned_stats[scode.lower()] = {"rows": int(rows or 0), "qty": int(qty or 0)}

    if have_cache:
        codes = set(all_set_codes())
    else:
        codes = set()
    codes.update(owned_stats.keys())

    profile_map = set_profiles(codes)
    name_map = {}
    release_map = {}
    if have_cache:
        name_map = {code: set_name_for_code(code) for code in codes}
        release_map = {code: set_release_for_code(code) for code in codes}


    items = []
    for code in sorted(codes):
        display_code = code.upper()
        name = name_map.get(code) if have_cache else None
        release = release_map.get(code) if have_cache else None
        release_display = None
        if release:
            try:
                release_display = datetime.strptime(release, "%Y-%m-%d").strftime("%b %d, %Y").replace(" 0", " ")
            except Exception:
                release_display = release
        stats = owned_stats.get(code, {"rows": 0, "qty": 0})
        rec = {
            "set_code": code,
            "set_name": name or display_code,
            "rows": stats.get("rows", 0),
            "qty": stats.get("qty", 0),
            "release_iso": release,
            "release_display": release_display,
        }
        if q and (q not in code) and (q not in (rec["set_name"] or "").lower()):
            continue
        items.append(rec)

    if sort == "code":
        items.sort(key=lambda r: r["set_code"], reverse=reverse)
    elif sort == "name":
        items.sort(key=lambda r: (r["set_name"] or "").lower(), reverse=reverse)
    elif sort == "rows":
        items.sort(key=lambda r: r["rows"], reverse=reverse)
    elif sort == "qty":
        items.sort(key=lambda r: r["qty"], reverse=reverse)
    elif sort == "release":
        def release_key(r):
            iso = r.get("release_iso")
            if iso:
                return iso
            return "0000-00-00" if reverse else "9999-12-31"

        items.sort(key=release_key, reverse=reverse)
    else:
        # Default: newest release first; fall back to name for ties or missing dates.
        items.sort(key=lambda r: (r["set_name"] or "").lower())
        items.sort(
            key=lambda r: r.get("release_iso") or "0000-00-00",
            reverse=True,
        )

    return render_template(
        "cards/sets.html",
        sets=items,
        q=q,
    )


@views.route("/sets/<set_code>/gallery")
def set_gallery(set_code):
    code = (set_code or "").strip().lower()
    name_query = (request.args.get("q") or "").strip()
    rarity_filter = (request.args.get("rarity") or "").strip().lower()
    if not code:
        abort(404)

    if not ensure_cache_loaded():
        flash("Scryfall cache not loaded yet. Load the default cache on the Admin page first.", "warning")
        return redirect(url_for("views.sets_overview"))

    set_name = set_name_for_code(code) or code.upper()

    prints, total = search_prints(set_code=code, limit=5000, offset=0)

    owned_rows = (
        db.session.query(Card.collector_number, func.coalesce(func.sum(Card.quantity), 0))
        .filter(Card.set_code.ilike(code))
        .filter(Card.is_proxy.is_(False))
        .group_by(Card.collector_number)
        .all()
    )
    owned_map = {str(cn or "").strip().lower(): int(qty or 0) for cn, qty in owned_rows}
    owned_total = sum(owned_map.values())

    def _image_for_print(pr):
        iu = pr.get("image_uris") or {}
        faces = pr.get("card_faces") or []
        small = iu.get("small")
        normal = iu.get("normal") or iu.get("large")
        if (not small or not normal) and faces and isinstance(faces, list):
            iu2 = (faces[0] or {}).get("image_uris") or {}
            small = small or iu2.get("small")
            normal = normal or iu2.get("normal") or iu2.get("large")
        return small, normal

    def _cn_sort_key(value):
        s = str(value or "").strip()
        match = re.match(r"(\d+)", s)
        if match:
            num = int(match.group(1))
            suffix = s[match.end() :].lower()
            return (0, num, suffix)
        return (1, s.lower())

    def _print_sort_key(pr):
        return (
            _cn_sort_key(pr.get("collector_number")),
            (pr.get("lang") or "").upper(),
            pr.get("name") or "",
        )

    cards = []
    release_dates = []
    for pr in sorted(prints, key=_print_sort_key):
        small, normal = _image_for_print(pr)
        cn = str(pr.get("collector_number") or "").strip()
        owned_qty = owned_map.get(cn.lower(), 0)
        release_date = pr.get("released_at")
        if release_date:
            release_dates.append(release_date)

        local_card_id = (
            Card.query.filter(
                Card.set_code.ilike(code),
                func.lower(Card.collector_number) == cn.lower(),
                Card.is_proxy.is_(False),
            )
            .with_entities(Card.id)
            .first()
        )
        local_card_id = local_card_id[0] if local_card_id else None

        cards.append(
            {
                "id": pr.get("id"),
                "name": pr.get("name"),
                "collector_number": cn,
                "lang": (pr.get("lang") or "").upper(),
                "rarity": (pr.get("rarity") or ""),
                "thumb": small,
                "normal": normal,
                "scryfall_uri": pr.get("scryfall_uri"),
                "card_id": local_card_id,
                "owned_qty": owned_qty,
            }
        )

    first_release = min(release_dates) if release_dates else None

    rarity_options = sorted(
        {
            (card["rarity"] or "").strip().lower()
            for card in cards
            if card.get("rarity")
        }
    )

    name_query_lower = name_query.lower()
    filtered_cards = []
    for card in cards:
        if name_query and name_query_lower not in (card.get("name") or "").lower():
            continue
        if rarity_filter and (card.get("rarity") or "").lower() != rarity_filter:
            continue
        filtered_cards.append(card)

    filtered_count = len(filtered_cards)

    return render_template(
        "cards/set_gallery.html",
        set_code=code,
        set_name=set_name,
        total_prints=total,
        owned_total=owned_total,
        release_date=first_release,
        cards=filtered_cards,
        filtered_count=filtered_count,
        name_query=name_query,
        rarity_filter=rarity_filter,
        rarity_options=rarity_options,
    )


@views.route("/sets/<set_code>")
def set_detail(set_code):
    """Legacy route retained for compatibility; redirect to the gallery view."""
    params = dict(request.args.items())
    return redirect(url_for("views.set_gallery", set_code=set_code, **params))



__all__ = ["set_detail", "set_gallery", "sets_overview"]
