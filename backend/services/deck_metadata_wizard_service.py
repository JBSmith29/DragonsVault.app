"""Service helpers for the deck metadata completion wizard."""

from __future__ import annotations

from typing import Any, Iterable

from flask import url_for

from models import Folder
from services.deck_tags import get_deck_tag_groups


def build_deck_metadata_wizard_payload(
    folders: Iterable[Folder],
    *,
    tag_groups: dict | None = None,
) -> dict[str, Any]:
    decks: list[dict[str, Any]] = []
    for folder in folders:
        if not folder or not folder.is_deck:
            continue
        missing_commander = not (folder.commander_name or folder.commander_oracle_id)
        missing_tag = not bool(folder.deck_tag)
        if not (missing_commander or missing_tag):
            continue
        decks.append(
            {
                "id": folder.id,
                "name": folder.name or "Deck",
                "commander_name": folder.commander_name or "",
                "deck_tag": folder.deck_tag or "",
                "missing_commander": missing_commander,
                "missing_tag": missing_tag,
                "actions": {
                    "commander_candidates_url": url_for(
                        "views.api_folder_commander_candidates",
                        folder_id=folder.id,
                    ),
                    "commander_set_url": url_for("views.set_commander", folder_id=folder.id),
                    "commander_clear_url": url_for("views.clear_commander", folder_id=folder.id),
                    "tag_set_url": url_for("views.set_folder_tag", folder_id=folder.id),
                    "tag_clear_url": url_for("views.clear_folder_tag", folder_id=folder.id),
                },
            }
        )

    return {
        "decks": decks,
        "total": len(decks),
        "tag_groups": tag_groups if tag_groups is not None else get_deck_tag_groups(),
    }
