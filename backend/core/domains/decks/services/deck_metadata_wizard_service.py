"""Service helpers for the deck metadata completion wizard."""

from __future__ import annotations

from typing import Any, Iterable

from flask import url_for

from models import Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.commander_utils import primary_commander_oracle_id
from core.domains.decks.services.deck_tags import get_deck_tag_groups


def build_deck_metadata_wizard_payload(
    folders: Iterable[Folder],
    *,
    tag_groups: dict | None = None,
) -> dict[str, Any]:
    cache_ready = False
    try:
        cache_ready = bool(sc.ensure_cache_loaded())
    except Exception:
        cache_ready = False

    def _resolve_commander_name(folder: Folder) -> str:
        name = (folder.commander_name or "").strip()
        if name:
            return name
        if not cache_ready:
            return ""
        oracle_id = primary_commander_oracle_id(folder.commander_oracle_id)
        if not oracle_id:
            return ""
        try:
            prints = sc.prints_for_oracle(oracle_id) or []
        except Exception:
            return ""
        if prints:
            return (prints[0].get("name") or "").strip()
        return ""

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
                "commander_name": _resolve_commander_name(folder),
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
