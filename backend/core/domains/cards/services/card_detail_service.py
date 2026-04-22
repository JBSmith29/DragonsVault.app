"""Owned-card API and detail views."""

from __future__ import annotations

import json

from flask import jsonify, redirect, render_template, url_for
from sqlalchemy.orm import selectinload

from extensions import db
from models import Card
from models.role import CardRole, OracleCoreRoleTag, OracleEvergreenTag, OracleRole, Role
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.pricing import (
    format_price_text as _format_price_text,
    prices_for_print as _prices_for_print,
)
from core.domains.cards.services.scryfall_cache import ensure_cache_loaded, find_by_set_cn, prints_for_oracle, rulings_for_oracle, set_name_for_code
from core.domains.cards.viewmodels.card_vm import CardInfoVM, CardListItemVM, CardTokenVM, FolderRefVM, ImageSetVM, format_role_label
from core.shared.utils.symbols_cache import colors_to_icons, ensure_symbols_cache, render_mana_html, render_oracle_html
from shared.auth import ensure_folder_access
from shared.cache.request_cache import request_cached
from shared.database import get_or_404
from shared.mtg import (
    _collection_metadata,
    _color_letters_list,
    _effective_color_identity,
    _faces_image_payload,
    _lookup_print_data,
    _mana_cost_from_faces,
    _oracle_text_from_faces,
    _scryfall_card_url,
    _scryfall_set_url,
    _token_stubs_from_oracle_text,
    _type_line_from_print,
    _unique_art_variants,
)


def _request_cached_find_by_set_cn(set_code: str | None, collector_number, name: str | None):
    key = (
        "card_detail",
        "setcn",
        (set_code or "").lower(),
        str(collector_number or ""),
        (name or "").lower(),
    )
    return request_cached(key, lambda: find_by_set_cn(set_code, collector_number, name))


def _request_cached_prints_for_oracle(oracle_id: str | None) -> list[dict]:
    if not oracle_id:
        return []
    return request_cached(("card_detail", "prints", oracle_id), lambda: prints_for_oracle(oracle_id) or [])


def _request_cached_rulings(oracle_id: str | None) -> list[dict]:
    if not oracle_id:
        return []
    return request_cached(("card_detail", "rulings", oracle_id), lambda: rulings_for_oracle(oracle_id) or [])


def _request_cached_primary_role_label(card_id: int | None) -> str | None:
    if not card_id:
        return None

    def _load() -> str | None:
        primary_role = (
            db.session.query(Role)
            .join(CardRole, CardRole.role_id == Role.id)
            .filter(CardRole.card_id == card_id, CardRole.primary.is_(True))
            .first()
        )
        if not primary_role:
            return None
        return primary_role.label or getattr(primary_role, "name", None) or primary_role.key

    return request_cached(("card_detail", "primary_role", int(card_id)), _load)


def _request_cached_evergreen_labels(oracle_id: str | None) -> list[str]:
    if not oracle_id:
        return []

    def _load() -> list[str]:
        return [
            row[0]
            for row in (
                db.session.query(OracleEvergreenTag.keyword)
                .filter(OracleEvergreenTag.oracle_id == oracle_id)
                .order_by(OracleEvergreenTag.keyword.asc())
                .all()
            )
            if row and row[0]
        ]

    return request_cached(("card_detail", "evergreen", oracle_id), _load)


def _request_cached_core_role_labels(oracle_id: str | None) -> list[str]:
    if not oracle_id:
        return []

    def _load() -> list[str]:
        rows = (
            db.session.query(OracleCoreRoleTag.role)
            .filter(OracleCoreRoleTag.oracle_id == oracle_id)
            .order_by(OracleCoreRoleTag.role.asc())
            .all()
        )
        labels: list[str] = []
        for row in rows:
            role = row[0] if row else None
            if not role:
                continue
            label = format_role_label(str(role))
            if label not in labels:
                labels.append(label)
        return labels

    return request_cached(("card_detail", "core_roles", oracle_id), _load)


