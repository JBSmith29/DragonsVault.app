"""Commander-specific folder detail context builders."""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from models import Card, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import cache_epoch, prints_for_oracle, unique_oracle_by_name
from core.domains.decks.services.build_recommendation_service import build_recommendation_sections
from core.domains.decks.services.commander_brackets import (
    BRACKET_RULESET_EPOCH,
    evaluate_commander_bracket,
    spellbook_dataset_epoch,
)
from core.domains.decks.services.commander_cache import compute_bracket_signature, get_cached_bracket, store_cached_bracket
from core.domains.decks.services.commander_info_service import commander_card_snapshot
from core.domains.decks.services.commander_utils import (
    primary_commander_name,
    primary_commander_oracle_id,
    split_commander_oracle_ids,
)
from core.domains.decks.services.deck_tags import resolve_deck_tag_from_slug
from core.domains.decks.services.edhrec_cache_service import cache_ready as edhrec_cache_ready


def _name_variants(name: str) -> Set[str]:
    if not name:
        return set()
    variants: Set[str] = set()
    parts = [name]
    if "//" in name:
        parts.extend([part.strip() for part in name.split("//") if part.strip()])
    for part in parts:
        clean = part.strip()
        if not clean:
            continue
        variants.add(clean.lower())
        core = clean.split("(")[0].strip()
        if core:
            variants.add(core.lower())
    return variants


def build_folder_detail_commander_context(
    folder: Folder,
    *,
    deck_cards: list[Card],
    print_map: dict[int, dict[str, Any]],
    bracket_cards: list[dict[str, Any]],
) -> dict[str, Any]:
    commander_ctx = None
    if not folder.is_collection:
        commander_stub = {
            "oracle_id": primary_commander_oracle_id(folder.commander_oracle_id),
            "name": primary_commander_name(folder.commander_name) or folder.commander_name,
        }
        epoch_val = cache_epoch() + BRACKET_RULESET_EPOCH + spellbook_dataset_epoch()
        signature = compute_bracket_signature(bracket_cards, commander_stub, epoch=epoch_val)
        commander_ctx = get_cached_bracket(folder.id, signature, epoch_val)
        if not commander_ctx:
            commander_ctx = evaluate_commander_bracket(bracket_cards, commander_stub)
            if folder.id:
                store_cached_bracket(folder.id, signature, epoch_val, commander_ctx)

    commander_media: Optional[Dict[str, Any]] = None
    commander_media_list: list[Dict[str, Any]] = []

    def _assign_commander_media(print_obj: Optional[Dict[str, Any]], name_hint: Optional[str] = None):
        nonlocal commander_media
        if not print_obj:
            return
        art_uris = sc.image_for_print(print_obj) or {}
        image_src = art_uris.get("normal") or art_uris.get("small") or art_uris.get("large")
        hover_src = art_uris.get("large") or art_uris.get("normal") or art_uris.get("small")
        if not image_src and not hover_src:
            return
        media = {
            "name": name_hint or print_obj.get("name") or folder.commander_name,
            "image": image_src or hover_src,
            "hover": hover_src or image_src,
            "label": art_uris.get("label") or name_hint or folder.commander_name,
        }
        commander_media_list.append(media)
        if commander_media is None:
            commander_media = media

    commander_oracle_set = {oid.strip().lower() for oid in split_commander_oracle_ids(folder.commander_oracle_id)}
    if commander_oracle_set:
        for card in deck_cards:
            card_oracle = (getattr(card, "oracle_id", "") or "").strip().lower()
            if not card_oracle or card_oracle not in commander_oracle_set:
                continue
            _assign_commander_media(print_map.get(card.id, {}) or {}, card.name)

    if commander_media is None:
        primary_oid = primary_commander_oracle_id(folder.commander_oracle_id)
        if primary_oid:
            try:
                oracle_prints = prints_for_oracle(primary_oid) or []
            except Exception:
                oracle_prints = []
            if oracle_prints:
                _assign_commander_media(oracle_prints[0], folder.commander_name)

    if commander_media is None and folder.commander_name:
        name_hint = primary_commander_name(folder.commander_name) or folder.commander_name
        try:
            snapshot = commander_card_snapshot(name_hint, cache_epoch())
        except Exception:
            snapshot = None
        if snapshot:
            image_src = snapshot.get("thumb") or snapshot.get("hover")
            hover_src = snapshot.get("hover") or snapshot.get("thumb")
            if image_src or hover_src:
                media = {
                    "name": snapshot.get("name") or folder.commander_name,
                    "image": image_src or hover_src,
                    "hover": hover_src or image_src,
                    "label": snapshot.get("set_name") or snapshot.get("set"),
                }
                commander_media_list.append(media)
                if commander_media is None:
                    commander_media = media

    bracket_card_links: Dict[str, int] = {}
    if commander_ctx:
        for card in deck_cards:
            for key in _name_variants(card.name or ""):
                key = key.strip()
                if key:
                    bracket_card_links.setdefault(key, card.id)

    is_deck_folder = bool(folder and not folder.is_collection)
    deck_oracle_ids = {
        (card.oracle_id or "").strip()
        for card in deck_cards
        if getattr(card, "oracle_id", None)
    }
    edhrec_ready = edhrec_cache_ready()
    edhrec_sections: list[dict[str, Any]] = []
    edhrec_tag_label = resolve_deck_tag_from_slug(folder.deck_tag) if folder.deck_tag else None
    edhrec_commander_ready = False
    if is_deck_folder:
        commander_oracle_id = primary_commander_oracle_id(folder.commander_oracle_id)
        if not commander_oracle_id and folder.commander_name:
            try:
                commander_oracle_id = unique_oracle_by_name(primary_commander_name(folder.commander_name) or folder.commander_name) or ""
            except Exception:
                commander_oracle_id = ""
        edhrec_commander_ready = bool(commander_oracle_id)
        if edhrec_ready and edhrec_commander_ready:
            tags = [edhrec_tag_label] if edhrec_tag_label else []
            edhrec_sections = build_recommendation_sections(
                commander_oracle_id,
                tags,
                sort_mode="synergy",
            )
            if deck_oracle_ids and edhrec_sections:
                for section in edhrec_sections:
                    for card in section.get("cards") or []:
                        oracle_id = (card.get("oracle_id") or "").strip()
                        if oracle_id:
                            card["in_deck"] = oracle_id in deck_oracle_ids

    return {
        "bracket_card_links": bracket_card_links,
        "commander_bracket": commander_ctx,
        "commander_media": commander_media,
        "commander_media_list": commander_media_list,
        "edhrec_commander_ready": edhrec_commander_ready,
        "edhrec_ready": edhrec_ready,
        "edhrec_sections": edhrec_sections,
        "edhrec_tag_label": edhrec_tag_label,
        "is_deck_folder": is_deck_folder,
    }


__all__ = ["build_folder_detail_commander_context"]
