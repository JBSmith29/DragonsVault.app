"""Card presentation builders for folder detail rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models import Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.folder_detail_analysis_service import CARD_TYPE_GROUPS, type_group_label
from core.domains.decks.services.folder_detail_card_state_service import FolderDetailCardState
from core.domains.decks.viewmodels.folder_vm import FolderCardVM


@dataclass(slots=True)
class FolderDetailCardPresentation:
    deck_cards: list[FolderCardVM]
    card_groups: list[dict[str, Any]]
    card_image_lookup: dict[int, str]
    curve_missing: int
    curve_rows: list[dict[str, Any]]


def build_folder_detail_card_presentation(
    folder: Folder,
    *,
    state: FolderDetailCardState,
) -> FolderDetailCardPresentation:
    base_types = [token for _label, token in CARD_TYPE_GROUPS]
    deck_cards: list[FolderCardVM] = []
    for card in state.deck_rows:
        type_line = state.resolved_type_line_map.get(card.id) or ""
        type_badges = [token for token in base_types if token in type_line]
        rarity_value = (state.resolved_rarity_map.get(card.id) or "").strip().lower()
        rarity_label = rarity_value.capitalize() if rarity_value else None
        rarity_badge_class = (rarity_label or "").lower() if rarity_label else None
        print_payload = state.print_map.get(card.id, {}) or {}
        image_payload = sc.image_for_print(print_payload) if print_payload else {}
        image_small = image_payload.get("small") or state.image_map.get(card.id)
        image_normal = image_payload.get("normal") or image_small
        image_large = image_payload.get("large") or image_normal
        hover_image = image_large or image_normal or image_small
        cmc_value = state.cmc_map.get(card.id)
        cmc_display = f"{cmc_value:.2f}".rstrip("0").rstrip(".") if cmc_value is not None else "—"
        card_roles: list[str] = []
        data_tags = f"{state.folder_tag_category or ''} {folder.deck_tag or ''} {' '.join(card_roles)}".strip()
        data_roles = " ".join(card_roles)
        deck_cards.append(
            FolderCardVM(
                id=card.id,
                name=card.name,
                display_name=card.name,
                set_code=card.set_code,
                collector_number=str(card.collector_number) if card.collector_number is not None else None,
                lang=card.lang,
                is_foil=bool(getattr(card, "is_foil", False)),
                quantity=int(getattr(card, "quantity", 0) or 0) or 1,
                type_line=type_line,
                type_badges=type_badges,
                color_icons=state.color_icons_map.get(card.id) or [],
                cmc_value=cmc_value,
                cmc_display=cmc_display,
                cmc_bucket=state.cmc_bucket_map.get(card.id) or "",
                rarity_label=rarity_label,
                rarity_badge_class=rarity_badge_class,
                image_small=image_small,
                image_normal=image_normal,
                image_large=image_large,
                hover_image=hover_image,
                data_tags=data_tags,
                data_roles=data_roles,
            )
        )

    group_map: dict[str, list[FolderCardVM]] = {label: [] for label, _token in CARD_TYPE_GROUPS}
    other_cards: list[FolderCardVM] = []
    for card in deck_cards:
        label = type_group_label(card.type_line)
        if label in group_map:
            group_map[label].append(card)
        else:
            other_cards.append(card)

    curve_bins: dict[str, int] = {bucket: 0 for bucket in ["0", "1", "2", "3", "4", "5", "6", "7+"]}
    curve_missing = 0
    for card in deck_cards:
        type_line = (card.type_line or "").lower()
        if "land" in type_line:
            continue
        qty = int(card.quantity or 0) or 1
        cmc_value = card.cmc_value
        if cmc_value is None:
            curve_missing += qty
            continue
        try:
            bucket_val = int(round(float(cmc_value)))
        except (TypeError, ValueError):
            curve_missing += qty
            continue
        if bucket_val < 0:
            bucket_val = 0
        bucket = str(bucket_val) if bucket_val <= 6 else "7+"
        curve_bins[bucket] += qty

    total_curve = sum(curve_bins.values()) or 1
    curve_rows = [
        {
            "label": bucket,
            "count": int(curve_bins.get(bucket) or 0),
            "pct": int(round(100.0 * int(curve_bins.get(bucket) or 0) / total_curve)) if total_curve else 0,
        }
        for bucket in ["0", "1", "2", "3", "4", "5", "6", "7+"]
    ]

    card_groups: list[dict[str, Any]] = []
    for label, _token in CARD_TYPE_GROUPS:
        cards = group_map.get(label, [])
        card_groups.append({"label": label, "cards": cards, "count": len(cards)})
    if other_cards:
        card_groups.append({"label": "Other", "cards": other_cards, "count": len(other_cards)})

    card_image_lookup = {card.id: card.image_small for card in deck_cards if card.image_small}
    return FolderDetailCardPresentation(
        deck_cards=deck_cards,
        card_groups=card_groups,
        card_image_lookup=card_image_lookup,
        curve_missing=curve_missing,
        curve_rows=curve_rows,
    )


__all__ = ["FolderDetailCardPresentation", "build_folder_detail_card_presentation"]
