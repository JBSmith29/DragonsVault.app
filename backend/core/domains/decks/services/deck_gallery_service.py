"""Deck gallery compatibility wrappers."""

from __future__ import annotations

import sys
from typing import Optional

from flask import jsonify, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import load_only, selectinload

from extensions import cache, db
from models import Card, Folder, FolderRole, FolderShare, User, UserFriend
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    cache_epoch,
    cache_ready,
    ensure_cache_loaded,
    find_by_set_cn,
    prints_for_oracle,
    unique_oracle_by_name,
)
from core.domains.decks.services import deck_gallery_drawer_service, deck_gallery_overview_service
from core.domains.cards.viewmodels.card_vm import ImageSetVM
from core.domains.decks.services.commander_brackets import (
    BRACKET_RULESET_EPOCH,
    evaluate_commander_bracket,
    spellbook_dataset_epoch,
)
from core.domains.decks.services.commander_cache import (
    compute_bracket_signature,
    get_cached_bracket,
    store_cached_bracket,
)
from core.domains.decks.services.commander_utils import (
    primary_commander_name,
    primary_commander_oracle_id,
    split_commander_names,
    split_commander_oracle_ids,
)
from core.domains.decks.services import deck_gallery_shared_service as gallery_shared
from core.domains.decks.services.deck_metadata_wizard_service import build_deck_metadata_wizard_payload
from core.domains.decks.services.deck_service import deck_curve_rows, deck_land_mana_sources, deck_mana_pip_dist
from core.domains.decks.services.deck_tags import get_deck_tag_category, get_deck_tag_groups
from core.domains.decks.viewmodels.deck_vm import DeckCommanderVM, DeckOwnerSummaryVM, DeckVM
from core.shared.utils.assets import static_url
from core.shared.utils.symbols_cache import ensure_symbols_cache, render_mana_html
from shared.auth import ensure_folder_access
from shared.cache.runtime_cache import cache_fetch, user_cache_key
from shared.database import get_or_404
from shared.mtg import _lookup_print_data, color_identity_name, compute_folder_color_identity


def _ensure_cache_ready() -> bool:
    return cache_ready() or ensure_cache_loaded()


def _image_pack_from_print(print_obj: dict | None) -> dict[str, str | None]:
    return gallery_shared.image_pack_from_print(print_obj)


def _prefetch_commander_cards(folder_map: dict[int, Folder]) -> dict[int, Card]:
    return gallery_shared.prefetch_commander_cards(folder_map)


def _owner_summary(decks: list[dict]) -> list[dict]:
    return gallery_shared.owner_summary(decks)


def _owner_names(decks: list[dict]) -> list[str]:
    return gallery_shared.owner_names(decks)


@cache.memoize(timeout=600)
def _commander_thumbnail_payload(
    folder_id: int,
    target_oracle_id: Optional[str],
    commander_name: Optional[str],
    row_count: int,
    qty_sum: int,
    epoch: int,
) -> dict[str, Optional[str]]:
    return gallery_shared.commander_thumbnail_payload(
        folder_id,
        target_oracle_id,
        commander_name,
        row_count,
        qty_sum,
        epoch,
    )


def _deck_drawer_summary(folder: Folder) -> dict:
    return deck_gallery_drawer_service.build_deck_drawer_summary(folder, hooks=sys.modules[__name__])


def api_deck_insight(deck_id: int):
    folder = get_or_404(Folder, deck_id)
    ensure_folder_access(folder, write=False, allow_shared=True)
    cache_key = f"deck_drawer:{user_cache_key()}:{folder.id}"
    payload = cache_fetch(cache_key, 60, lambda: _deck_drawer_summary(folder))
    return jsonify(payload)


def decks_overview():
    context = deck_gallery_overview_service.build_decks_overview_context(hooks=sys.modules[__name__])
    return render_template("decks/decks.html", **context)


__all__ = [
    "_commander_thumbnail_payload",
    "_prefetch_commander_cards",
    "api_deck_insight",
    "decks_overview",
]
