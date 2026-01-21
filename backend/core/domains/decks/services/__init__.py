"""Decks domain services."""

from __future__ import annotations

import importlib

__all__ = [
    "build_landing_service",
    "build_recommendation_service",
    "build_session_service",
    "commander_brackets",
    "commander_cache",
    "commander_utils",
    "core_role_logic",
    "deck_metadata_wizard_service",
    "deck_service",
    "deck_tags",
    "deck_utils",
    "edhrec",
    "edhrec_cache_service",
    "edhrec_client",
    "edhrec_recommendation_service",
    "folder_service",
    "oracle_tagging",
    "proxy_decks",
    "spellbook_sync",
]


def __getattr__(name: str):
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted(__all__)
