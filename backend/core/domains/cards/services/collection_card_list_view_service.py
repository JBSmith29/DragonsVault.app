"""Card-list view helpers for collection browsing."""

from __future__ import annotations

import re

from extensions import db
from models import Card, User
from models.role import OracleCoreRoleTag, OracleEvergreenTag
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.pricing import prices_for_print_exact as _prices_for_print_exact
from core.domains.cards.viewmodels.card_vm import CardListItemVM, FolderRefVM, format_role_label, slice_badges
from shared.mtg import (
    _bulk_print_lookup,
    _color_letters_list,
    _effective_color_identity,
    _oracle_text_from_faces,
    _type_line_from_print,
)


def image_from_print_payload(print_payload: dict | None):
    if not print_payload:
        return None
    image_uris = print_payload.get("image_uris")
    if image_uris:
        return image_uris.get("small") or image_uris.get("normal") or image_uris.get("large")
    faces = print_payload.get("card_faces") or []
    if faces:
        image_uris = (faces[0] or {}).get("image_uris") or {}
        return image_uris.get("small") or image_uris.get("normal") or image_uris.get("large")
    return None


def _price_to_float(value):
    if value in (None, "", 0, "0", "0.0", "0.00"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def format_exact_price(prices: dict | None, is_foil: bool) -> str | None:
    if not prices:
        return None

    def _format(value, prefix):
        if value in (None, "", 0, "0", "0.0", "0.00"):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        return f"{prefix}{number:,.2f}".replace(",", "")

    if is_foil:
        value = _format(prices.get("usd_foil"), "$") or _format(prices.get("usd"), "$") or _format(prices.get("usd_etched"), "$")
        if value:
            return value
        value = _format(prices.get("eur_foil"), "EUR ") or _format(prices.get("eur"), "EUR ")
        if value:
            return value
    else:
        value = _format(prices.get("usd"), "$") or _format(prices.get("usd_foil"), "$") or _format(prices.get("usd_etched"), "$")
        if value:
            return value
        value = _format(prices.get("eur"), "EUR ") or _format(prices.get("eur_foil"), "EUR ")
        if value:
            return value

    return _format(prices.get("tix"), "TIX ")


def price_value_from_exact_prices(prices: dict | None, is_foil: bool) -> float | None:
    if not prices:
        return None
    keys = ("usd_foil", "usd", "usd_etched") if is_foil else ("usd", "usd_foil", "usd_etched")
    for key in keys:
        value = _price_to_float(prices.get(key))
        if value is not None:
            return value
    for key in ("eur", "eur_foil", "tix"):
        value = _price_to_float(prices.get(key))
        if value is not None:
            return value
    return None


def _rarity_badge_class(label: str | None) -> str | None:
    normalized = (label or "").strip().lower()
    if normalized == "common":
        return "secondary"
    if normalized == "uncommon":
        return "success"
    if normalized == "rare":
        return "warning"
    if normalized in {"mythic", "mythic rare"}:
        return "danger"
    return None


def build_collection_card_list_items(
    cards: list[Card],
    *,
    base_types: list[str],
    current_user_id: int | None = None,
) -> list[CardListItemVM]:
    oracle_ids = {card_obj.oracle_id for card_obj in cards if card_obj.oracle_id}
    core_role_map: dict[str, list[str]] = {}
    evergreen_map: dict[str, list[str]] = {}
    if oracle_ids:
        core_rows = (
            db.session.query(OracleCoreRoleTag.oracle_id, OracleCoreRoleTag.role)
            .filter(OracleCoreRoleTag.oracle_id.in_(oracle_ids))
            .order_by(OracleCoreRoleTag.role.asc())
            .all()
        )
        for oracle_id, role in core_rows:
            if not role:
                continue
            bucket = core_role_map.setdefault(oracle_id, [])
            if role not in bucket:
                bucket.append(role)
        evergreen_rows = (
            db.session.query(OracleEvergreenTag.oracle_id, OracleEvergreenTag.keyword)
            .filter(OracleEvergreenTag.oracle_id.in_(oracle_ids))
            .order_by(OracleEvergreenTag.keyword.asc())
            .all()
        )
        for oracle_id, keyword in evergreen_rows:
            if not keyword:
                continue
            bucket = evergreen_map.setdefault(oracle_id, [])
            if keyword not in bucket:
                bucket.append(keyword)

    if not sc.cache_ready():
        sc.ensure_cache_loaded()

    print_map = _bulk_print_lookup(cards)
    owner_label_map: dict[int, str] = {}
    owner_ids: set[int] = set()
    for card_obj in cards:
        folder = getattr(card_obj, "folder", None)
        owner_id = getattr(folder, "owner_user_id", None)
        if isinstance(owner_id, int):
            owner_ids.add(owner_id)
    if owner_ids:
        owner_rows = (
            db.session.query(User.id, User.display_name, User.username, User.email)
            .filter(User.id.in_(owner_ids))
            .all()
        )
        for uid, display_name, username, email in owner_rows:
            label = display_name or username or email
            if label:
                owner_label_map[uid] = label

    cards_vm: list[CardListItemVM] = []
    for card_obj in cards:
        print_data = print_map.get(card_obj.id, {})
        image_package = sc.image_for_print(print_data) if print_data else {}
        thumb_src = image_package.get("small") or image_package.get("normal") or image_package.get("large")
        hover_src = image_package.get("large") or image_package.get("normal") or image_package.get("small")
        if not thumb_src:
            thumb_src = image_from_print_payload(print_data)
        type_line = (getattr(card_obj, "type_line", None) or "").strip() or _type_line_from_print(print_data)
        print_oracle_text = ""
        if print_data:
            print_oracle_text = print_data.get("oracle_text") or _oracle_text_from_faces(print_data.get("card_faces")) or ""

        color_value = getattr(card_obj, "color_identity", None) or getattr(card_obj, "colors", None)
        if isinstance(color_value, (list, tuple, set)):
            db_color_letters = [str(item).upper() for item in color_value if str(item).upper()]
        else:
            db_color_letters = [ch for ch in str(color_value or "").upper() if ch in "WUBRG"]
        print_color_letters = _color_letters_list((print_data or {}).get("color_identity")) or _color_letters_list((print_data or {}).get("colors"))
        resolved_color_letters = db_color_letters or print_color_letters
        resolved_color_letters = _effective_color_identity(type_line, print_oracle_text, resolved_color_letters)
        if not resolved_color_letters:
            resolved_color_letters = ["C"]

        rarity_value = (getattr(card_obj, "rarity", None) or "").strip().lower() or ((print_data or {}).get("rarity") or "").strip().lower()
        type_badges = [value for value in base_types if value.lower() in (type_line or "").lower()]
        type_tokens = [value.lower() for value in type_badges] if type_badges else []
        if not type_tokens and type_line:
            raw_tokens = re.split(r"[\s\-/,]+", type_line)
            type_tokens = [token.lower() for token in raw_tokens if token]
        prices = _prices_for_print_exact(print_data) if print_data else {}
        price_text = format_exact_price(prices, bool(card_obj.is_foil))

        core_roles_raw = core_role_map.get(card_obj.oracle_id or "", []) if card_obj.oracle_id else []
        core_roles_labels = [format_role_label(role) for role in core_roles_raw]
        core_display, core_overflow = slice_badges(core_roles_labels)
        evergreen_raw = evergreen_map.get(card_obj.oracle_id or "", []) if card_obj.oracle_id else []
        evergreen_labels = [format_role_label(tag) for tag in evergreen_raw]
        evergreen_display, evergreen_overflow = slice_badges(evergreen_labels)
        rarity_label = rarity_value.replace("_", " ").title() if rarity_value else None

        folder_ref = None
        owner_label = None
        if getattr(card_obj, "folder", None):
            folder_ref = FolderRefVM(id=card_obj.folder.id, name=card_obj.folder.name)
            owner_id = getattr(card_obj.folder, "owner_user_id", None)
            if owner_id is not None:
                if current_user_id and owner_id == current_user_id:
                    owner_label = "You"
                else:
                    owner_label = owner_label_map.get(owner_id)
            owner_label = owner_label or getattr(card_obj.folder, "owner", None)

        cards_vm.append(
            CardListItemVM(
                id=card_obj.id,
                name=card_obj.name,
                display_name=card_obj.name,
                quantity=int(card_obj.quantity or 0) or 1,
                folder=folder_ref,
                set_code=card_obj.set_code,
                collector_number=str(card_obj.collector_number) if card_obj.collector_number is not None else None,
                lang=card_obj.lang,
                is_foil=bool(card_obj.is_foil),
                image_small=thumb_src,
                image_large=hover_src,
                type_line=type_line,
                type_badges=type_badges,
                type_tokens=type_tokens,
                core_roles_display=core_display,
                core_roles_overflow=core_overflow,
                evergreen_display=evergreen_display,
                evergreen_overflow=evergreen_overflow,
                color_letters=resolved_color_letters,
                rarity_label=rarity_label,
                rarity_badge_class=_rarity_badge_class(rarity_label),
                price_text=price_text,
                owner_label=owner_label,
            )
        )

    return cards_vm


__all__ = [
    "build_collection_card_list_items",
    "format_exact_price",
    "image_from_print_payload",
    "price_value_from_exact_prices",
]
