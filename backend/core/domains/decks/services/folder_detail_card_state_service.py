"""Raw card-state builders for folder detail rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import case
from sqlalchemy.orm import load_only

from models import Card, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.pricing import prices_for_print as _prices_for_print
from core.domains.cards.services.scryfall_cache import cache_epoch
from core.domains.decks.services.deck_tags import get_deck_tag_category
from core.domains.decks.services.folder_detail_analysis_service import (
    WUBRG,
    artifact_production_colors,
    oracle_text_from_faces_json,
    type_line_from_print_payload,
)
from core.shared.utils.symbols_cache import colors_to_icons
from shared.mtg import _bulk_print_lookup, _collector_number_numeric, _name_sort_expr


@dataclass(slots=True)
class FolderDetailCardState:
    deck_rows: list[Card]
    print_map: dict[int, dict[str, Any]]
    image_map: dict[int, str | None]
    color_icons_map: dict[int, list[str]]
    cmc_map: dict[int, float | None]
    cmc_bucket_map: dict[int, str]
    resolved_type_line_map: dict[int, str]
    resolved_rarity_map: dict[int, str]
    folder_tag_category: str | None
    total_value_usd: float


def _price_from_print(print_payload: dict[str, Any], *, is_foil: bool = False) -> float:
    try:
        prices = _prices_for_print(print_payload)
        if is_foil:
            for key in ("usd_foil", "usd", "usd_etched"):
                value = prices.get(key)
                if value:
                    price_val = float(str(value))
                    if price_val > 0:
                        return price_val
        else:
            for key in ("usd", "usd_foil"):
                value = prices.get(key)
                if value:
                    price_val = float(str(value))
                    if price_val > 0:
                        return price_val
    except Exception:
        pass
    return 0.0


def _color_letters(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(v).upper() for v in value]
    else:
        raw = [ch for ch in str(value).upper()]
    return [ch for ch in raw if ch in {"W", "U", "B", "R", "G"}]


def _rarity_rank(rarity: str | None) -> int:
    lowered = (rarity or "").lower()
    if lowered in ("mythic", "mythic rare"):
        return 3
    if lowered == "rare":
        return 2
    if lowered == "uncommon":
        return 1
    if lowered == "common":
        return 0
    return -1


def _collector_number_key(collector_number: Any) -> tuple[int, str]:
    if collector_number is None:
        return (10**9, "")
    text = str(collector_number)
    digits = ""
    for ch in text:
        if ch.isdigit():
            digits += ch
        else:
            break
    return (int(digits) if digits else 10**9, text)


def build_folder_detail_card_state(
    folder: Folder,
    *,
    folder_id: int,
    sort: str,
    reverse: bool,
) -> FolderDetailCardState:
    name_col = _name_sort_expr()
    cn_num = _collector_number_numeric()
    cn_numeric_last = case((cn_num.is_(None), 1), else_=0)
    deck_rows = (
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
                Card.quantity,
                Card.type_line,
                Card.rarity,
                Card.oracle_text,
                Card.mana_value,
                Card.colors,
                Card.color_identity,
                Card.color_identity_mask,
                Card.faces_json,
            )
        )
        .filter(Card.folder_id == folder_id)
        .order_by(
            name_col.asc(),
            Card.set_code.asc(),
            cn_numeric_last.asc(),
            cn_num.asc(),
            Card.collector_number.asc(),
        )
        .all()
    )

    if not sc.cache_ready():
        sc.ensure_cache_loaded()

    image_map: dict[int, str | None] = {}
    color_icons_map: dict[int, list[str]] = {}
    cmc_map: dict[int, float | None] = {}
    cmc_bucket_map: dict[int, str] = {}
    color_letters_map: dict[int, str] = {}
    resolved_type_line_map: dict[int, str] = {}
    resolved_rarity_map: dict[int, str] = {}
    total_value_usd = 0.0
    cache_key = f"folder:{folder.id}" if getattr(folder, "id", None) else None
    print_map = _bulk_print_lookup(deck_rows, cache_key=cache_key, epoch=cache_epoch())

    for card in deck_rows:
        print_payload = print_map.get(card.id, {}) or {}

        if print_payload:
            image_payload = sc.image_for_print(print_payload)
            image_map[card.id] = image_payload.get("small") or image_payload.get("normal")
        else:
            image_map[card.id] = None

        type_line = (getattr(card, "type_line", None) or "").strip() or type_line_from_print_payload(print_payload)
        rarity_val = (getattr(card, "rarity", None) or "").strip().lower() or str(print_payload.get("rarity") or "").strip().lower()
        resolved_type_line_map[card.id] = type_line or ""
        resolved_rarity_map[card.id] = rarity_val or ""

        letters_list = _color_letters(getattr(card, "color_identity", None)) or _color_letters(getattr(card, "colors", None))
        if not letters_list:
            letters_list = _color_letters(print_payload.get("color_identity")) or _color_letters(print_payload.get("colors"))
        if "artifact" in (type_line or "").lower():
            oracle_text = (getattr(card, "oracle_text", None) or "").strip()
            if not oracle_text:
                oracle_text = oracle_text_from_faces_json(getattr(card, "faces_json", None))
            if not oracle_text:
                oracle_text = str(print_payload.get("oracle_text") or "").strip()
            if not oracle_text:
                oracle_text = oracle_text_from_faces_json(print_payload.get("card_faces"))
            produced_colors = artifact_production_colors(oracle_text)
            if produced_colors:
                letters_list = [ch for ch in WUBRG if ch in (set(letters_list) | produced_colors)]

        letters_norm = "".join(ch for ch in WUBRG if ch in set(letters_list)) if letters_list else "C"
        color_letters_map[card.id] = letters_norm
        color_icons_map[card.id] = colors_to_icons(letters_list or ["C"], use_local=True)

        cmc_val = getattr(card, "mana_value", None)
        if cmc_val is None:
            cmc_val = print_payload.get("cmc")
        try:
            cmc_val = float(cmc_val) if cmc_val is not None else None
        except (TypeError, ValueError):
            cmc_val = None
        cmc_map[card.id] = cmc_val

        bucket = ""
        if cmc_val is not None:
            try:
                rounded = int(round(cmc_val))
            except (TypeError, ValueError):
                rounded = None
            if rounded is not None:
                if rounded < 0:
                    rounded = 0
                bucket = str(rounded) if rounded <= 6 else "7+"
        cmc_bucket_map[card.id] = bucket

        qty = getattr(card, "quantity", 1) or 1
        is_foil = bool(getattr(card, "is_foil", False))
        total_value_usd += _price_from_print(print_payload, is_foil=is_foil) * qty

    if sort in {"name", "ctype", "colors", "rar", "set", "cn", "foil", "qty", "cmc"}:
        if sort == "name":
            deck_rows.sort(key=lambda card: (card.name or "").lower(), reverse=reverse)
        elif sort == "ctype":
            deck_rows.sort(key=lambda card: resolved_type_line_map.get(card.id, "").lower(), reverse=reverse)
        elif sort == "colors":
            deck_rows.sort(key=lambda card: color_letters_map.get(card.id) or "C", reverse=reverse)
        elif sort == "rar":
            deck_rows.sort(key=lambda card: _rarity_rank(resolved_rarity_map.get(card.id) or ""), reverse=reverse)
        elif sort == "set":
            deck_rows.sort(key=lambda card: (card.set_code or "").upper(), reverse=reverse)
        elif sort == "cn":
            deck_rows.sort(key=lambda card: _collector_number_key(card.collector_number), reverse=reverse)
        elif sort == "foil":
            deck_rows.sort(key=lambda card: 1 if getattr(card, "is_foil", False) else 0, reverse=reverse)
        elif sort == "qty":
            deck_rows.sort(key=lambda card: getattr(card, "quantity", 1) or 1, reverse=reverse)
        elif sort == "cmc":
            def _cmc_key(card: Card):
                value = cmc_map.get(card.id)
                if value is None:
                    return (1, 0.0)
                return (0, (-value if reverse else value))

            deck_rows.sort(key=_cmc_key)

    return FolderDetailCardState(
        deck_rows=deck_rows,
        print_map=print_map,
        image_map=image_map,
        color_icons_map=color_icons_map,
        cmc_map=cmc_map,
        cmc_bucket_map=cmc_bucket_map,
        resolved_type_line_map=resolved_type_line_map,
        resolved_rarity_map=resolved_rarity_map,
        folder_tag_category=get_deck_tag_category(folder.deck_tag),
        total_value_usd=total_value_usd,
    )


__all__ = ["FolderDetailCardState", "build_folder_detail_card_state"]
