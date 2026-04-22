"""Scryfall print-detail and print-API services."""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlencode
from urllib.request import urlopen

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func

from extensions import db
from models import Card, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.pricing import (
    format_price_text as _format_price_text,
    prices_for_print as _prices_for_print,
)
from core.domains.cards.services.scryfall_cache import (
    ensure_cache_loaded,
    rulings_for_oracle,
)
from core.domains.cards.services.scryfall_shared_service import (
    _faces_from_scry_json,
    _request_cached_core_role_labels,
    _request_cached_evergreen_labels,
    _request_cached_primary_oracle_role_label,
)
from core.shared.utils.symbols_cache import colors_to_icons, ensure_symbols_cache, render_mana_html, render_oracle_html
from shared.cache.request_cache import request_cached
from shared.mtg import _collection_metadata


def api_scryfall_print(sid):
    ensure_symbols_cache(force=False)
    data = None
    prints_lookup = None
    set_name_lookup = None

    try:
        if ensure_cache_loaded():
            from core.domains.cards.services.scryfall_cache import find_print_by_id, prints_for_oracle, set_name_for_code

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
        variants = list(prints_lookup(oid) or []) if (prints_lookup and oid) else [data]
    except Exception:
        variants = [data]

    if variants and data:
        selected_id = data.get("id")
        if selected_id:
            variants = (
                [variant for variant in variants if variant.get("id") == selected_id]
                + [variant for variant in variants if variant.get("id") != selected_id]
            )

    seen = set()
    for variant in variants or []:
        pid = variant.get("id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        small, normal, large = _img(variant)
        if not (small or normal or large):
            continue
        set_code = (variant.get("set") or "").upper()
        collector_number = str(variant.get("collector_number") or "")
        lang = str(variant.get("lang") or "").upper()
        label_bits = [value for value in (set_code, collector_number, lang) if value]
        purchase = variant.get("purchase_uris") or {}
        related = variant.get("related_uris") or {}
        images.append(
            {
                "id": pid,
                "small": small,
                "normal": normal,
                "large": large,
                "label": " Â· ".join(label_bits) if label_bits else (variant.get("name") or data.get("name") or ""),
                "set": set_code,
                "set_name": variant.get("set_name")
                or (set_name_lookup(set_code.lower()) if (set_name_lookup and set_code) else None),
                "collector_number": collector_number,
                "lang": lang,
                "rarity": variant.get("rarity"),
                "released_at": variant.get("released_at"),
                "scryfall_uri": variant.get("scryfall_uri"),
                "tcgplayer_url": purchase.get("tcgplayer") or related.get("tcgplayer"),
                "prices": variant.get("prices") or {},
            }
        )

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


def api_print_faces(sid):
    data = None
    try:
        if ensure_cache_loaded():
            from core.domains.cards.services.scryfall_cache import get_print_by_id

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
        from core.domains.cards.services.scryfall_cache import find_print_by_id, prints_for_oracle, set_name_for_code
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
    mana_cost_html = render_mana_html(raw_mana, use_local=False)
    oracle_text_html = render_oracle_html(raw_text, use_local=False)
    info = {
        "name": pr.get("name"),
        "mana_cost_html": mana_cost_html,
        "cmc": pr.get("cmc"),
        "type_line": pr.get("type_line"),
        "oracle_text": raw_text,
        "oracle_text_html": oracle_text_html,
        "colors": pr.get("colors") or [],
        "color_identity": pr.get("color_identity") or [],
        "rarity": pr.get("rarity"),
        "set_code": pr.get("set"),
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
    commander_legality = all_leg.get("commander")

    purchase_uris = pr.get("purchase_uris") or {}
    tcgplayer_url = purchase_uris.get("tcgplayer") or (pr.get("related_uris") or {}).get("tcgplayer")
    prices = _prices_for_print(pr)
    price_text = _format_price_text(prices)

    commander_label = None
    commander_class = None
    if commander_legality:
        leg_norm = str(commander_legality)
        commander_label = "Not legal" if leg_norm == "not_legal" else leg_norm.replace("_", " ").capitalize()
        if leg_norm == "legal":
            commander_class = "bg-success"
        elif leg_norm == "banned":
            commander_class = "bg-danger"
        elif leg_norm == "restricted":
            commander_class = "bg-warning text-dark"
        else:
            commander_class = "bg-secondary"

    prices_json = json.dumps(prices or {}, ensure_ascii=True)
    has_oracle_text = bool(raw_text)
    has_mana_cost = bool(mana_cost_html)

    info["legalities"] = all_leg
    info["commander_legality"] = commander_legality
    info["commander_legality_label"] = commander_label
    info["commander_legality_class"] = commander_class
    info["has_commander_legality"] = bool(commander_label)
    info["purchase_uris"] = purchase_uris
    info["tcgplayer_url"] = tcgplayer_url
    info["prices"] = prices
    info["price_text"] = price_text
    info["prices_json"] = prices_json
    info["has_prices"] = bool(prices)
    info["has_oracle_text"] = has_oracle_text
    info["has_mana_cost"] = has_mana_cost
    info["has_scryfall_uri"] = bool(info.get("scryfall_uri"))
    info["has_scryfall_set_uri"] = bool(info.get("scryfall_set_uri"))

    rulings = request_cached(("card_view", "rulings", oid), lambda: rulings_for_oracle(oid) or []) if oid else []
    color_pips = colors_to_icons(info.get("color_identity") or info.get("colors"), use_local=True)

    card_stub = SimpleNamespace(
        id=None,
        name=info.get("name"),
        set_code=info.get("set_code"),
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

    primary_role_label = _request_cached_primary_oracle_role_label(oid) if oid else None
    role_labels = _request_cached_core_role_labels(oid) if oid else []
    evergreen_labels = _request_cached_evergreen_labels(oid) if oid else []
    subrole_labels: list[str] = []

    print_images = []
    if variants:
        ordered_prints = []
        if pr:
            ordered_prints.append(pr)
        ordered_prints.extend([variant for variant in variants if variant is not pr])
        seen_prints: set[str] = set()
        for variant in ordered_prints:
            pid = variant.get("id") or ""
            if pid and pid in seen_prints:
                continue
            if pid:
                seen_prints.add(pid)
            img_pack = _img(variant)
            if not (img_pack.get("small") or img_pack.get("normal") or img_pack.get("large")):
                continue
            set_code = (variant.get("set") or "").upper()
            collector_number = str(variant.get("collector_number") or "")
            lang_code = str(variant.get("lang") or "").upper()
            label_bits = [val for val in (set_code, collector_number, lang_code) if val]
            label = " · ".join(label_bits) if label_bits else (variant.get("name") or pr.get("name"))
            prices = variant.get("prices") or {}
            purchase = variant.get("purchase_uris") or {}
            related = variant.get("related_uris") or {}
            set_name = variant.get("set_name") or (set_name_for_code(variant.get("set")) if variant.get("set") else "")
            print_images.append(
                {
                    "id": pid,
                    "set": set_code,
                    "setName": set_name or "",
                    "collectorNumber": collector_number,
                    "lang": lang_code,
                    "rarity": variant.get("rarity") or "",
                    "prices": prices or {},
                    "name": variant.get("name") or pr.get("name"),
                    "scryUri": variant.get("scryfall_uri") or "",
                    "tcgUri": purchase.get("tcgplayer") or related.get("tcgplayer") or "",
                    "releasedAt": variant.get("released_at") or "",
                    "small": img_pack.get("small") or img_pack.get("normal") or img_pack.get("large"),
                    "normal": img_pack.get("normal") or img_pack.get("large") or img_pack.get("small"),
                    "large": img_pack.get("large") or img_pack.get("normal") or img_pack.get("small"),
                    "label": label,
                }
            )
    print_images_json = json.dumps(print_images, ensure_ascii=True)

    return render_template(
        "cards/card_detail.html",
        card=card_stub,
        info=info,
        images=images,
        print_images_json=print_images_json,
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
        primary_role_label=primary_role_label,
        role_labels=role_labels,
        subrole_labels=subrole_labels,
        evergreen_labels=evergreen_labels,
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


__all__ = [
    "api_print_faces",
    "api_scryfall_print",
    "scryfall_print_detail",
    "scryfall_resolve_by_name",
]
