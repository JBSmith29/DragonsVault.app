"""User dashboard service."""

from __future__ import annotations

from flask import current_app, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import func, text
from sqlalchemy.orm import load_only, selectinload

from extensions import db
from models import Card, Folder, FolderRole, UserSetting
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    cache_epoch,
    ensure_cache_loaded,
    find_by_set_cn,
    prints_for_oracle,
)
from core.domains.cards.viewmodels.card_vm import ImageSetVM
from core.domains.decks.services.commander_utils import (
    primary_commander_oracle_id,
    split_commander_oracle_ids,
)
from core.domains.decks.services.deck_gallery_shared_service import (
    commander_thumbnail_payload,
    prefetch_commander_cards,
)
from core.domains.decks.viewmodels.deck_vm import DeckCommanderVM, DeckVM
from core.domains.users.viewmodels.dashboard_vm import (
    DashboardActionVM,
    DashboardCollectionStatsVM,
    DashboardModeOptionVM,
    DashboardStatTileVM,
    DashboardTopCardVM,
    DashboardViewModel,
)
from core.shared.utils.assets import static_url
from shared.cache.request_cache import request_cached
from shared.cache.runtime_cache import cache_fetch
from shared.mtg import (
    _bulk_print_lookup,
    _img_url_for_print,
    _lookup_print_data,
    color_identity_name,
    compute_folder_color_identity,
)

_DASHBOARD_SETTING_KEY = "dashboard_mode"
_DEFAULT_DASHBOARD_MODE = "collection"
_DASHBOARD_MODES = {
    "collection": {
        "label": "Collection",
        "description": "Collection insights and ownership.",
        "partial": "dashboard/_collection.html",
    },
    "decks": {
        "label": "Decks",
        "description": "Deck overview and maintenance.",
        "partial": "dashboard/_decks.html",
    },
}
_DASHBOARD_MODE_SEQUENCE = ("collection", "decks")


def dashboard_index():
    return redirect(url_for("views.dashboard"))


def _normalize_dashboard_mode(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in _DASHBOARD_MODES:
        return normalized
    return _DEFAULT_DASHBOARD_MODE


def _is_missing_table_error(exc: Exception, table_name: str) -> bool:
    message = str(exc).lower()
    return table_name.lower() in message and ("does not exist" in message or "undefinedtable" in message)


def _ensure_user_settings_table() -> bool:
    try:
        db.session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
        )
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to ensure user_settings table.")
        return False


def _load_dashboard_mode() -> str:
    try:
        setting = db.session.get(UserSetting, _DASHBOARD_SETTING_KEY)
    except Exception as exc:
        db.session.rollback()
        if _is_missing_table_error(exc, "user_settings") and _ensure_user_settings_table():
            try:
                setting = db.session.get(UserSetting, _DASHBOARD_SETTING_KEY)
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to load dashboard mode preference after ensuring table.")
                return _DEFAULT_DASHBOARD_MODE
        else:
            current_app.logger.exception("Failed to load dashboard mode preference.")
            return _DEFAULT_DASHBOARD_MODE
    if setting and setting.value:
        return _normalize_dashboard_mode(setting.value)
    return _DEFAULT_DASHBOARD_MODE


def _persist_dashboard_mode(mode: str) -> None:
    mode = _normalize_dashboard_mode(mode)
    try:
        setting = db.session.get(UserSetting, _DASHBOARD_SETTING_KEY)
        if setting:
            setting.value = mode
        else:
            db.session.add(UserSetting(key=_DASHBOARD_SETTING_KEY, value=mode))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if _is_missing_table_error(exc, "user_settings") and _ensure_user_settings_table():
            try:
                setting = db.session.get(UserSetting, _DASHBOARD_SETTING_KEY)
                if setting:
                    setting.value = mode
                else:
                    db.session.add(UserSetting(key=_DASHBOARD_SETTING_KEY, value=mode))
                db.session.commit()
                return
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to update dashboard mode preference after ensuring table.")
                return
        current_app.logger.exception("Failed to update dashboard mode preference.")


