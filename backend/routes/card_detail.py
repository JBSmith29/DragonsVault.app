"""Owned card detail views."""

from __future__ import annotations

from flask import redirect, render_template, url_for

from extensions import db
from models import Card
from models.role import Role, CardRole, OracleEvergreenTag
from services import scryfall_cache as sc
from services.scryfall_cache import (
    ensure_cache_loaded,
    find_by_set_cn,
    prints_for_oracle,
    rulings_for_oracle,
    set_name_for_code,
    fetch_live_print,
)
from services.symbols_cache import colors_to_icons, ensure_symbols_cache, render_mana_html, render_oracle_html
from utils.db import get_or_404

from .base import (
    _collection_metadata,
    _format_price_text,
    _prices_for_print,
    _unique_art_variants,
    views,
)


@views.route("/cards/<int:card_id>")
def card_detail(card_id):
    """
    Owned card detail:
      - unique arts (owned art first)
      - rulings, tokens, pips
      - provides scryfall_id / oracle_id / prints_search_uri / main_img_url
    """
    ensure_symbols_cache(force=False)
    _, collection_folder_names, _ = _collection_metadata()

    card = get_or_404(Card, card_id)
    have_cache = ensure_cache_loaded()
    oid = card.oracle_id

    if have_cache and not oid:
        found = find_by_set_cn(card.set_code, card.collector_number, card.name)
        if found and found.get("oracle_id"):
            oid = found["oracle_id"]
            card.oracle_id = oid
            db.session.commit()

    prints = []
    if have_cache and oid:
        prints = prints_for_oracle(oid) or []
    elif have_cache:
        fetched = find_by_set_cn(card.set_code, card.collector_number, card.name)
        if fetched:
            prints = [fetched]
            oid = oid or fetched.get("oracle_id")

    if not prints:
        live = sc.fetch_live_print(card.set_code, card.collector_number, card.name)
        if live:
            prints = [live]
            oid = oid or live.get("oracle_id") or oid
            if live.get("oracle_id") and not card.oracle_id:
                card.oracle_id = live.get("oracle_id")
                db.session.commit()

    owned_set = (card.set_code or "").lower()
    owned_cn = str(card.collector_number) if card.collector_number is not None else ""
    owned_lang = (card.lang or "").lower()

    best = None
    for pr in prints:
        if (
            (pr.get("set") or "").lower() == owned_set
            and str(pr.get("collector_number") or "") == owned_cn
            and ((pr.get("lang") or "").lower() == owned_lang or not owned_lang)
        ):
            best = pr
            break
    if not best:
        for pr in prints:
            if (pr.get("set") or "").lower() == owned_set and str(pr.get("collector_number") or "") == owned_cn:
                best = pr
                break
    if not best and prints:
        best = prints[0]

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

    unique = _unique_art_variants(prints)
    owned_art_id = (best or {}).get("illustration_id") or (best or {}).get("id")
    ordered = []
    if best:
        for pr in unique:
            pid = pr.get("illustration_id") or pr.get("id")
            if pid == owned_art_id:
                ordered.append(pr)
                break
        for pr in unique:
            pid = pr.get("illustration_id") or pr.get("id")
            if pid != owned_art_id:
                ordered.append(pr)
    else:
        ordered = unique

    images = []
    for pr in ordered:
        iu = _img(pr)
        if iu["small"] or iu["normal"] or iu["large"]:
            bits = []
            if pr.get("set"):
                bits.append(pr.get("set").upper())
            if pr.get("collector_number"):
                bits.append(str(pr.get("collector_number")))
            if pr.get("lang"):
                bits.append(str(pr.get("lang")).upper())
            label = " · ".join(bits) if bits else (pr.get("name") or card.name)
            images.append({"small": iu["small"], "normal": iu["normal"], "large": iu["large"], "label": label})

    if best:
        all_leg = best.get("legalities") or {}
        raw_name = best.get("name") or card.name
        raw_mana = best.get("mana_cost")
        raw_text = _oracle_text(best)
        colors_ci = best.get("color_identity") or best.get("colors") or []
        info = {
            "name": raw_name,
            "mana_cost": raw_mana,
            "mana_cost_html": render_mana_html(raw_mana, use_local=False),
            "cmc": best.get("cmc"),
            "type_line": best.get("type_line"),
            "oracle_text": raw_text,
            "oracle_text_html": render_oracle_html(raw_text, use_local=False),
            "colors": best.get("colors") or [],
            "color_identity": colors_ci,
            "keywords": best.get("keywords") or [],
            "rarity": best.get("rarity"),
            "set": best.get("set") or card.set_code,
            "set_name": best.get("set_name") or (set_name_for_code(card.set_code) if have_cache else None),
            "collector_number": best.get("collector_number") or card.collector_number,
            "scryfall_uri": best.get("scryfall_uri"),
            "scryfall_set_uri": best.get("scryfall_set_uri"),
            "legalities": {"commander": all_leg.get("commander")},
            "commander_legality": all_leg.get("commander"),
        }
        purchase_uris = best.get("purchase_uris") or {}
        related_uris = best.get("related_uris") or {}
        prices = _prices_for_print(best)
        info["purchase_uris"] = purchase_uris
        info["related_uris"] = related_uris
        info["prices"] = prices
        info["price_text"] = _format_price_text(prices)
        info["tcgplayer_url"] = purchase_uris.get("tcgplayer") or related_uris.get("tcgplayer")
    else:
        raw_mana = None
        raw_text = None
        info = {
            "name": card.name,
            "mana_cost": raw_mana,
            "mana_cost_html": render_mana_html(raw_mana, use_local=False),
            "cmc": None,
            "type_line": None,
            "oracle_text": raw_text,
            "oracle_text_html": render_oracle_html(raw_text, use_local=False),
            "colors": [],
            "color_identity": [],
            "keywords": [],
            "rarity": None,
            "set": card.set_code,
            "set_name": set_name_for_code(card.set_code) if have_cache else None,
            "collector_number": card.collector_number,
            "scryfall_uri": None,
            "scryfall_set_uri": None,
            "legalities": {"commander": None},
            "commander_legality": None,
        }
        info["purchase_uris"] = {}
        info["related_uris"] = {}
        info["prices"] = {}
        info["price_text"] = None
        info["tcgplayer_url"] = None

    if oid:
        info["oracle_id"] = oid
        if not info.get("prints_search_uri"):
            info["prints_search_uri"] = (
                f"https://api.scryfall.com/cards/search?order=released&q=oracleid:{oid}&unique=prints"
            )

    tokens_created = sc.tokens_from_print(best) if (have_cache and best) else []
    pip_srcs = colors_to_icons(info.get("color_identity") or info.get("colors"), use_local=False)
    rulings = rulings_for_oracle(oid) or [] if oid else []

    selected = ordered[0] if ordered else {}

    def _pick_img(obj):
        iu = (obj or {}).get("image_uris") or {}
        faces = (obj or {}).get("card_faces") or []
        if not iu and faces and isinstance(faces, list):
            iu = (faces[0] or {}).get("image_uris") or {}
        return iu.get("large") or iu.get("normal") or iu.get("small")

    scryfall_id = selected.get("id")
    main_img_url = _pick_img(selected) or (
        (images[0].get("large") or images[0].get("normal") or images[0].get("small")) if images else None
    )
    oracle_id = oid
    display_name = info.get("name") or card.name
    role_labels = [(r.label or getattr(r, "name", None) or r.key) for r in (card.roles or [])]
    subrole_labels = [(s.label or getattr(s, "name", None) or s.key) for s in (card.subroles or [])]
    primary_role = (
        db.session.query(Role)
        .join(CardRole, CardRole.role_id == Role.id)
        .filter(CardRole.card_id == card.id, CardRole.primary.is_(True))
        .first()
    )
    primary_role_label = primary_role.label or getattr(primary_role, "name", None) or primary_role.key if primary_role else None
    evergreen_labels: list[str] = []
    if oid:
        evergreen_labels = [
            row[0]
            for row in (
                db.session.query(OracleEvergreenTag.keyword)
                .filter(OracleEvergreenTag.oracle_id == oid)
                .order_by(OracleEvergreenTag.keyword.asc())
                .all()
            )
            if row and row[0]
        ]

    return render_template(
        "cards/card_detail.html",
        card=card,
        info=info,
        images=images,
        rulings=rulings,
        color_pips=pip_srcs,
        tokens_created=tokens_created,
        scryfall_id=scryfall_id,
        oracle_id=oracle_id,
        main_img_url=main_img_url,
        name=display_name,
        collection_folders=collection_folder_names,
        primary_role_label=primary_role_label,
        role_labels=role_labels,
        subrole_labels=subrole_labels,
        evergreen_labels=evergreen_labels,
    )


@views.route("/cards/<id_or_sid>")
def smart_card_detail(id_or_sid):
    """Smart redirect: integer -> owned card, else -> scryfall print detail."""
    try:
        cid = int(id_or_sid)
        return redirect(url_for("views.card_detail", card_id=cid))
    except ValueError:
        return redirect(url_for("views.scryfall_print_detail", sid=id_or_sid))


__all__ = ["card_detail", "smart_card_detail"]