def _request_cached_primary_oracle_role_label(oracle_id: str | None) -> str | None:
    if not oracle_id:
        return None

    def _load() -> str | None:
        row = db.session.query(OracleRole.primary_role).filter(OracleRole.oracle_id == oracle_id).first()
        if not row or not row[0]:
            return None
        return format_role_label(str(row[0]))

    return request_cached(("card_detail", "primary_oracle_role", oracle_id), _load)


def _image_from_print(print_obj: dict | None) -> dict:
    if not print_obj:
        return {"small": None, "normal": None, "large": None}
    imgs = sc.image_for_print(print_obj) or {}
    faces = print_obj.get("card_faces") or []
    if not imgs.get("small") and faces:
        face_imgs = (faces[0] or {}).get("image_uris") or {}
        imgs.setdefault("small", face_imgs.get("small"))
        imgs.setdefault("normal", face_imgs.get("normal"))
        imgs.setdefault("large", face_imgs.get("large") or face_imgs.get("png"))
    return {
        "small": imgs.get("small"),
        "normal": imgs.get("normal"),
        "large": imgs.get("large") or imgs.get("png"),
    }


def _role_names(card: Card) -> tuple[list[str], list[str], str | None]:
    role_names = [
        (role.label or getattr(role, "name", None) or role.key)
        for role in (card.roles or [])
        if (role.label or getattr(role, "name", None) or role.key)
    ]
    subrole_names = [
        (subrole.label or getattr(subrole, "name", None) or subrole.key)
        for subrole in (card.subroles or [])
        if (subrole.label or getattr(subrole, "name", None) or subrole.key)
    ]
    return role_names, subrole_names, _request_cached_primary_role_label(card.id)


def _best_print_for_owned_card(card: Card, prints: list[dict], fallback: dict | None = None) -> dict | None:
    if not prints:
        return fallback
    owned_set = (card.set_code or "").lower()
    owned_cn = str(card.collector_number) if card.collector_number is not None else ""
    owned_lang = (card.lang or "").lower()

    for pr in prints:
        if (
            (pr.get("set") or "").lower() == owned_set
            and str(pr.get("collector_number") or "") == owned_cn
            and ((pr.get("lang") or "").lower() == owned_lang or not owned_lang)
        ):
            return pr
    for pr in prints:
        if (pr.get("set") or "").lower() == owned_set and str(pr.get("collector_number") or "") == owned_cn:
            return pr
    return prints[0]


def _load_print_context(card: Card, *, allow_live_fallback: bool) -> tuple[bool, str | None, list[dict], dict | None]:
    have_cache = ensure_cache_loaded()
    oracle_id = card.oracle_id
    lookup_print = _lookup_print_data(card.set_code, card.collector_number, card.name, card.oracle_id) if have_cache else None

    if have_cache and not oracle_id:
        found = _request_cached_find_by_set_cn(card.set_code, card.collector_number, card.name)
        if found and found.get("oracle_id"):
            oracle_id = found["oracle_id"]
            card.set_oracle_id(oracle_id)
            db.session.commit()
            if not lookup_print:
                lookup_print = found

    prints: list[dict] = []
    if have_cache and oracle_id:
        prints = _request_cached_prints_for_oracle(oracle_id)
    elif have_cache and lookup_print:
        prints = [lookup_print]
        oracle_id = oracle_id or lookup_print.get("oracle_id")
    elif have_cache:
        fetched = _request_cached_find_by_set_cn(card.set_code, card.collector_number, card.name)
        if fetched:
            prints = [fetched]
            oracle_id = oracle_id or fetched.get("oracle_id")
            lookup_print = lookup_print or fetched

    if allow_live_fallback and not prints:
        try:
            live = sc.fetch_live_print(card.set_code, card.collector_number, card.name)
        except Exception:
            live = None
        if live:
            prints = [live]
            lookup_print = lookup_print or live
            oracle_id = oracle_id or live.get("oracle_id") or oracle_id
            if live.get("oracle_id") and not card.oracle_id:
                card.set_oracle_id(live.get("oracle_id"))
                db.session.commit()

    best = _best_print_for_owned_card(card, prints, lookup_print)
    return have_cache, oracle_id, prints, best