def _dashboard_card_stats(
    user_key: str,
    folder_ids: tuple[int, ...],
    collection_ids: tuple[int, ...],
) -> dict:
    cache_key = ("dashboard_stats", user_key, folder_ids, collection_ids)

    def _load() -> dict:
        if not folder_ids:
            return {
                "rows": 0,
                "qty": 0,
                "unique_names": 0,
                "sets": 0,
                "collection_qty": 0,
            }

        total_rows, total_qty, unique_names, set_count = (
            db.session.query(
                func.count(Card.id),
                func.coalesce(func.sum(Card.quantity), 0),
                func.count(func.distinct(Card.name)),
                func.count(func.distinct(func.lower(Card.set_code))),
            )
            .filter(Card.folder_id.in_(folder_ids))
            .one()
        )

        collection_qty = 0
        if collection_ids:
            collection_qty = (
                db.session.query(func.coalesce(func.sum(Card.quantity), 0))
                .filter(Card.folder_id.in_(collection_ids))
                .scalar()
                or 0
            )

        return {
            "rows": int(total_rows or 0),
            "qty": int(total_qty or 0),
            "unique_names": int(unique_names or 0),
            "sets": int(set_count or 0),
            "collection_qty": int(collection_qty or 0),
        }

    return request_cached(cache_key, _load)


def _dashboard_price_to_float(value: object | None) -> float | None:
    if value in (None, "", 0, "0", "0.0", "0.00"):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _dashboard_price_value(prices: dict | None, is_foil: bool) -> float | None:
    if not prices:
        return None
    keys = ("usd_foil", "usd", "usd_etched") if is_foil else ("usd", "usd_foil", "usd_etched")
    for key in keys:
        value = _dashboard_price_to_float(prices.get(key))
        if value is not None:
            return value
    for key in ("eur", "eur_foil", "tix"):
        value = _dashboard_price_to_float(prices.get(key))
        if value is not None:
            return value
    return None


def _dashboard_price_text(prices: dict | None, is_foil: bool) -> str | None:
    if not prices:
        return None

    def _fmt(value: object | None, prefix: str) -> str | None:
        if value in (None, "", 0, "0", "0.0", "0.00"):
            return None
        try:
            num = float(value)
        except (TypeError, ValueError):
            return None
        if num <= 0:
            return None
        return f"{prefix}{num:,.2f}".replace(",", "")

    if is_foil:
        value = _fmt(prices.get("usd_foil"), "$") or _fmt(prices.get("usd"), "$") or _fmt(prices.get("usd_etched"), "$")
        if value:
            return value
        value = _fmt(prices.get("eur_foil"), "EUR ") or _fmt(prices.get("eur"), "EUR ")
        if value:
            return value
    else:
        value = _fmt(prices.get("usd"), "$") or _fmt(prices.get("usd_foil"), "$") or _fmt(prices.get("usd_etched"), "$")
        if value:
            return value
        value = _fmt(prices.get("eur"), "EUR ") or _fmt(prices.get("eur_foil"), "EUR ")
        if value:
            return value

    return _fmt(prices.get("tix"), "TIX ")


def _dashboard_top_cards(user_id: int | None, folder_ids: list[int]) -> list[DashboardTopCardVM]:
    if not user_id or not folder_ids:
        return []
    cache_key = f"dashboard_top_cards:{user_id}:{cache_epoch()}"

    def _load() -> list[DashboardTopCardVM]:
        cards = (
            Card.query.options(
                load_only(
                    Card.id,
                    Card.name,
                    Card.set_code,
                    Card.collector_number,
                    Card.oracle_id,
                    Card.lang,
                    Card.is_foil,
                    Card.folder_id,
                ),
                selectinload(Card.folder).load_only(Folder.id, Folder.name),
            )
            .join(Folder, Folder.id == Card.folder_id)
            .filter(
                Card.folder_id.in_(folder_ids),
                Folder.is_proxy.is_(False),
            )
            .all()
        )
        if not cards:
            return []
        if not sc.cache_ready():
            sc.ensure_cache_loaded()
        print_map = _bulk_print_lookup(cards)
        ranked: list[tuple[float, str, DashboardTopCardVM]] = []
        for card in cards:
            print_data = print_map.get(card.id, {}) or {}
            prices = print_data.get("prices") or {}
            value = _dashboard_price_value(prices, bool(card.is_foil))
            if value is None:
                continue
            price_text = _dashboard_price_text(prices, bool(card.is_foil))
            image = _img_url_for_print(print_data, "normal") or _img_url_for_print(print_data, "small")
            folder = getattr(card, "folder", None)
            folder_name = folder.name if folder and folder.name else "Unknown folder"
            printing_label = None
            set_code = (card.set_code or "").strip().upper()
            collector_number = str(card.collector_number or "").strip()
            if set_code and collector_number:
                printing_label = f"{set_code} #{collector_number}"
            elif set_code:
                printing_label = set_code
            ranked.append(
                (
                    float(value),
                    (card.name or "").lower(),
                    DashboardTopCardVM(
                        id=card.id,
                        name=card.name or "",
                        image=image,
                        price_text=price_text,
                        folder_name=folder_name,
                        card_href=url_for("views.card_detail", card_id=card.id),
                        printing_label=printing_label,
                    ),
                )
            )
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [vm for _, __, vm in ranked[:10]]

    return cache_fetch(cache_key, 300, _load)


