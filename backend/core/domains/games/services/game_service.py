"""Commander game route wrapper with compatibility delegation."""

from __future__ import annotations

from . import game_compat_service

_COMPAT_EXPORTS = frozenset(game_compat_service.__all__)


def games_landing():
    from . import game_overview_service

    return game_overview_service.games_landing()


def games_dashboard():
    from . import game_overview_service

    return game_overview_service.games_dashboard()


def games_admin():
    from . import game_overview_service

    return game_overview_service.games_admin()


def games_overview():
    from . import game_overview_service

    return game_overview_service.games_overview()


def games_overview_public():
    from . import game_overview_service

    return game_overview_service.games_overview_public()


def games_manual_deck_update():
    from . import game_overview_service

    return game_overview_service.games_manual_deck_update()


def games_deck_bracket_update():
    from . import game_overview_service

    return game_overview_service.games_deck_bracket_update()


def games_export():
    from . import game_overview_service

    return game_overview_service.games_export()


def games_import():
    from . import game_overview_service

    return game_overview_service.games_import()


def games_import_template():
    from . import game_overview_service

    return game_overview_service.games_import_template()


def games_metrics():
    from . import game_metrics_service

    return game_metrics_service.games_metrics()


def games_metrics_player():
    from . import game_metrics_service

    return game_metrics_service.games_metrics_player()


def games_metrics_pods():
    from . import game_metrics_service

    return game_metrics_service.games_metrics_pods()


def games_metrics_users():
    from . import game_metrics_breakdown_service

    return game_metrics_breakdown_service.games_metrics_users()


def games_metrics_users_public():
    from . import game_metrics_breakdown_service

    return game_metrics_breakdown_service.games_metrics_users_public()


def games_metrics_decks():
    from . import game_metrics_breakdown_service

    return game_metrics_breakdown_service.games_metrics_decks()


def games_metrics_decks_public():
    from . import game_metrics_breakdown_service

    return game_metrics_breakdown_service.games_metrics_decks_public()


def games_metrics_public_dashboard():
    from . import game_metrics_breakdown_service

    return game_metrics_breakdown_service.games_metrics_public_dashboard()


def games_players():
    from . import game_players_service

    return game_players_service.games_players()


def game_detail(game_id: int):
    from . import game_session_form_service

    return game_session_form_service.game_detail(game_id)


def games_new():
    from . import game_session_form_service

    return game_session_form_service.games_new()


def games_edit(game_id: int):
    from . import game_session_form_service

    return game_session_form_service.games_edit(game_id)


def games_delete(game_id: int):
    from . import game_session_form_service

    return game_session_form_service.games_delete(game_id)


def games_bulk_delete():
    from . import game_session_form_service

    return game_session_form_service.games_bulk_delete()


def __getattr__(name: str):
    if name in _COMPAT_EXPORTS:
        return getattr(game_compat_service, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _COMPAT_EXPORTS)


__all__ = [
    "game_detail",
    "games_admin",
    "games_bulk_delete",
    "games_dashboard",
    "games_deck_bracket_update",
    "games_delete",
    "games_edit",
    "games_export",
    "games_import",
    "games_import_template",
    "games_landing",
    "games_manual_deck_update",
    "games_metrics",
    "games_metrics_decks",
    "games_metrics_decks_public",
    "games_metrics_player",
    "games_metrics_pods",
    "games_metrics_public_dashboard",
    "games_metrics_users",
    "games_metrics_users_public",
    "games_new",
    "games_overview",
    "games_overview_public",
    "games_players",
]
