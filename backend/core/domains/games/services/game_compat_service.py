"""Legacy compatibility surface for shared games models and helper proxies."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from models import (
    Folder,
    FolderRole,
    GameDeck,
    GamePlayer,
    GamePod,
    GamePodMember,
    GameRosterDeck,
    GameRosterPlayer,
    GameSeat,
    GameSeatAssignment,
    GameSession,
    User,
)
from core.domains.cards.services import scryfall_cache as sc
from shared.validation import ValidationError, log_validation_error, parse_optional_positive_int, parse_positive_int


def _accessible_deck_options(
    owner_user_id: int | None = None,
    *,
    commander_only: bool = False,
) -> list[dict[str, Any]]:
    from . import game_players_service

    return game_players_service._accessible_deck_options(
        owner_user_id,
        commander_only=commander_only,
    )


def accessible_deck_options(
    owner_user_id: int | None = None,
    *,
    commander_only: bool = False,
) -> list[dict[str, Any]]:
    from . import game_players_service

    return game_players_service.accessible_deck_options(
        owner_user_id,
        commander_only=commander_only,
    )


def _roster_players(owner_user_id: int) -> list[dict[str, Any]]:
    from . import game_players_service

    return game_players_service._roster_players(owner_user_id)


def _roster_payloads_for_owner(owner_user_id: int) -> list[dict[str, Any]]:
    from . import game_players_service

    return game_players_service._roster_payloads_for_owner(owner_user_id)


def _pod_access_flags(pod: GamePod, user_id: int) -> tuple[bool, bool]:
    from . import game_players_service

    return game_players_service._pod_access_flags(pod, user_id)


def _pod_payloads_for_owner(owner_user_id: int, roster_players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from . import game_players_service

    return game_players_service._pod_payloads_for_owner(owner_user_id, roster_players)


def _pod_payloads_for_management(
    pods: list[GamePod],
    roster_label_map_by_owner: dict[int, dict[int, str]],
    roster_options_by_owner: dict[int, list[dict[str, Any]]],
    owner_label_map: dict[int, str],
    current_user_id: int,
) -> list[dict[str, Any]]:
    from . import game_players_service

    return game_players_service._pod_payloads_for_management(
        pods,
        roster_label_map_by_owner,
        roster_options_by_owner,
        owner_label_map,
        current_user_id,
    )


def _parse_deck_ref(raw_value: str | None, *, seat_number: int, errors: list[str]) -> tuple[str | None, int | None]:
    from . import game_session_shared_service

    return game_session_shared_service._parse_deck_ref(raw_value, seat_number=seat_number, errors=errors)


def _snapshot_deck(folder: Folder) -> dict[str, Any]:
    from . import game_session_shared_service

    return game_session_shared_service._snapshot_deck(folder)


def _oracle_image(oracle_id: str | None) -> str | None:
    from . import game_session_shared_service

    return game_session_shared_service._oracle_image(oracle_id)


def _oracle_name_from_id(oracle_id: str | None) -> str | None:
    from . import game_session_shared_service

    return game_session_shared_service._oracle_name_from_id(oracle_id)


def _find_deck_by_name(owner_user_id: int, deck_name: str | None) -> Folder | None:
    from . import game_session_shared_service

    return game_session_shared_service._find_deck_by_name(owner_user_id, deck_name)


def _parse_played_at(raw: str | None, errors: list[str]) -> datetime:
    from . import game_session_shared_service

    return game_session_shared_service._parse_played_at(raw, errors)


def _player_label(player: GamePlayer | None) -> str:
    from . import game_session_shared_service

    return game_session_shared_service._player_label(player)


def _game_session_payload(session: GameSession, user_id: int | None = None) -> dict[str, Any]:
    from . import game_session_shared_service

    return game_session_shared_service._game_session_payload(session, user_id)


def _games_summary(user_id: int) -> dict[str, int]:
    from . import game_session_shared_service

    return game_session_shared_service._games_summary(user_id)


def _manual_deck_summary(owner_user_id: int) -> list[dict[str, Any]]:
    from . import game_session_shared_service

    return game_session_shared_service._manual_deck_summary(owner_user_id)


def _available_years(user_id: int, scope: dict[str, Any] | None = None) -> list[int]:
    from . import game_metrics_support_service

    return game_metrics_support_service._available_years(user_id, scope=scope)


def _game_csv_headers_wide(include_game_id: bool = True) -> list[str]:
    from . import game_export_support_service

    return game_export_support_service.game_csv_headers_wide(include_game_id=include_game_id)


def _apply_notes_search(query, q: str):
    from . import game_metrics_support_service

    return game_metrics_support_service._apply_notes_search(query, q)


def _session_visibility_filter(user_id: int):
    from . import game_metrics_support_service

    return game_metrics_support_service._session_visibility_filter(user_id)


def _session_filters(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> list:
    from . import game_metrics_support_service

    return game_metrics_support_service._session_filters(
        user_id,
        start_at=start_at,
        end_at=end_at,
        scope=scope,
    )


def _pod_options_for_user(user_id: int) -> list[dict[str, Any]]:
    from . import game_metrics_support_service

    return game_metrics_support_service._pod_options_for_user(user_id)


def _pod_metrics_scope(user_id: int, pod_id: int | None = None) -> dict[str, Any] | None:
    from . import game_metrics_support_service

    return game_metrics_support_service._pod_metrics_scope(user_id, pod_id=pod_id)


def _canonical_player_identity(
    user_id: int | None,
    display_name: str | None,
    scope: dict[str, Any] | None,
) -> tuple[str, str]:
    from . import game_metrics_support_service

    return game_metrics_support_service._canonical_player_identity(user_id, display_name, scope)


def _seat_counts_subquery(filters: list) -> Any:
    from . import game_metrics_support_service

    return game_metrics_support_service._seat_counts_subquery(filters)


def _parse_date_value(raw: str | None) -> date | None:
    from . import game_metrics_support_service

    return game_metrics_support_service._parse_date_value(raw)


def _resolve_date_range(params) -> dict[str, Any]:
    from . import game_metrics_support_service

    return game_metrics_support_service._resolve_date_range(params)


def _resolve_year_or_all_range(params) -> dict[str, Any]:
    from . import game_metrics_support_service

    return game_metrics_support_service._resolve_year_or_all_range(params)


def _range_query_params(range_ctx: dict[str, Any]) -> dict[str, str]:
    from . import game_metrics_support_service

    return game_metrics_support_service._range_query_params(range_ctx)


def _metrics_cache_key(
    user_id: int,
    range_ctx: dict[str, Any],
    *,
    pod_id: int | None = None,
    player_key: str | None = None,
    deck_key: str | None = None,
    suffix: str = "payload",
) -> str:
    from . import game_metrics_support_service

    return game_metrics_support_service._metrics_cache_key(
        user_id,
        range_ctx,
        pod_id=pod_id,
        player_key=player_key,
        deck_key=deck_key,
        suffix=suffix,
    )


def _metrics_payload(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from . import game_metrics_query_service

    return game_metrics_query_service._metrics_payload(
        user_id,
        start_at=start_at,
        end_at=end_at,
        scope=scope,
    )


METRICS_GAMES_LIMIT = 200
POD_METRICS_GAMES_LIMIT = 10


def _metrics_games(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._metrics_games(
        user_id,
        start_at=start_at,
        end_at=end_at,
        scope=scope,
        limit=limit,
    )


def _player_key_filter(player_key: str | None, scope: dict[str, Any] | None = None):
    from . import game_metrics_support_service

    return game_metrics_support_service._player_key_filter(player_key, scope=scope)


def _deck_key_filter(deck_key: str | None):
    from . import game_metrics_support_service

    return game_metrics_support_service._deck_key_filter(deck_key)


def _session_filter_for_player(player_key: str | None, scope: dict[str, Any] | None = None):
    from . import game_metrics_support_service

    return game_metrics_support_service._session_filter_for_player(player_key, scope=scope)


def _session_filter_for_deck(deck_key: str | None):
    from . import game_metrics_support_service

    return game_metrics_support_service._session_filter_for_deck(deck_key)


def _merge_scope_filters(
    scope: dict[str, Any] | None,
    extra_filters: list[Any] | None = None,
) -> dict[str, Any] | None:
    from . import game_metrics_support_service

    return game_metrics_support_service._merge_scope_filters(scope, extra_filters=extra_filters)


def _seat_count_breakdown(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_support_service

    return game_metrics_support_service._seat_count_breakdown(
        user_id,
        start_at=start_at,
        end_at=end_at,
        scope=scope,
    )


def _player_label_expr():
    from . import game_metrics_support_service

    return game_metrics_support_service._player_label_expr()


def _top_players_by_plays(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 5,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._top_players_by_plays(
        user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        scope=scope,
    )


def _combo_winners(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 5,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._combo_winners(
        user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        scope=scope,
    )


def _deck_usage(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 5,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._deck_usage(
        user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        player_key=player_key,
        scope=scope,
    )


def _commander_usage(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 5,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._commander_usage(
        user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        player_key=player_key,
        scope=scope,
    )


def _commander_win_rates(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 6,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._commander_win_rates(
        user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        player_key=player_key,
        scope=scope,
    )


def _bracket_stats(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 6,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._bracket_stats(
        user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        player_key=player_key,
        scope=scope,
    )


def _turn_order_metrics(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._turn_order_metrics(
        user_id,
        start_at=start_at,
        end_at=end_at,
        scope=scope,
    )


def _player_options(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._player_options(
        user_id,
        start_at=start_at,
        end_at=end_at,
        scope=scope,
    )


def _deck_options(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._deck_options(
        user_id,
        start_at=start_at,
        end_at=end_at,
        player_key=player_key,
        scope=scope,
    )


def _player_stats(
    user_id: int,
    player_key: str | None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from . import game_metrics_query_service

    return game_metrics_query_service._player_stats(
        user_id,
        player_key,
        start_at=start_at,
        end_at=end_at,
        scope=scope,
    )


def _player_deck_stats(
    user_id: int,
    player_key: str | None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._player_deck_stats(
        user_id,
        player_key,
        start_at=start_at,
        end_at=end_at,
        scope=scope,
    )


def _player_win_rates(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 6,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._player_win_rates(
        user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        scope=scope,
    )


def _deck_win_rates(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int | None = 6,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from . import game_metrics_query_service

    return game_metrics_query_service._deck_win_rates(
        user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        player_key=player_key,
        scope=scope,
    )


def _resolve_public_dashboard_owner_user_id() -> int | None:
    from . import game_public_dashboard_service

    return game_public_dashboard_service.resolve_public_dashboard_owner_user_id()


__all__ = [
    "Folder",
    "FolderRole",
    "GameDeck",
    "GamePlayer",
    "GamePod",
    "GamePodMember",
    "GameRosterDeck",
    "GameRosterPlayer",
    "GameSeat",
    "GameSeatAssignment",
    "GameSession",
    "METRICS_GAMES_LIMIT",
    "POD_METRICS_GAMES_LIMIT",
    "User",
    "ValidationError",
    "_accessible_deck_options",
    "_apply_notes_search",
    "_available_years",
    "_bracket_stats",
    "_canonical_player_identity",
    "_combo_winners",
    "_commander_usage",
    "_commander_win_rates",
    "_deck_key_filter",
    "_deck_options",
    "_deck_usage",
    "_deck_win_rates",
    "_find_deck_by_name",
    "_game_csv_headers_wide",
    "_game_session_payload",
    "_games_summary",
    "_manual_deck_summary",
    "_merge_scope_filters",
    "_metrics_cache_key",
    "_metrics_games",
    "_metrics_payload",
    "_oracle_image",
    "_oracle_name_from_id",
    "_parse_date_value",
    "_parse_deck_ref",
    "_parse_played_at",
    "_player_deck_stats",
    "_player_key_filter",
    "_player_label",
    "_player_label_expr",
    "_player_options",
    "_player_stats",
    "_player_win_rates",
    "_pod_access_flags",
    "_pod_metrics_scope",
    "_pod_options_for_user",
    "_pod_payloads_for_management",
    "_pod_payloads_for_owner",
    "_range_query_params",
    "_resolve_date_range",
    "_resolve_public_dashboard_owner_user_id",
    "_resolve_year_or_all_range",
    "_roster_payloads_for_owner",
    "_roster_players",
    "_seat_count_breakdown",
    "_seat_counts_subquery",
    "_session_filter_for_deck",
    "_session_filter_for_player",
    "_session_filters",
    "_session_visibility_filter",
    "_snapshot_deck",
    "_top_players_by_plays",
    "_turn_order_metrics",
    "accessible_deck_options",
    "log_validation_error",
    "parse_optional_positive_int",
    "parse_positive_int",
    "sc",
]