def _build_card_info(card: Card, best: dict | None, *, have_cache: bool) -> dict:
    best_faces = (best or {}).get("card_faces") if isinstance(best, dict) else None
    best_oracle_text = (best or {}).get("oracle_text") or _oracle_text_from_faces(best_faces)
    best_mana_cost = (best or {}).get("mana_cost") or _mana_cost_from_faces(best_faces)
    best_type_line = _type_line_from_print(best)
    best_colors = _color_letters_list((best or {}).get("colors"))
    best_color_identity = _color_letters_list((best or {}).get("color_identity")) or best_colors

    type_line = (getattr(card, "type_line", None) or "").strip() or best_type_line or None
    oracle_text = getattr(card, "oracle_text", None) or _oracle_text_from_faces(getattr(card, "faces_json", None)) or best_oracle_text
    mana_cost = _mana_cost_from_faces(getattr(card, "faces_json", None)) or best_mana_cost
    cmc = getattr(card, "mana_value", None)
    if cmc is None and isinstance(best, dict):
        cmc = best.get("cmc")
    colors = _color_letters_list(getattr(card, "colors", None)) or best_colors
    color_identity = _color_letters_list(getattr(card, "color_identity", None)) or best_color_identity or colors
    color_identity = _effective_color_identity(type_line, oracle_text, color_identity)
    if not colors:
        colors = color_identity
    rarity = (getattr(card, "rarity", None) or "").strip().lower() or ((best or {}).get("rarity") or None)

    legalities = (best or {}).get("legalities") or {}
    return {
        "name": card.name,
        "mana_cost": mana_cost,
        "mana_cost_html": render_mana_html(mana_cost, use_local=False),
        "cmc": cmc,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "oracle_text_html": render_oracle_html(oracle_text, use_local=False),
        "colors": colors or [],
        "color_identity": color_identity or [],
        "keywords": [],
        "rarity": rarity,
        "set": card.set_code,
        "set_name": set_name_for_code(card.set_code) if have_cache else None,
        "collector_number": card.collector_number,
        "scryfall_uri": (best or {}).get("scryfall_uri") or _scryfall_card_url(card.set_code, card.collector_number),
        "scryfall_set_uri": (best or {}).get("scryfall_set_uri") or _scryfall_set_url(card.set_code),
        "legalities": legalities,
        "commander_legality": legalities.get("commander"),
        "faces": _faces_image_payload(getattr(card, "faces_json", None)),
        "scryfall_id": (best or {}).get("id"),
    }


