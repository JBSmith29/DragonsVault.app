"""Scryfall-related service functions."""

from __future__ import annotations

import json
import re
from datetime import datetime
from math import ceil
from types import SimpleNamespace
from urllib.parse import urlencode
from urllib.request import urlopen

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, or_

from extensions import db
from models import Card, Folder
from models.role import OracleCoreRoleTag, OracleEvergreenTag
from services import scryfall_cache as sc
from services.scryfall_cache import (
    all_set_codes,
    ensure_cache_loaded,
    rulings_for_oracle,
    search_prints,
    set_name_for_code,
    set_profiles,
    set_release_for_code,
)
from services.scryfall_search import build_query, search_cards
from services.symbols_cache import colors_to_icons, ensure_symbols_cache, render_mana_html, render_oracle_html
from services.request_cache import request_cached
from viewmodels.card_vm import ScryfallCardVM, SetGalleryCardVM
from viewmodels.set_vm import SetSummaryVM

from routes.base import (
    API_PAGE_SIZE,
    _collection_metadata,
    _format_price_text,
    _prices_for_print,
)

RARITY_CHOICES = [
    {"value": "common", "label": "Common"},
    {"value": "uncommon", "label": "Uncommon"},
    {"value": "rare", "label": "Rare"},
    {"value": "mythic", "label": "Mythic"},
    {"value": "special", "label": "Special"},
    {"value": "bonus", "label": "Bonus"},
    {"value": "masterpiece", "label": "Masterpiece"},
    {"value": "timeshifted", "label": "Timeshifted"},
    {"value": "basic", "label": "Basic"},
]

BASE_TYPES = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]
RARITY_CLASS_MAP = {
    "common": "secondary",
    "uncommon": "success",
    "rare": "warning",
    "mythic": "danger",
    "mythic rare": "danger",
}


def _type_badges(type_line: str | None) -> list[str]:
    if not type_line:
        return []
    return [t for t in BASE_TYPES if t in type_line]


def _rarity_badge_class(rarity_value: str | None) -> str | None:
    if not rarity_value:
        return None
    return RARITY_CLASS_MAP.get(rarity_value.lower(), "secondary")


def _price_lines(prices: dict | None) -> list[str]:
    if not prices:
        return []
    lines = []
    if prices.get("usd"):
        lines.append(f"USD {prices['usd']}")
    if prices.get("usd_foil"):
        lines.append(f"USD Foil {prices['usd_foil']}")
    if prices.get("usd_etched"):
        lines.append(f"USD Etched {prices['usd_etched']}")
    return lines


