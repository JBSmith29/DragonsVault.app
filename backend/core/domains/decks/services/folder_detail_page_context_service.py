"""Page-context builder for folder detail rendering."""

from __future__ import annotations

from typing import Any

from flask import url_for

from models import Folder
from core.domains.decks.services.deck_tags import (
    get_deck_tag_groups,
)
from core.domains.decks.services.folder_detail_cards_context_service import build_folder_detail_cards_context
from core.domains.decks.services.folder_detail_commander_context_service import build_folder_detail_commander_context
from core.domains.decks.services.folder_detail_folder_shell_service import build_folder_detail_folder_shell


def build_folder_detail_page_context(
    folder: Folder,
    *,
    folder_id: int,
    sort: str,
    reverse: bool,
    bracket_cards: list[dict[str, Any]],
) -> dict[str, Any]:
    cards_context = build_folder_detail_cards_context(
        folder,
        folder_id=folder_id,
        sort=sort,
        reverse=reverse,
    )
    folder_shell = build_folder_detail_folder_shell(folder)

    commander_context = build_folder_detail_commander_context(
        folder,
        deck_cards=cards_context.deck_rows,
        print_map=cards_context.print_map,
        bracket_cards=bracket_cards,
    )

    cards_link = url_for("views.list_cards", folder=folder_id)

    return {
        "card_groups": cards_context.card_groups,
        "card_image_lookup": cards_context.card_image_lookup,
        "cards_link": cards_link,
        "curve_missing": cards_context.curve_missing,
        "curve_rows": cards_context.curve_rows,
        "deck_cards": cards_context.deck_cards,
        "deck_tag_groups": get_deck_tag_groups(),
        "folder": folder_shell.folder,
        "folder_tag_category": cards_context.folder_tag_category,
        "move_targets": folder_shell.move_targets,
        "total_value_usd": cards_context.total_value_usd,
        **commander_context,
    }


__all__ = ["build_folder_detail_page_context"]
