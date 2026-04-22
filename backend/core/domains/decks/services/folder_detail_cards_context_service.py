"""Card-list context builders for folder detail rendering."""

from __future__ import annotations

from dataclasses import dataclass

from models import Card, Folder
from core.domains.decks.services.folder_detail_card_presentation_service import build_folder_detail_card_presentation
from core.domains.decks.services.folder_detail_card_state_service import build_folder_detail_card_state


@dataclass(slots=True)
class FolderDetailCardsContext:
    deck_rows: list[Card]
    print_map: dict[int, dict[str, object]]
    deck_cards: list[object]
    card_groups: list[dict[str, object]]
    card_image_lookup: dict[int, str]
    curve_missing: int
    curve_rows: list[dict[str, object]]
    folder_tag_category: str | None
    total_value_usd: float


def build_folder_detail_cards_context(
    folder: Folder,
    *,
    folder_id: int,
    sort: str,
    reverse: bool,
) -> FolderDetailCardsContext:
    state = build_folder_detail_card_state(
        folder,
        folder_id=folder_id,
        sort=sort,
        reverse=reverse,
    )
    presentation = build_folder_detail_card_presentation(folder, state=state)

    return FolderDetailCardsContext(
        deck_rows=state.deck_rows,
        print_map=state.print_map,
        deck_cards=presentation.deck_cards,
        card_groups=presentation.card_groups,
        card_image_lookup=presentation.card_image_lookup,
        curve_missing=presentation.curve_missing,
        curve_rows=presentation.curve_rows,
        folder_tag_category=state.folder_tag_category,
        total_value_usd=state.total_value_usd,
    )


__all__ = ["FolderDetailCardsContext", "build_folder_detail_cards_context"]