def _color_identity_html(letters: str) -> str:
    if not letters:
        return f'<span class="pip-row"><img class="mana mana-sm" src="{static_url("symbols/C.svg")}" alt="{{C}}"></span>'
    return (
        '<span class="pip-row">'
        + "".join(
            f'<img class="mana mana-sm" src="{static_url(f"symbols/{color}.svg")}" alt="{{{color}}}">'
            for color in letters
        )
        + "</span>"
    )


def _exact_print_for_card(card_row: Card | None) -> dict | None:
    if not card_row:
        return None
    try:
        print_data = find_by_set_cn(card_row.set_code, card_row.collector_number, card_row.name)
        if print_data:
            return print_data
    except Exception:
        pass
    return _lookup_print_data(
        getattr(card_row, "set_code", None),
        getattr(card_row, "collector_number", None),
        getattr(card_row, "name", None),
        getattr(card_row, "oracle_id", None),
    )


def dashboard():
    if request.method == "POST":
        selected_mode = _normalize_dashboard_mode(request.form.get("dashboard_mode"))
        _persist_dashboard_mode(selected_mode)
        return redirect(url_for("views.dashboard"))

    mode = _load_dashboard_mode()
    mode_meta = _DASHBOARD_MODES.get(mode, _DASHBOARD_MODES[_DEFAULT_DASHBOARD_MODE])
    is_authenticated = bool(current_user and getattr(current_user, "is_authenticated", False))
    owner_id = current_user.id if is_authenticated else None

    user_folder_ids: list[int] = []
    collection_ids: list[int] = []
    if owner_id:
        user_folder_ids = [
            folder_id
            for (folder_id,) in db.session.query(Folder.id)
            .filter(Folder.owner_user_id == owner_id)
            .all()
        ]
        collection_ids = [
            folder_id
            for (folder_id,) in db.session.query(Folder.id)
            .join(FolderRole, FolderRole.folder_id == Folder.id)
            .filter(
                FolderRole.role == FolderRole.ROLE_COLLECTION,
                Folder.owner_user_id == owner_id,
            )
            .all()
        ]

    stats_key = str(session.get("_user_id") or "anon")
    stats = _dashboard_card_stats(stats_key, tuple(sorted(user_folder_ids)), tuple(sorted(collection_ids)))
    total_qty = stats["qty"]
    unique_names = stats["unique_names"]
    set_count = stats["sets"]
    collection_qty = stats["collection_qty"]
    collection_bucket_count = len(collection_ids)

    _ = ensure_cache_loaded()
    deck_query = (
        db.session.query(
            Folder.id,
            Folder.name,
            func.count(Card.id).label("rows"),
            func.coalesce(func.sum(Card.quantity), 0).label("qty"),
        )
        .outerjoin(Card, Card.folder_id == Folder.id)
        .filter(Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES)))
    )
    if owner_id:
        deck_query = deck_query.filter(Folder.owner_user_id == owner_id)
    deck_rows = (
        deck_query.group_by(Folder.id, Folder.name)
        .order_by(func.coalesce(func.sum(Card.quantity), 0).desc(), Folder.name.asc())
        .all()
    )
    decks = [
        {"id": deck_id, "name": deck_name, "rows": int(rows or 0), "qty": int(qty or 0)}
        for deck_id, deck_name, rows, qty in deck_rows
    ]

    deck_vms: list[DeckVM] = []
    placeholder_thumb = static_url("img/card-placeholder.svg")
    if decks:
        deck_folder_ids = [deck["id"] for deck in decks]
        folder_map = {folder.id: folder for folder in Folder.query.filter(Folder.id.in_(deck_folder_ids)).all()}
        commander_cards = prefetch_commander_cards(folder_map)
        epoch = cache_epoch()

        for deck in decks:
            folder_id = deck["id"]
            folder = folder_map.get(folder_id)
            commander_card = commander_cards.get(folder_id) if folder else None
            oracle_ids = []
            if folder:
                oracle_ids = [oracle_id.strip() for oracle_id in split_commander_oracle_ids(folder.commander_oracle_id) if oracle_id.strip()]
            print_data = _exact_print_for_card(commander_card) if commander_card else None
            images: list[dict[str, str | None]] = []

            def add_image_from_print(print_obj: dict | None, name_hint: str | None = None) -> None:
                if not print_obj:
                    return
                name_value = name_hint or getattr(folder, "commander_name", None) or (
                    commander_card.name if commander_card else print_obj.get("name")
                )
                images.append(
                    {
                        "name": name_value,
                        "small": _img_url_for_print(print_obj, "small") or _img_url_for_print(print_obj, "normal") or placeholder_thumb,
                        "normal": _img_url_for_print(print_obj, "normal") or _img_url_for_print(print_obj, "large") or placeholder_thumb,
                        "large": _img_url_for_print(print_obj, "large") or _img_url_for_print(print_obj, "normal") or placeholder_thumb,
                        "alt": name_value or "Commander",
                    }
                )

            if not print_data and folder:
                print_data = _lookup_print_data(
                    getattr(folder, "commander_set_code", None),
                    getattr(folder, "commander_collector_number", None),
                    getattr(folder, "commander_name", None),
                    primary_commander_oracle_id(getattr(folder, "commander_oracle_id", None)),
                )
            if print_data:
                add_image_from_print(print_data)

            if not images and oracle_ids:
                primary_oid = primary_commander_oracle_id(getattr(folder, "commander_oracle_id", None)) if folder else None
                target_oid = primary_oid or oracle_ids[0]
                try:
                    prints = prints_for_oracle(target_oid) or []
                except Exception:
                    prints = []
                if prints:
                    add_image_from_print(prints[0])

            if not images:
                target_oid = primary_commander_oracle_id(getattr(folder, "commander_oracle_id", None)) if folder else None
                thumb_payload = commander_thumbnail_payload(
                    folder_id,
                    target_oid,
                    getattr(folder, "commander_name", None) if folder else None,
                    deck.get("rows") or 0,
                    deck.get("qty") or 0,
                    epoch,
                )
                images.append(
                    {
                        "name": thumb_payload.get("name"),
                        "small": thumb_payload.get("small") or placeholder_thumb,
                        "normal": None,
                        "large": thumb_payload.get("large") or placeholder_thumb,
                        "alt": thumb_payload.get("alt") or (thumb_payload.get("name") or "Commander"),
                    }
                )

            commander_vm = None
            if images:
                primary = images[0]
                commander_vm = DeckCommanderVM(
                    name=primary.get("name"),
                    small=primary.get("small"),
                    large=primary.get("large"),
                    alt=primary.get("alt"),
                    images=[
                        ImageSetVM(
                            small=image.get("small"),
                            normal=image.get("normal"),
                            large=image.get("large"),
                            label=image.get("name"),
                        )
                        for image in images
                    ],
                )

            letters, _label = compute_folder_color_identity(folder_id, "20260311a")
            letters = letters or ""
            deck_vms.append(
                DeckVM(
                    id=folder_id,
                    name=deck.get("name") or "",
                    qty=int(deck.get("qty") or 0),
                    owner=getattr(folder, "owner", None) if folder else None,
                    owner_key=(getattr(folder, "owner", None) or "").strip().lower() if folder else "",
                    is_proxy=bool(getattr(folder, "is_proxy", False)) if folder else False,
                    is_owner=bool(folder and is_authenticated and folder.owner_user_id == current_user.id),
                    tag=getattr(folder, "deck_tag", None) if folder else None,
                    tag_label=getattr(folder, "deck_tag", None) if folder else None,
                    ci_name=color_identity_name(letters),
                    ci_html=_color_identity_html(letters),
                    ci_letters=letters or "C",
                    commander=commander_vm,
                    bracket_level=None,
                    bracket_label=None,
                )
            )

    def _format_stat(value: int | None) -> str:
        if value is None:
            return "—"
        return f"{value:,}"

    deck_count = len(deck_vms)
    deck_tiles = [
        DashboardStatTileVM(
            label="Decks",
            value=_format_stat(deck_count),
            href=url_for("views.decks_overview"),
            icon="bi bi-collection",
        ),
        DashboardStatTileVM(
            label="Total Cards",
            value=_format_stat(total_qty),
            href=url_for("views.list_cards"),
            icon="bi bi-stack",
        ),
        DashboardStatTileVM(
            label="Collection Cards",
            value=_format_stat(collection_qty),
            href=url_for("views.collection_overview"),
            icon="bi bi-box-seam",
        ),
        DashboardStatTileVM(
            label="Sets",
            value=_format_stat(set_count),
            href=url_for("views.sets_overview"),
            icon="bi bi-grid-3x3-gap",
        ),
    ]
    collection_tiles = [
        DashboardStatTileVM(
            label="Collection Cards",
            value=_format_stat(collection_qty),
            href=url_for("views.collection_overview"),
            icon="bi bi-box-seam",
        ),
        DashboardStatTileVM(
            label="Total Cards",
            value=_format_stat(total_qty),
            href=url_for("views.list_cards"),
            icon="bi bi-stack",
        ),
        DashboardStatTileVM(
            label="Unique Cards",
            value=_format_stat(unique_names),
            href=url_for("views.list_cards"),
            icon="bi bi-card-list",
        ),
        DashboardStatTileVM(
            label="Sets",
            value=_format_stat(set_count),
            href=url_for("views.sets_overview"),
            icon="bi bi-grid-3x3-gap",
        ),
    ]
    collection_stats = DashboardCollectionStatsVM(
        total_qty=int(total_qty or 0),
        collection_qty=int(collection_qty or 0),
        unique_names=int(unique_names or 0),
        set_count=int(set_count or 0),
        collection_bucket_count=int(collection_bucket_count or 0),
    )
    collection_actions = [
        DashboardActionVM(
            label="Collection",
            href=url_for("views.collection_overview"),
            icon="bi bi-box-seam",
        ),
        DashboardActionVM(
            label="Browse Cards",
            href=url_for("views.list_cards"),
            icon="bi bi-stack",
        ),
        DashboardActionVM(
            label="Sets",
            href=url_for("views.sets_overview"),
            icon="bi bi-grid-3x3-gap",
        ),
        DashboardActionVM(
            label="Import CSV",
            href=url_for("views.import_csv"),
            icon="bi bi-file-earmark-arrow-up",
        ),
        DashboardActionVM(
            label="Dragonshield",
            href="https://mtg.dragonshield.com",
            icon="bi bi-shield",
            external=True,
        ),
        DashboardActionVM(
            label="Wishlist",
            href=url_for("views.wishlist"),
            icon="bi bi-heart",
        ),
        DashboardActionVM(
            label="List Checker",
            href=url_for("views.list_checker"),
            icon="bi bi-list-check",
        ),
    ]
    collection_top_cards: list[DashboardTopCardVM] = []
    if mode == "collection":
        collection_top_cards = _dashboard_top_cards(owner_id, user_folder_ids)
    deck_actions = [
        DashboardActionVM(
            label="Opening Hand",
            href=url_for("views.opening_hand"),
            icon="bi bi-hand-thumbs-up",
        ),
        DashboardActionVM(
            label="Deck Tokens",
            href=url_for("views.deck_tokens_overview"),
            icon="bi bi-layers",
        ),
        DashboardActionVM(
            label="Commander Bracket",
            href=url_for("views.commander_brackets_info"),
            icon="bi bi-trophy",
        ),
        DashboardActionVM(
            label="Spellbook Combos",
            href=url_for("views.commander_spellbook_combos"),
            icon="bi bi-lightning-charge",
        ),
    ]
    mode_options = [
        DashboardModeOptionVM(
            value=mode_key,
            label=_DASHBOARD_MODES[mode_key]["label"],
            selected=mode_key == mode,
        )
        for mode_key in _DASHBOARD_MODE_SEQUENCE
    ]
    dashboard_vm = DashboardViewModel(
        mode=mode,
        mode_label=mode_meta["label"],
        mode_description=mode_meta["description"],
        content_partial=mode_meta["partial"],
        mode_options=mode_options,
        collection_tiles=collection_tiles,
        deck_tiles=deck_tiles,
        collection_actions=collection_actions,
        deck_actions=deck_actions,
        decks=deck_vms,
        collection_stats=collection_stats,
        collection_top_cards=collection_top_cards,
    )
    return render_template("dashboard.html", dashboard=dashboard_vm)


__all__ = ["dashboard", "dashboard_index"]
