"""Decks domain services."""

from __future__ import annotations

import importlib

__all__ = [
    "build_landing_service",
    "build_session_page_context_service",
    "build_recommendation_service",
    "build_session_mutation_service",
    "build_session_service",
    "commander_bracket_card_service",
    "commander_bracket_reference_service",
    "commander_brackets",
    "commander_assignment_service",
    "commander_bracket_evaluation_service",
    "commander_info_service",
    "commander_spellbook_service",
    "commander_cache",
    "commander_utils",
    "core_role_logic",
    "deck_metadata_wizard_service",
    "deck_gallery_service",
    "deck_gallery_drawer_service",
    "deck_gallery_overview_service",
    "deck_gallery_shared_service",
    "deck_from_collection_service",
    "deck_service",
    "deck_tags",
    "deck_tokens_service",
    "deck_utils",
    "edhrec",
    "edhrec_cache_service",
    "edhrec_cache_refresh_service",
    "edhrec_cache_target_service",
    "edhrec_cache_query_service",
    "edhrec_client",
    "edhrec_recommendation_service",
    "folder_detail_service",
    "folder_detail_analysis_service",
    "folder_detail_card_presentation_service",
    "folder_detail_card_state_service",
    "folder_detail_cards_context_service",
    "folder_detail_commander_context_service",
    "folder_detail_folder_shell_service",
    "folder_detail_page_context_service",
    "folder_sharing_service",
    "folder_metadata_service",
    "folder_service",
    "opening_hand_lookup_service",
    "opening_hand_payload_service",
    "opening_hand_deck_source_service",
    "opening_hand_gameplay_service",
    "opening_hand_service",
    "opening_hand_state_service",
    "proxy_deck_service",
    "oracle_tagging",
    "proxy_decks",
    "send_to_build_service",
    "spellbook_sync",
    "wishlist_display_service",
    "wishlist_mutation_service",
    "wishlist_service",
]


def __getattr__(name: str):
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted(__all__)