def api_card(card_id):
    ensure_symbols_cache(force=False)
    card = get_or_404(
        Card,
        card_id,
        options=(
            selectinload(Card.folder),
            selectinload(Card.roles),
            selectinload(Card.subroles),
        ),
    )
    ensure_folder_access(card.folder, write=False, allow_shared=True)

    have_cache, _oracle_id, prints, best = _load_print_context(card, allow_live_fallback=False)
    role_names = []
    subrole_names = []
    primary_role = None
    try:
        role_names, subrole_names, primary_role = _role_names(card)
    except Exception:
        role_names = role_names or []
        subrole_names = subrole_names or []
        primary_role = None

    info = _build_card_info(card, best, have_cache=have_cache)
    info["faces"] = _faces_image_payload(getattr(card, "faces_json", None))

    image_pack = _image_from_print(best)
    images = [{"small": image_pack["small"], "normal": image_pack["normal"], "large": image_pack["large"]}]

    printings: list[dict] = []
    if have_cache:
        ordered_prints = []
        if best:
            ordered_prints.append(best)
        ordered_prints.extend([pr for pr in prints if pr is not best])
        seen_prints: set[str] = set()
        for pr in ordered_prints:
            pid = pr.get("id") or ""
            if pid and pid in seen_prints:
                continue
            if pid:
                seen_prints.add(pid)
            img_pack = _image_from_print(pr)
            if not (img_pack.get("small") or img_pack.get("normal") or img_pack.get("large")):
                continue
            set_code = (pr.get("set") or "").upper()
            collector_number = str(pr.get("collector_number") or "")
            lang_code = str(pr.get("lang") or "").upper()
            label_bits = [value for value in (set_code, collector_number, lang_code) if value]
            label = " · ".join(label_bits) if label_bits else (pr.get("name") or card.name)
            purchase = pr.get("purchase_uris") or {}
            related = pr.get("related_uris") or {}
            printings.append(
                {
                    "id": pid,
                    "label": label,
                    "set": set_code,
                    "set_name": pr.get("set_name") or (set_name_for_code((pr.get("set") or "").lower()) if pr.get("set") else ""),
                    "collector_number": collector_number,
                    "lang": lang_code,
                    "rarity": pr.get("rarity") or "",
                    "released_at": pr.get("released_at") or "",
                    "scryfall_uri": pr.get("scryfall_uri") or _scryfall_card_url(pr.get("set"), pr.get("collector_number")),
                    "tcgplayer_url": purchase.get("tcgplayer") or related.get("tcgplayer") or "",
                    "prices": pr.get("prices") or {},
                    "image": img_pack.get("normal") or img_pack.get("large") or img_pack.get("small"),
                }
            )

    resp = jsonify(
        {
            "card": {
                "id": card.id,
                "quantity": card.quantity,
                "folder": card.folder.name if card.folder else None,
                "roles": role_names,
                "subroles": subrole_names,
                "primary_role": primary_role,
            },
            "info": info,
            "images": images,
            "printings": printings,
        }
    )
    resp.cache_control.private = True
    resp.cache_control.max_age = 60
    return resp