def scryfall_browser():
    """
    Scryfall browser backed by /cards/search with:
      q, set, type (multi), typal, color (multi), color_mode, unique, commander, foil (y/n)
    """
    args = request.args
    has_query = bool(args)

    name = (args.get("q") or "").strip()
    set_code = (args.get("set") or "").strip().lower()
    rarity_value = (args.get("rarity") or "").strip().lower()
    if rarity_value == "any":
        rarity_value = ""
    rarity_label = "Any rarity"
    for choice in RARITY_CHOICES:
        if choice["value"] == rarity_value:
            rarity_label = choice["label"]
            break

    set_options: list[dict[str, str]] = []
    try:
        if ensure_cache_loaded():
            for code in all_set_codes():
                label = code.upper()
                set_name = set_name_for_code(code)
                if set_name:
                    label = f"{label} ({set_name})"
                set_options.append({"code": code, "label": label})
    except Exception:
        set_options = []

    base_types = [t for t in args.getlist("type") if t]
    typal = (args.get("typal") or "").strip()
    role_query_text = (args.get("role_q") or "").strip()

    color_filters = [c for c in args.getlist("color") if c]
    selected_colors = [c.upper() for c in color_filters]
    color_mode = (args.get("color_mode") or "contains").lower()
    if color_mode not in {"contains", "exact"}:
        color_mode = "contains"

    sort_field = (args.get("sort") or "name").lower()
    if sort_field not in {"name", "cmc", "rarity", "set", "collector", "mana", "type", "price", "art"}:
        sort_field = "name"
    sort_direction = (args.get("dir") or "asc").lower()
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "asc"

    unique_on = (("unique" in args) or (not has_query))
    commander_on = (("commander" in args) or (not has_query))
    unique = "cards" if unique_on else "prints"

    order_map = {
        "name": "name",
        "cmc": "cmc",
        "rarity": "rarity",
        "set": "set",
        "collector": "set",
        "mana": "cmc",
        "type": "name",
        "price": "name",
        "art": "name",
    }
    order_param = order_map.get(sort_field, "name")

    allowed_per_page = (25, 50, 100, 150, 200)
    try:
        per = int(args.get("per", allowed_per_page[0]))
    except Exception:
        per = allowed_per_page[0]
    if per not in allowed_per_page:
        per = allowed_per_page[0]
    try:
        page = max(1, int(args.get("page", 1)))
    except Exception:
        page = 1

    q = build_query(
        name=name,
        set_code=set_code,
        base_types=base_types,
        typal=typal,
        colors=[c for c in selected_colors if c in {"W", "U", "B", "R", "G"}],
        color_mode=color_mode,
        commander_only=commander_on,
        rarity=rarity_value,
    )

    global_start = (page - 1) * per
    scry_page = (global_start // API_PAGE_SIZE) + 1
    offset_in_page = global_start % API_PAGE_SIZE

    collected, total_cards = [], 0
    got_total, remaining, guard = False, per, 0

    while remaining > 0 and guard < 5:
        guard += 1
        payload = search_cards(q, unique=unique, page=scry_page, order=order_param, direction=sort_direction)
        data = payload.get("data", [])
        if not got_total:
            total_cards = int(payload.get("total_cards", 0))
            got_total = True

        start_idx = offset_in_page if guard == 1 else 0
        take = max(0, min(remaining, len(data) - start_idx))
        if take > 0:
            collected.extend(data[start_idx : start_idx + take])
            remaining -= take

        if remaining <= 0 or not payload.get("has_more", False):
            break

        scry_page += 1
        offset_in_page = 0

    ensure_symbols_cache(force=False)

    rarity_order = {
        "common": 0,
        "uncommon": 1,
        "rare": 2,
        "mythic": 3,
        "mythic rare": 3,
        "special": 4,
        "bonus": 5,
    }

    def _collector_key(value):
        if not value:
            return (float("inf"), "")
        digits, suffix = [], []
        for ch in str(value):
            (digits if ch.isdigit() else suffix).append(ch)
        try:
            number = int("".join(digits)) if digits else float("inf")
        except Exception:
            number = float("inf")
        return (number, "".join(suffix))

    def _price_to_float(value):
        if value in (None, "", 0, "0", "0.0", "0.00"):
            return None
        try:
            num = float(value)
        except (TypeError, ValueError):
            return None
        return num if num > 0 else None

    def _price_value_from_prices(prices: dict | None) -> float | None:
        if not prices:
            return None
        for key in ("usd", "usd_foil", "usd_etched", "eur", "eur_foil", "tix"):
            val = _price_to_float(prices.get(key))
            if val is not None:
                return val
        return None

    results = []
    for pr in collected:
        iu = pr.get("image_uris") or {}
        faces = pr.get("card_faces") or []
        thumb = iu.get("small") or (faces[0].get("image_uris", {}).get("small") if faces else None)
        normal = iu.get("normal") or iu.get("large") or (faces[0].get("image_uris", {}).get("normal") if faces else None)
        large = iu.get("large") or normal or (faces[0].get("image_uris", {}).get("large") if faces else None)
        images = [{"small": thumb, "normal": normal, "label": ""}] if (thumb or normal) else []
        prices = _prices_for_print(pr)
        price_text = _format_price_text(prices)
        preferred_set = (set_code.upper() if set_code else (pr.get("set") or "").upper()) or None

        raw_mana = pr.get("mana_cost")
        if not raw_mana and faces:
            mana_parts = [face.get("mana_cost") for face in faces if face.get("mana_cost")]
            raw_mana = " // ".join(mana_parts) if mana_parts else None
        mana_cost_html = render_mana_html(raw_mana, use_local=True) if raw_mana else "-"

        cmc_val = pr.get("cmc")
        if cmc_val is None:
            cmc_display = "-"
            cmc_numeric = None
        else:
            try:
                cmc_numeric = float(cmc_val)
                cmc_display = str(int(round(float(cmc_val))))
            except Exception:
                cmc_numeric = None
                cmc_display = str(cmc_val)
        rarity_value_local = (pr.get("rarity") or "").lower()
        rarity_label_local = rarity_value_local.title() if rarity_value_local else None
        rarity_rank = rarity_order.get(rarity_value_local, 99)

        collector_value = pr.get("collector_number")
        collector_sort = _collector_key(collector_value)

        results.append(
            {
                "id": pr.get("id"),
                "name": pr.get("name"),
                "primary_role": None,
                "set": (pr.get("set") or "").upper(),
                "set_name": pr.get("set_name"),
                "collector_number": pr.get("collector_number"),
                "lang": (pr.get("lang") or "").upper(),
                "rarity": (pr.get("rarity") or ""),
                "scryfall_uri": pr.get("scryfall_uri"),
                "tcgplayer_url": (pr.get("purchase_uris") or {}).get("tcgplayer")
                or (pr.get("related_uris") or {}).get("tcgplayer"),
                "prints_uri": pr.get("prints_search_uri"),
                "oracle_id": pr.get("oracle_id"),
                "thumb": thumb,
                "images": images,
                "image_large": large,
                "mana_cost": raw_mana,
                "mana_cost_html": mana_cost_html,
                "cmc_value": cmc_numeric,
                "cmc_display": cmc_display,
                "type_line": pr.get("type_line"),
                "rarity_value": rarity_value_local,
                "rarity_label": rarity_label_local,
                "rarity_rank": rarity_rank,
                "price_text": price_text,
                "prices": prices,
                "price_value": _price_value_from_prices(prices),
                "preferred_set": preferred_set,
                "game_changer": bool(pr.get("game_changer")),
                "collector_sort": collector_sort,
            }
        )

    if role_query_text:
        role_query_base = role_query_text.lower().strip()
        role_query_alt = re.sub(r"[_-]+", " ", role_query_base).strip()
        role_query_tokens = {role_query_base, role_query_alt}
        role_query_patterns = [f"%{token}%" for token in role_query_tokens if token]
        matching_roles = {
            oid
            for (oid,) in db.session.query(OracleCoreRoleTag.oracle_id)
            .filter(or_(*[func.lower(OracleCoreRoleTag.role).ilike(pattern) for pattern in role_query_patterns]))
            .distinct()
            .all()
            if oid
        }
        matching_evergreen = {
            oid
            for (oid,) in db.session.query(OracleEvergreenTag.oracle_id)
            .filter(or_(*[func.lower(OracleEvergreenTag.keyword).ilike(pattern) for pattern in role_query_patterns]))
            .distinct()
            .all()
            if oid
        }
        matching_oids = matching_roles | matching_evergreen
        if matching_oids:
            results = [r for r in results if r.get("oracle_id") in matching_oids]
            total_cards = len(results)
        else:
            results = []
            total_cards = 0

    collection_ids, _, _collection_lower = _collection_metadata()
    oids = [r.get("oracle_id") for r in results if r.get("oracle_id")]
    owned_by_oid = {}
    if oids:
        rows_query = (
            db.session.query(Card.oracle_id, func.coalesce(func.sum(Card.quantity), 0))
            .join(Folder, Card.folder_id == Folder.id)
            .filter(Card.oracle_id.in_(oids))
        )
        if collection_ids:
            rows_query = rows_query.filter(Card.folder_id.in_(collection_ids))
        rows = rows_query.group_by(Card.oracle_id).all()
        owned_by_oid = {oid: int(total or 0) for (oid, total) in rows}

    for rec in results:
        rec["owned_total"] = owned_by_oid.get(rec.get("oracle_id"), 0)

    sort_key_map = {
        "name": lambda r: ((r.get("name") or "").casefold(), r.get("collector_sort")),
        "cmc": lambda r: (
            float(r.get("cmc_value")) if r.get("cmc_value") is not None else float("inf"),
            (r.get("name") or "").casefold(),
        ),
        "rarity": lambda r: (r.get("rarity_rank", 99), (r.get("name") or "").casefold()),
        "set": lambda r: (r.get("set") or "", r.get("collector_sort")),
        "collector": lambda r: r.get("collector_sort"),
        "mana": lambda r: ((r.get("mana_cost") or ""), (r.get("name") or "").casefold()),
        "type": lambda r: ((r.get("type_line") or "").casefold(), (r.get("name") or "").casefold()),
        "price": lambda r: ((r.get("price_value") or 0.0), (r.get("name") or "").casefold()),
        "art": lambda r: (0 if r.get("thumb") else 1, (r.get("name") or "").casefold()),
    }
    sort_key = sort_key_map.get(sort_field)
    if sort_key:
        results.sort(key=sort_key, reverse=(sort_direction == "desc"))

    result_vms = [
        ScryfallCardVM(
            id=str(rec.get("id") or ""),
            name=rec.get("name") or "",
            thumb=rec.get("thumb"),
            image_large=rec.get("image_large"),
            prints_uri=rec.get("prints_uri"),
            set_code=(rec.get("set") or ""),
            set_name=rec.get("set_name"),
            collector_number=rec.get("collector_number"),
            lang=rec.get("lang"),
            owned_total=int(rec.get("owned_total") or 0),
            mana_cost_html=rec.get("mana_cost_html"),
            cmc_display=rec.get("cmc_display") or "-",
            type_badges=_type_badges(rec.get("type_line")),
            rarity_label=rec.get("rarity_label") or None,
            rarity_badge_class=_rarity_badge_class(rec.get("rarity_value")),
            price_lines=_price_lines(rec.get("prices")),
        )
        for rec in results
    ]

    pages = max(1, ceil((total_cards or 0) / per))

    def _with_page(n):
        params = request.args.to_dict(flat=False)
        params["page"] = [str(n)]
        params["per"] = [str(per)]
        return url_for("views.scryfall_browser", **{k: v if len(v) > 1 else v[0] for k, v in params.items()})

    page_urls = [(n, _with_page(n)) for n in range(1, pages + 1)]
    prev_url = _with_page(page - 1) if page > 1 else None
    next_url = _with_page(page + 1) if page < pages else None

    base_args = {}
    if name:
        base_args["q"] = name
    if set_code:
        base_args["set"] = set_code
    if typal:
        base_args["typal"] = typal
    if role_query_text:
        base_args["role_q"] = role_query_text
    if base_types:
        base_args["type"] = base_types
    if color_filters:
        base_args["color"] = color_filters
    if color_mode:
        base_args["color_mode"] = color_mode
    if unique_on:
        base_args["unique"] = "1"
    if commander_on:
        base_args["commander"] = "legal"
    if rarity_value:
        base_args["rarity"] = rarity_value
    if per:
        base_args["per"] = per

    def sort_url(field: str, direction: str) -> str:
        params = {}
        for key, value in base_args.items():
            if isinstance(value, list):
                params[key] = value[:]
            else:
                params[key] = value
        params["page"] = 1
        params["sort"] = field
        params["dir"] = direction
        return url_for("views.scryfall_browser", **params)

    return render_template(
        "cards/scryfall_browser.html",
        results=result_vms,
        total=total_cards,
        page=page,
        pages=pages,
        per=per,
        prev_url=prev_url,
        next_url=next_url,
        page_urls=page_urls,
        query=name,
        role_query_text=role_query_text,
        set_code=set_code,
        base_types=base_types,
        typal=typal,
        selected_colors=selected_colors,
        color_mode=color_mode,
        commander_on=commander_on,
        unique_on=unique_on,
        rarity_choices=RARITY_CHOICES,
        rarity_value=rarity_value,
        rarity_label=rarity_label,
        sort_field=sort_field,
        sort_direction=sort_direction,
        sort_url=sort_url,
        set_options=set_options,
    )


def sets_overview():
    q = (request.args.get("q") or "").strip().lower()
    sort = (request.args.get("sort") or "").strip().lower()
    direction = (request.args.get("dir") or "asc").strip().lower()
    reverse = direction == "desc"

    agg = (
        db.session.query(
            Card.set_code.label("set_code"),
            func.count(Card.id).label("row_count"),
            func.coalesce(func.sum(Card.quantity), 0).label("qty_sum"),
        )
        .filter(Card.set_code != None)  # noqa: E711
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

    codes = set(all_set_codes()) if have_cache else set()
    codes.update(owned_stats.keys())

    profile_map = set_profiles(codes)
    _ = profile_map
    name_map = {}
    release_map = {}
    if have_cache:
        name_map = {code: set_name_for_code(code) for code in codes}
        release_map = {code: set_release_for_code(code) for code in codes}

    items: list[SetSummaryVM] = []
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
        rec = SetSummaryVM(
            set_code=code,
            set_name=name or display_code,
            rows=int(stats.get("rows", 0) or 0),
            qty=int(stats.get("qty", 0) or 0),
            release_iso=release,
            release_display=release_display,
        )
        if q and (q not in code) and (q not in (rec.set_name or "").lower()):
            continue
        items.append(rec)

    if sort == "code":
        items.sort(key=lambda r: r.set_code, reverse=reverse)
    elif sort == "name":
        items.sort(key=lambda r: (r.set_name or "").lower(), reverse=reverse)
    elif sort == "rows":
        items.sort(key=lambda r: r.rows, reverse=reverse)
    elif sort == "qty":
        items.sort(key=lambda r: r.qty, reverse=reverse)
    elif sort == "release":
        def release_key(r):
            iso = r.release_iso
            if iso:
                return iso
            return "0000-00-00" if reverse else "9999-12-31"

        items.sort(key=release_key, reverse=reverse)
    else:
        items.sort(key=lambda r: (r.set_name or "").lower())
        items.sort(key=lambda r: r.release_iso or "0000-00-00", reverse=True)

    return render_template(
        "cards/sets.html",
        sets=items,
        q=q,
    )


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

    prints, total = search_prints(set_code=code, limit=0, offset=0)

    owned_rows = (
        db.session.query(Card.collector_number, func.coalesce(func.sum(Card.quantity), 0))
        .filter(Card.set_code.ilike(code))
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
            suffix = s[match.end():].lower()
            return (0, num, suffix)
        return (1, s.lower())

    def _print_sort_key(pr):
        return (
            _cn_sort_key(pr.get("collector_number")),
            (pr.get("lang") or "").upper(),
            pr.get("name") or "",
        )

    cards_raw = []
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
            )
            .with_entities(Card.id)
            .first()
        )
        local_card_id = local_card_id[0] if local_card_id else None

        rarity_raw = (pr.get("rarity") or "").strip().lower()
        rarity_label = rarity_raw.replace("_", " ").title() if rarity_raw else "-"
        detail_href = (
            url_for("views.card_detail", card_id=local_card_id)
            if local_card_id
            else url_for("views.scryfall_print_detail", sid=pr.get("id"))
        )
        collector_display = cn or "—"
        title = f"{pr.get('name') or ''} (#{collector_display})".strip()

        cards_raw.append(
            {
                "id": pr.get("id") or "",
                "name": pr.get("name") or "",
                "collector_number": cn,
                "collector_display": collector_display,
                "rarity": rarity_raw,
                "rarity_label": rarity_label,
                "image_src": normal or small,
                "detail_href": detail_href,
                "owned_qty": owned_qty,
                "title": title,
            }
        )

    first_release = min(release_dates) if release_dates else None

    rarity_options = sorted(
        {
            (card["rarity"] or "").strip().lower()
            for card in cards_raw
            if card.get("rarity")
        }
    )

    def _normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()

    query_tokens = [_token for _token in _normalize(name_query).split() if _token]
    filtered_raw = []
    for card in cards_raw:
        if query_tokens:
            normalized_name = _normalize(card.get("name"))
            if any(token not in normalized_name for token in query_tokens):
                continue
        if rarity_filter and (card.get("rarity") or "").lower() != rarity_filter:
            continue
        filtered_raw.append(card)

    filtered_cards = [
        SetGalleryCardVM(
            id=card.get("id") or "",
            name=card.get("name") or "",
            collector_number=card.get("collector_number") or "",
            collector_number_display=card.get("collector_display") or "—",
            image_src=card.get("image_src"),
            detail_href=card.get("detail_href") or "",
            rarity_label=card.get("rarity_label") or "-",
            owned_qty=int(card.get("owned_qty") or 0),
            title=card.get("title") or card.get("name") or "Card",
        )
        for card in filtered_raw
    ]

    return render_template(
        "cards/set_gallery.html",
        set_code=code,
        set_name=set_name,
        total_prints=total,
        owned_total=owned_total,
        release_date=first_release,
        cards=filtered_cards,
        filtered_count=len(filtered_cards),
        name_query=name_query,
        rarity_filter=rarity_filter,
        rarity_options=rarity_options,
        rarity_label=("All rarities" if not rarity_filter else rarity_filter.replace("_", " ").title()),
    )


def set_detail(set_code):
    params = dict(request.args.items())
    return redirect(url_for("views.set_gallery", set_code=set_code, **params))


def api_scryfall_print(sid):
    ensure_symbols_cache(force=False)
    data = None
    prints_lookup = None
    set_name_lookup = None

    try:
        if ensure_cache_loaded():
            from services.scryfall_cache import find_print_by_id, prints_for_oracle, set_name_for_code

            data = find_print_by_id(sid)
            prints_lookup = prints_for_oracle
            set_name_lookup = set_name_for_code
    except Exception:
        data = None

    if data is None:
        try:
            import requests

            resp = requests.get(f"https://api.scryfall.com/cards/{sid}", timeout=6)
            if resp.ok:
                data = resp.json()
        except Exception:
            data = None

    if data is None:
        abort(404)

    faces = data.get("card_faces") or []

    def _img(obj):
        if not obj:
            return None, None, None
        iu = obj.get("image_uris") or {}
        small = iu.get("small")
        normal = iu.get("normal") or iu.get("large")
        large = iu.get("large") or iu.get("normal")
        if not (small or normal or large):
            if faces:
                fiu = (faces[0] or {}).get("image_uris") or {}
                small = fiu.get("small")
                normal = fiu.get("normal") or fiu.get("large")
                large = fiu.get("large") or fiu.get("normal")
        return small, normal, large

    def _oracle_text(obj):
        if not obj:
            return None
        if obj.get("card_faces"):
            parts = [face.get("oracle_text") for face in obj["card_faces"] if face.get("oracle_text")]
            return " // ".join(parts) if parts else None
        return obj.get("oracle_text")

    raw_mana = data.get("mana_cost")
    if not raw_mana and faces:
        mana_parts = [face.get("mana_cost") for face in faces if face.get("mana_cost")]
        raw_mana = " // ".join(mana_parts) if mana_parts else None

    oracle_text = _oracle_text(data)

    prices = _prices_for_print(data)
    price_text = _format_price_text(prices)

    oid = data.get("oracle_id")

    images = []
    try:
        variants = prints_lookup(oid) if (prints_lookup and oid) else [data]
    except Exception:
        variants = [data]

    seen = set()
    for variant in variants or []:
        vid = variant.get("illustration_id") or variant.get("id")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        s, n, l = _img(variant)
        if not (s or n or l):
            continue
        label_bits = []
        if variant.get("set"):
            label_bits.append((variant.get("set") or "").upper())
        if variant.get("collector_number"):
            label_bits.append(str(variant.get("collector_number")))
        if variant.get("lang"):
            label_bits.append(str(variant.get("lang")).upper())
        images.append(
            {
                "id": variant.get("id"),
                "small": s,
                "normal": n,
                "large": l,
                "label": " Â· ".join(label_bits) if label_bits else (variant.get("name") or data.get("name") or ""),
            }
        )
        if len(images) >= 12:
            break

    info = {
        "id": data.get("id"),
        "name": data.get("name"),
        "mana_cost": raw_mana,
        "mana_cost_html": render_mana_html(raw_mana, use_local=True) if raw_mana else "â€”",
        "cmc": data.get("cmc"),
        "type_line": data.get("type_line"),
        "oracle_text": oracle_text,
        "oracle_text_html": render_oracle_html(oracle_text, use_local=True) if oracle_text else "â€”",
        "set": data.get("set"),
        "set_name": data.get("set_name") or (set_name_lookup(data.get("set")) if (set_name_lookup and data.get("set")) else None),
        "collector_number": data.get("collector_number"),
        "lang": (data.get("lang") or "").upper(),
        "rarity": data.get("rarity"),
        "rarity_label": (data.get("rarity") or "").title() if data.get("rarity") else None,
        "released_at": data.get("released_at"),
        "scryfall_uri": data.get("scryfall_uri"),
        "prints_uri": data.get("prints_search_uri"),
        "oracle_id": oid,
        "legalities": data.get("legalities") or {},
        "purchase_uris": data.get("purchase_uris") or {},
        "tcgplayer_url": (data.get("purchase_uris") or {}).get("tcgplayer") or (data.get("related_uris") or {}).get("tcgplayer"),
    }

    small, normal, large = _img(data)
    primary_image = {"small": small, "normal": normal, "large": large}

    return jsonify(
        {
            "info": info,
            "prices": prices,
            "price_text": price_text,
            "image": primary_image,
            "images": images or [primary_image],
        }
    )


def _faces_from_scry_json(data: dict | None) -> list[dict]:
    faces = []
    if not data:
        return faces
    if data.get("card_faces"):
        for face in data["card_faces"]:
            iu = (face or {}).get("image_uris") or {}
            faces.append({"large": iu.get("large"), "normal": iu.get("normal"), "small": iu.get("small")})
    else:
        iu = data.get("image_uris") or {}
        if iu:
            faces.append({"large": iu.get("large"), "normal": iu.get("normal"), "small": iu.get("small")})
    out = []
    seen = set()
    for face in faces:
        key = (face.get("large"), face.get("normal"), face.get("small"))
        if key in seen:
            continue
        seen.add(key)
        out.append(face)
    return out


def api_print_faces(sid):
    data = None
    try:
        if ensure_cache_loaded():
            from services.scryfall_cache import get_print_by_id

            data = get_print_by_id(sid)
    except Exception:
        data = None

    if data is None:
        try:
            import requests

            resp = requests.get(f"https://api.scryfall.com/cards/{sid}", timeout=6)
            if resp.ok:
                data = resp.json()
        except Exception:
            data = None

    return jsonify({"faces": _faces_from_scry_json(data)})


def scryfall_print_detail(sid):
    """Details for a specific Scryfall print id; reuses card_detail template."""
    if not ensure_cache_loaded():
        flash("Scryfall cache not loaded yet. Go to Admin â†’ refresh 'default_cards' first.", "warning")
        return redirect(url_for("views.scryfall_browser"))

    try:
        from services.scryfall_cache import find_print_by_id, prints_for_oracle, set_name_for_code
    except Exception:
        flash("Scryfall helpers not found. Add them to services/scryfall_cache.py.", "danger")
        return redirect(url_for("views.scryfall_browser"))

    pr = request_cached(("card_view", "print_by_id", sid), lambda: find_print_by_id(sid))
    if not pr:
        abort(404)

    def _img(obj):
        iu = obj.get("image_uris")
        if iu:
            return {"small": iu.get("small"), "normal": iu.get("normal"), "large": iu.get("large")}
        faces = obj.get("card_faces") or []
        if faces and isinstance(faces, list):
            iu2 = (faces[0] or {}).get("image_uris") or {}
            return {"small": iu2.get("small"), "normal": iu2.get("normal"), "large": iu2.get("large")}
        return {"small": None, "normal": None, "large": None}

    def _oracle_text(obj):
        faces = obj.get("card_faces") or []
        if faces:
            parts = [face.get("oracle_text") for face in faces if face.get("oracle_text")]
            return " // ".join(parts) if parts else None
        return obj.get("oracle_text")

    oid = pr.get("oracle_id")
    if prints_for_oracle and oid:
        variants = request_cached(("card_view", "prints", oid), lambda: prints_for_oracle(oid) or [])
    else:
        variants = [pr]
    selected_illus = pr.get("illustration_id") or pr.get("id")
    _, collection_folder_names, _ = _collection_metadata()

    seen, ordered = set(), []
    for variant in variants:
        vid = variant.get("illustration_id") or variant.get("id")
        if vid == selected_illus and vid not in seen:
            seen.add(vid)
            ordered.append(variant)
            break
    for variant in variants:
        vid = variant.get("illustration_id") or variant.get("id")
        if vid not in seen:
            seen.add(vid)
            ordered.append(variant)

    images = []
    for variant in ordered:
        iu = _img(variant)
        if iu["small"] or iu["normal"] or iu["large"]:
            label_bits = []
            if variant.get("set"):
                label_bits.append((variant.get("set") or "").upper())
            if variant.get("collector_number"):
                label_bits.append(str(variant.get("collector_number")))
            if variant.get("lang"):
                label_bits.append(str(variant.get("lang")).upper())
            label = " Â· ".join(label_bits) if label_bits else (variant.get("name") or pr.get("name"))
            images.append({"small": iu["small"], "normal": iu["normal"], "large": iu["large"], "label": label})

    ensure_symbols_cache(force=False)
    raw_mana = pr.get("mana_cost")
    raw_text = _oracle_text(pr)
    info = {
        "name": pr.get("name"),
        "mana_cost_html": render_mana_html(raw_mana, use_local=False),
        "cmc": pr.get("cmc"),
        "type_line": pr.get("type_line"),
        "oracle_text": raw_text,
        "oracle_text_html": render_oracle_html(raw_text, use_local=False),
        "colors": pr.get("colors") or [],
        "color_identity": pr.get("color_identity") or [],
        "rarity": pr.get("rarity"),
        "set": pr.get("set"),
        "set_name": pr.get("set_name") or (set_name_for_code(pr.get("set")) if set_name_for_code else None),
        "collector_number": pr.get("collector_number"),
        "lang": pr.get("lang"),
        "scryfall_uri": pr.get("scryfall_uri"),
        "scryfall_set_uri": pr.get("scryfall_set_uri"),
        "oracle_id": oid,
        "prints_search_uri": pr.get("prints_search_uri")
        or (f"https://api.scryfall.com/cards/search?order=released&q=oracleid:{oid}&unique=prints" if oid else None),
    }

    all_leg = pr.get("legalities") or {}
    info["legalities"] = all_leg
    info["commander_legality"] = all_leg.get("commander")

    purchase_uris = pr.get("purchase_uris") or {}
    info["purchase_uris"] = purchase_uris
    info["tcgplayer_url"] = purchase_uris.get("tcgplayer") or (pr.get("related_uris") or {}).get("tcgplayer")

    rulings = request_cached(("card_view", "rulings", oid), lambda: rulings_for_oracle(oid) or []) if oid else []
    color_pips = colors_to_icons(info.get("color_identity") or info.get("colors"), use_local=True)

    card_stub = SimpleNamespace(
        id=None,
        name=info.get("name"),
        set_code=info.get("set"),
        collector_number=info.get("collector_number"),
        lang=info.get("lang"),
        is_foil=False,
        folder=None,
        quantity=None,
    )

    owned_folders = []
    try:
        if oid:
            def _owned_folders_query():
                rows = (
                    db.session.query(Folder.name, func.coalesce(func.sum(Card.quantity), 0).label("qty"))
                    .join(Card, Card.folder_id == Folder.id)
                    .filter(Card.oracle_id == oid)
                    .group_by(Folder.name)
                    .order_by(func.coalesce(func.sum(Card.quantity), 0).desc())
                    .all()
                )
                return [{"name": folder_name, "qty": int(qty or 0)} for folder_name, qty in rows]

            owned_folders = request_cached(("card_view", "owned_folders", oid), _owned_folders_query)
    except Exception:
        owned_folders = []

    prices = _prices_for_print(pr)
    price_text = _format_price_text(prices)

    return render_template(
        "cards/card_detail.html",
        card=card_stub,
        info={
            **info,
            "prices": prices,
            "price_text": price_text,
        },
        images=images,
        rulings=rulings,
        color_pips=color_pips,
        tokens_created=sc.tokens_from_print(pr) if hasattr(sc, "tokens_from_print") else [],
        scryfall_id=pr.get("id"),
        oracle_id=oid,
        main_img_url=images[0]["large"] if images else None,
        name=info.get("name"),
        collection_folders=collection_folder_names,
        owned_folders=owned_folders,
        return_to=request.args.get("return_to"),
        primary_role_label=None,
        role_labels=[],
        subrole_labels=[],
        evergreen_labels=[],
    )


def scryfall_resolve_by_name():
    """
    Resolve a card by exact name via Scryfall Named API, then
    redirect to the in-app scryfall_print_detail(sid=...).
    """
    name = (request.args.get("name") or "").strip()
    return_to = request.args.get("return_to")
    if not name:
        flash("No card name provided.", "warning")
        return redirect(url_for("views.scryfall_browser"))

    try:
        qs = urlencode({"exact": name})
        with urlopen(f"https://api.scryfall.com/cards/named?{qs}", timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        sid = data.get("id")
        if not sid:
            raise ValueError("Missing Scryfall id")
        return redirect(url_for("views.scryfall_print_detail", sid=sid, return_to=return_to))
    except Exception:
        current_app.logger.exception("scryfall_resolve_by_name failed for %r", name)
        flash(f"Could not resolve â€œ{name}â€ on Scryfall.", "warning")
        return redirect(url_for("views.scryfall_browser", q=name))
