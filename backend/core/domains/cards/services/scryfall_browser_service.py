"""Scryfall browser service."""

from __future__ import annotations

import re
from math import ceil

from flask import render_template, request, url_for
from sqlalchemy import func, or_

from extensions import db
from models import Card, Folder
from models.role import OracleCoreRoleTag, OracleEvergreenTag
from core.domains.cards.services.pricing import (
    format_price_text as _format_price_text,
    prices_for_print as _prices_for_print,
)
from core.domains.cards.services.scryfall_cache import (
    all_set_codes,
    ensure_cache_loaded,
    search_prints,
    set_name_for_code,
)
from core.domains.cards.services.scryfall_search import build_query, search_cards
from core.domains.cards.services.scryfall_shared_service import (
    RARITY_CHOICES,
    _price_lines,
    _rarity_badge_class,
    _type_badges,
)
from core.shared.utils.symbols_cache import ensure_symbols_cache, render_mana_html
from shared.mtg import API_PAGE_SIZE, _collection_metadata
from core.domains.cards.viewmodels.card_vm import ScryfallCardVM


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

    base_types = [value for value in args.getlist("type") if value]
    typal = (args.get("typal") or "").strip()
    role_query_text = (args.get("role_q") or "").strip()

    color_filters = [value for value in args.getlist("color") if value]
    selected_colors = [value.upper() for value in color_filters]
    color_mode = (args.get("color_mode") or "contains").lower()
    if color_mode not in {"contains", "exact"}:
        color_mode = "contains"

    sort_field = (args.get("sort") or "name").lower()
    if sort_field not in {"name", "cmc", "rarity", "set", "collector", "mana", "type", "price", "art"}:
        sort_field = "name"
    sort_direction = (args.get("dir") or "asc").lower()
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "asc"

    unique_on = ("unique" in args) or (not has_query)
    commander_on = ("commander" in args) or (not has_query)
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
        colors=[value for value in selected_colors if value in {"W", "U", "B", "R", "G"}],
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
            results = [rec for rec in results if rec.get("oracle_id") in matching_oids]
            total_cards = len(results)
        else:
            results = []
            total_cards = 0

    collection_ids, _, _collection_lower = _collection_metadata()
    oids = [rec.get("oracle_id") for rec in results if rec.get("oracle_id")]
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
        "name": lambda rec: (rec.get("name") or "").casefold(),
        "cmc": lambda rec: float(rec.get("cmc_value")) if rec.get("cmc_value") is not None else float("inf"),
        "rarity": lambda rec: rec.get("rarity_rank", 99),
        "set": lambda rec: rec.get("set") or "",
        "collector": lambda rec: rec.get("collector_sort"),
        "mana": lambda rec: rec.get("mana_cost") or "",
        "type": lambda rec: (rec.get("type_line") or "").casefold(),
        "price": lambda rec: rec.get("price_value") or 0.0,
        "art": lambda rec: 0 if rec.get("thumb") else 1,
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
            set_code=rec.get("set") or "",
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

    def _with_page(page_number):
        params = request.args.to_dict(flat=False)
        params["page"] = [str(page_number)]
        params["per"] = [str(per)]
        return url_for("views.scryfall_browser", **{key: value if len(value) > 1 else value[0] for key, value in params.items()})

    page_urls = [(page_number, _with_page(page_number)) for page_number in range(1, pages + 1)]
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


__all__ = ["scryfall_browser"]