def card_detail(card_id):
    """Owned card detail with arts, rulings, token stubs, and print pricing."""
    ensure_symbols_cache(force=False)
    _, collection_folder_names, _ = _collection_metadata()

    card = get_or_404(Card, card_id)
    ensure_folder_access(card.folder, write=False, allow_shared=True)

    have_cache, oracle_id, prints, best = _load_print_context(card, allow_live_fallback=True)
    info = _build_card_info(card, best, have_cache=have_cache)

    purchase_uris: dict = {}
    related_uris: dict = {}
    prices: dict = {}
    if best:
        purchase_uris = best.get("purchase_uris") or {}
        related_uris = best.get("related_uris") or {}
        prices = _prices_for_print(best)
        info["purchase_uris"] = purchase_uris
        info["related_uris"] = related_uris
        info["prices"] = prices
        info["price_text"] = _format_price_text(prices)
        info["tcgplayer_url"] = purchase_uris.get("tcgplayer") or related_uris.get("tcgplayer")
    else:
        info["purchase_uris"] = {}
        info["related_uris"] = {}
        info["prices"] = {}
        info["price_text"] = None
        info["tcgplayer_url"] = None

    if oracle_id:
        info["oracle_id"] = oracle_id
        if not info.get("prints_search_uri"):
            info["prints_search_uri"] = f"https://api.scryfall.com/cards/search?order=released&q=oracleid:{oracle_id}&unique=prints"

    unique_variants = _unique_art_variants(prints)
    owned_art_id = (best or {}).get("illustration_id") or (best or {}).get("id")
    ordered_variants: list[dict] = []
    if best:
        for pr in unique_variants:
            variant_id = pr.get("illustration_id") or pr.get("id")
            if variant_id == owned_art_id:
                ordered_variants.append(pr)
                break
        for pr in unique_variants:
            variant_id = pr.get("illustration_id") or pr.get("id")
            if variant_id != owned_art_id:
                ordered_variants.append(pr)
    else:
        ordered_variants = unique_variants

    images = []
    for pr in ordered_variants:
        image_pack = _image_from_print(pr)
        if not (image_pack["small"] or image_pack["normal"] or image_pack["large"]):
            continue
        bits = []
        if pr.get("set"):
            bits.append(pr.get("set").upper())
        if pr.get("collector_number"):
            bits.append(str(pr.get("collector_number")))
        if pr.get("lang"):
            bits.append(str(pr.get("lang")).upper())
        label = " · ".join(bits) if bits else (pr.get("name") or card.name)
        images.append(
            {
                "small": image_pack["small"],
                "normal": image_pack["normal"],
                "large": image_pack["large"],
                "label": label,
            }
        )

    tokens_created = _token_stubs_from_oracle_text(info.get("oracle_text"))
    pip_srcs = colors_to_icons(info.get("color_identity") or info.get("colors"), use_local=False)
    rulings = _request_cached_rulings(oracle_id)

    selected = ordered_variants[0] if ordered_variants else {}
    selected_image_pack = _image_from_print(selected)
    main_img_url = selected_image_pack.get("large") or selected_image_pack.get("normal") or selected_image_pack.get("small")
    if not main_img_url and images:
        first = images[0]
        main_img_url = first.get("large") or first.get("normal") or first.get("small")

    display_name = info.get("name") or card.name
    role_labels = [(role.label or getattr(role, "name", None) or role.key) for role in (card.roles or [])]
    subrole_labels = [(subrole.label or getattr(subrole, "name", None) or subrole.key) for subrole in (card.subroles or [])]
    primary_role_label = _request_cached_primary_role_label(card.id)
    evergreen_labels = _request_cached_evergreen_labels(oracle_id)
    core_role_labels = _request_cached_core_role_labels(oracle_id)
    if core_role_labels:
        if not role_labels:
            role_labels = core_role_labels
        else:
            for label in core_role_labels:
                if label not in role_labels:
                    role_labels.append(label)
    if not primary_role_label:
        primary_role_label = _request_cached_primary_oracle_role_label(oracle_id)

    commander_legality = info.get("commander_legality")
    commander_label = None
    commander_class = None
    if commander_legality:
        legality_value = str(commander_legality)
        commander_label = "Not legal" if legality_value == "not_legal" else legality_value.replace("_", " ").capitalize()
        if legality_value == "legal":
            commander_class = "bg-success"
        elif legality_value == "banned":
            commander_class = "bg-danger"
        elif legality_value == "restricted":
            commander_class = "bg-warning text-dark"
        else:
            commander_class = "bg-secondary"

    prices_json = json.dumps(prices or {}, ensure_ascii=True)
    info_vm = CardInfoVM(
        name=info.get("name"),
        mana_cost_html=info.get("mana_cost_html"),
        cmc=info.get("cmc"),
        type_line=info.get("type_line"),
        oracle_text_html=info.get("oracle_text_html"),
        colors=info.get("colors") or [],
        color_identity=info.get("color_identity") or [],
        keywords=info.get("keywords") or [],
        rarity=info.get("rarity"),
        set_code=info.get("set"),
        set_name=info.get("set_name"),
        collector_number=info.get("collector_number"),
        scryfall_uri=info.get("scryfall_uri"),
        scryfall_set_uri=info.get("scryfall_set_uri"),
        commander_legality=commander_legality,
        commander_legality_label=commander_label,
        commander_legality_class=commander_class,
        has_commander_legality=bool(commander_label),
        price_text=info.get("price_text"),
        tcgplayer_url=info.get("tcgplayer_url"),
        prints_search_uri=info.get("prints_search_uri"),
        lang=info.get("lang"),
        oracle_id=info.get("oracle_id"),
        prices_json=prices_json,
        has_prices=bool(prices),
        has_oracle_text=bool(info.get("oracle_text_html")) and info.get("oracle_text_html") != "—",
        has_mana_cost=bool(info.get("mana_cost_html")),
        has_scryfall_uri=bool(info.get("scryfall_uri")),
        has_scryfall_set_uri=bool(info.get("scryfall_set_uri")),
    )

    print_images = []
    if prints:
        ordered_prints = []
        if best:
            ordered_prints.append(best)
        ordered_prints.extend([pr for pr in prints if pr is not best])
        seen_prints: set[str] = set()
        for pr in ordered_prints:
            pid = pr.get("id") or ""
            if pid and pid in seen_prints:
                continue
            if pid:
                seen_prints.add(pid)
            image_pack = _image_from_print(pr)
            if not (image_pack.get("small") or image_pack.get("normal") or image_pack.get("large")):
                continue
            set_code = (pr.get("set") or "").upper()
            collector_number = str(pr.get("collector_number") or "")
            lang_code = str(pr.get("lang") or "").upper()
            label_bits = [value for value in (set_code, collector_number, lang_code) if value]
            label = " · ".join(label_bits) if label_bits else (pr.get("name") or card.name)
            purchase = pr.get("purchase_uris") or {}
            related = pr.get("related_uris") or {}
            print_images.append(
                {
                    "id": pid,
                    "set": set_code,
                    "setName": pr.get("set_name") or (set_name_for_code(pr.get("set")) if have_cache and pr.get("set") else ""),
                    "collectorNumber": collector_number,
                    "lang": lang_code,
                    "rarity": pr.get("rarity") or "",
                    "prices": pr.get("prices") or {},
                    "name": pr.get("name") or card.name,
                    "scryUri": pr.get("scryfall_uri") or _scryfall_card_url(pr.get("set"), pr.get("collector_number")),
                    "tcgUri": purchase.get("tcgplayer") or related.get("tcgplayer") or "",
                    "releasedAt": pr.get("released_at") or "",
                    "small": image_pack.get("small") or image_pack.get("normal") or image_pack.get("large"),
                    "normal": image_pack.get("normal") or image_pack.get("large") or image_pack.get("small"),
                    "large": image_pack.get("large") or image_pack.get("normal") or image_pack.get("small"),
                    "label": label,
                }
            )

    image_vms = [ImageSetVM(small=img.get("small"), normal=img.get("normal"), large=img.get("large"), label=img.get("label")) for img in images]
    token_vms: list[CardTokenVM] = []
    for token in tokens_created or []:
        image_pack = token.get("images") or sc.image_for_print(token) or {}
        token_vms.append(
            CardTokenVM(
                id=token.get("id"),
                name=token.get("name"),
                type_line=token.get("type_line"),
                images=ImageSetVM(
                    small=image_pack.get("small"),
                    normal=image_pack.get("normal"),
                    large=image_pack.get("large"),
                ),
            )
        )

    folder_ref = FolderRefVM(id=card.folder.id, name=card.folder.name) if getattr(card, "folder", None) else None
    card_vm = CardListItemVM(
        id=card.id,
        name=card.name,
        display_name=display_name,
        quantity=int(card.quantity or 0) or 1,
        folder=folder_ref,
        set_code=card.set_code,
        collector_number=str(card.collector_number) if card.collector_number is not None else None,
        lang=card.lang,
        is_foil=bool(card.is_foil),
    )

    return render_template(
        "cards/card_detail.html",
        card=card_vm,
        info=info_vm,
        images=image_vms,
        print_images_json=json.dumps(print_images, ensure_ascii=True),
        rulings=rulings,
        color_pips=pip_srcs,
        tokens_created=token_vms,
        scryfall_id=selected.get("id"),
        oracle_id=oracle_id,
        main_img_url=main_img_url,
        name=display_name,
        collection_folders=collection_folder_names,
        primary_role_label=primary_role_label,
        role_labels=role_labels,
        subrole_labels=subrole_labels,
        evergreen_labels=evergreen_labels,
    )


def smart_card_detail(id_or_sid):
    """Smart redirect: integer -> owned card, else -> Scryfall print detail."""
    try:
        return redirect(url_for("views.card_detail", card_id=int(id_or_sid)))
    except ValueError:
        return redirect(url_for("views.scryfall_print_detail", sid=id_or_sid))


__all__ = ["api_card", "card_detail", "smart_card_detail"]
