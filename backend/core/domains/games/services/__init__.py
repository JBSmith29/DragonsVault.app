"""Games domain services."""

from __future__ import annotations

import importlib

__all__ = [
    "game_compat_service",
    "game_import_service",
    "game_metrics_breakdown_service",
    "game_metrics_service",
    "game_metrics_query_service",
    "game_metrics_support_service",
    "game_overview_service",
    "game_players_action_service",
    "game_players_payload_service",
    "game_players_service",
    "game_public_dashboard_service",
    "game_session_form_context_service",
    "game_session_form_mutation_service",
    "game_session_form_parsing_service",
    "game_session_form_service",
    "game_session_shared_service",
    "game_export_support_service",
    "game_service",
    "games_enhanced",
    "stats",
]


def __getattr__(name: str):
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted(__all__)
