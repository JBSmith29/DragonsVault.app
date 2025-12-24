"""Scryfall search and print detail routes."""

from __future__ import annotations

import json
import re
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
from services.scryfall_cache import ensure_cache_loaded, rulings_for_oracle, set_name_for_code, all_set_codes
from services.scryfall_search import build_query, search_cards
from services.symbols_cache import colors_to_icons, ensure_symbols_cache, render_mana_html, render_oracle_html

from .base import (
    API_PAGE_SIZE,
    _collection_metadata,
    _format_price_text,
    _prices_for_print,
    views,
)


@views.route("/scryfall")
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
    if sort_field not in {"name", "cmc", "rarity", "set", "collector"}:
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
        'common': 0,
        'uncommon': 1,
        'rare': 2,
        'mythic': 3,
        'mythic rare': 3,
        'special': 4,
        'bonus': 5,
    }

    def _collector_key(value):
        if not value:
            return (float('inf'), '')
        digits, suffix = [], []
        for ch in str(value):
            (digits if ch.isdigit() else suffix).append(ch)
        try:
            number = int(''.join(digits)) if digits else float('inf')
        except Exception:
            number = float('inf')
        return (number, ''.join(suffix))

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
        rarity_value = (pr.get("rarity") or "").lower()
        rarity_label = rarity_value.title() if rarity_value else None
        rarity_rank = rarity_order.get(rarity_value, 99)

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
                "rarity_value": rarity_value,
                "rarity_label": rarity_label,
                "rarity_rank": rarity_rank,
                "price_text": price_text,
                "prices": prices,
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

    collection_ids, _, collection_lower = _collection_metadata()
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
        elif collection_lower:
            rows_query = rows_query.filter(func.lower(Folder.name).in_(collection_lower))
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
    }
    sort_key = sort_key_map.get(sort_field)
    if sort_key:
        results.sort(key=sort_key, reverse=(sort_direction == "desc"))

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
        base_args['q'] = name
    if set_code:
        base_args['set'] = set_code
    if typal:
        base_args['typal'] = typal
    if role_query_text:
        base_args['role_q'] = role_query_text
    if base_types:
        base_args['type'] = base_types
    if color_filters:
        base_args['color'] = color_filters
    if color_mode:
        base_args['color_mode'] = color_mode
    if unique_on:
        base_args['unique'] = '1'
    if commander_on:
        base_args['commander'] = 'legal'
    if rarity_value:
        base_args['rarity'] = rarity_value
    if per:
        base_args['per'] = per

    def sort_url(field: str, direction: str) -> str:
        params = {}
        for key, value in base_args.items():
            if isinstance(value, list):
                params[key] = value[:]
            else:
                params[key] = value
        params['page'] = 1
        params['sort'] = field
        params['dir'] = direction
        return url_for('views.scryfall_browser', **params)

    return render_template(
        "cards/scryfall_browser.html",
        results=results,
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


@views.route("/api/scryfall/print/<sid>")
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


@views.route("/scryfall/print/<sid>")
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

    pr = find_print_by_id(sid)
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
    variants = prints_for_oracle(oid) or [] if (prints_for_oracle and oid) else [pr]
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

    rulings = rulings_for_oracle(oid) or [] if oid else []
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
            rows = (
                db.session.query(Folder.name, func.coalesce(func.sum(Card.quantity), 0).label("qty"))
                .join(Card, Card.folder_id == Folder.id)
                .filter(Card.oracle_id == oid)
                .group_by(Folder.name)
                .order_by(func.coalesce(func.sum(Card.quantity), 0).desc())
                .all()
            )
            for folder_name, qty in rows:
                owned_folders.append({"name": folder_name, "qty": int(qty or 0)})
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


@views.route("/scryfall/resolve-by-name")
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


__all__ = ["scryfall_browser", "api_scryfall_print", "scryfall_print_detail", "scryfall_resolve_by_name"]
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
