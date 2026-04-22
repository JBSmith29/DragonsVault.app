"""User and deck breakdown metrics renderers for private and public dashboards."""

from __future__ import annotations

from typing import Any

from flask import render_template, request
from flask_login import current_user

from . import game_metrics_query_service as metrics_query
from . import game_metrics_support_service as metrics_support
from . import game_overview_service
from . import game_public_dashboard_service


def _empty_metrics_summary() -> dict[str, Any]:
    return {
        "total_games": 0,
        "combo_wins": 0,
        "combo_rate": 0,
        "avg_players": None,
        "unique_players": 0,
        "avg_bracket_score": None,
        "top_winners": [],
        "top_decks": [],
        "top_commanders": [],
    }


def _render_games_metrics_users(
    owner_user_id: int,
    *,
    metrics_action_endpoint: str,
    player_filter_endpoint: str,
    player_detail_endpoint: str | None,
    show_related_metric_nav: bool,
    is_public_dashboard: bool,
):
    range_ctx = metrics_support._resolve_year_or_all_range(request.args)
    player_key = (request.args.get("player") or "").strip()

    player_options = metrics_query._player_options(
        owner_user_id,
        range_ctx["start_at"],
        range_ctx["end_at"],
    )
    selected_player_label = "All players"
    if player_key:
        selected_player_label = "Selected player"
        for option in player_options:
            if option["key"] == player_key:
                selected_player_label = option["label"]
                break
        else:
            player_key = ""
            selected_player_label = "All players"

    player_session_filter = metrics_support._session_filter_for_player(player_key)
    metrics_scope = metrics_support._merge_scope_filters(None, [player_session_filter])

    from extensions import cache

    metrics_cache_key = metrics_support._metrics_cache_key(
        owner_user_id,
        range_ctx,
        player_key=player_key,
    )
    metrics = cache.get(metrics_cache_key)
    if metrics is None:
        metrics = metrics_query._metrics_payload(
            owner_user_id,
            range_ctx["start_at"],
            range_ctx["end_at"],
            scope=metrics_scope,
        )
        cache.set(metrics_cache_key, metrics, timeout=300)

    year_options = metrics_support._available_years(owner_user_id)
    range_params = dict(metrics_support._range_query_params(range_ctx))

    top_players = metrics_query._top_players_by_plays(
        owner_user_id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=metrics_scope,
    )
    player_win_rates = metrics_query._player_win_rates(
        owner_user_id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=metrics_scope,
    )
    combo_winners = metrics_query._combo_winners(
        owner_user_id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=metrics_scope,
    )

    return render_template(
        "games/metrics_users.html",
        metrics=metrics,
        range_ctx=range_ctx,
        year_options=year_options,
        range_params=range_params,
        player_options=player_options,
        selected_player_key=player_key,
        selected_player_label=selected_player_label,
        top_players=top_players,
        player_win_rates=player_win_rates,
        combo_winners=combo_winners,
        metrics_action_endpoint=metrics_action_endpoint,
        player_filter_endpoint=player_filter_endpoint,
        player_detail_endpoint=player_detail_endpoint,
        show_related_metric_nav=show_related_metric_nav,
        is_public_dashboard=is_public_dashboard,
    )


def games_metrics_users():
    return _render_games_metrics_users(
        current_user.id,
        metrics_action_endpoint="views.games_metrics_users",
        player_filter_endpoint="views.games_metrics_users",
        player_detail_endpoint="views.games_metrics_player",
        show_related_metric_nav=True,
        is_public_dashboard=False,
    )


def games_metrics_users_public():
    owner_user_id = game_public_dashboard_service.resolve_public_dashboard_owner_user_id()
    if owner_user_id is None:
        range_ctx = metrics_support._resolve_year_or_all_range(request.args)
        return render_template(
            "games/metrics_users.html",
            metrics=_empty_metrics_summary(),
            range_ctx=range_ctx,
            year_options=[],
            range_params=metrics_support._range_query_params(range_ctx),
            player_options=[],
            selected_player_key="",
            selected_player_label="All players",
            top_players=[],
            player_win_rates=[],
            combo_winners=[],
            metrics_action_endpoint="views.gamedashboard",
            player_filter_endpoint="views.gamedashboard",
            player_detail_endpoint=None,
            show_related_metric_nav=False,
            is_public_dashboard=True,
        )

    return _render_games_metrics_users(
        owner_user_id,
        metrics_action_endpoint="views.gamedashboard",
        player_filter_endpoint="views.gamedashboard",
        player_detail_endpoint=None,
        show_related_metric_nav=False,
        is_public_dashboard=True,
    )


def _render_games_metrics_decks(
    owner_user_id: int,
    *,
    metrics_action_endpoint: str,
    metrics_metric_value: str | None,
    show_related_metric_nav: bool,
    is_public_dashboard: bool,
):
    range_ctx = metrics_support._resolve_year_or_all_range(request.args)
    player_key = (request.args.get("player") or "").strip()

    player_options = metrics_query._player_options(
        owner_user_id,
        range_ctx["start_at"],
        range_ctx["end_at"],
    )
    selected_player_label = "All players"
    if player_key:
        selected_player_label = "Selected player"
        for option in player_options:
            if option["key"] == player_key:
                selected_player_label = option["label"]
                break
        else:
            player_key = ""
            selected_player_label = "All players"

    player_session_filter = metrics_support._session_filter_for_player(player_key)
    metrics_scope = metrics_support._merge_scope_filters(None, [player_session_filter])

    from extensions import cache

    metrics_cache_key = metrics_support._metrics_cache_key(
        owner_user_id,
        range_ctx,
        player_key=player_key,
    )
    metrics = cache.get(metrics_cache_key)
    if metrics is None:
        metrics = metrics_query._metrics_payload(
            owner_user_id,
            range_ctx["start_at"],
            range_ctx["end_at"],
            scope=metrics_scope,
        )
        cache.set(metrics_cache_key, metrics, timeout=300)

    year_options = metrics_support._available_years(owner_user_id)
    range_params = dict(metrics_support._range_query_params(range_ctx))
    if player_key:
        range_params["player"] = player_key

    player_filter_active = bool(player_key)

    deck_options = metrics_query._deck_options(
        owner_user_id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key if player_filter_active else None,
    )
    deck_usage = metrics_query._deck_usage(
        owner_user_id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        player_key=player_key if player_filter_active else None,
    )
    deck_win_rates = metrics_query._deck_win_rates(
        owner_user_id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=None,
        player_key=player_key if player_filter_active else None,
    )
    bracket_stats = metrics_query._bracket_stats(
        owner_user_id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        player_key=player_key if player_filter_active else None,
    )

    return render_template(
        "games/metrics_decks.html",
        metrics=metrics,
        range_ctx=range_ctx,
        year_options=year_options,
        range_params=range_params,
        player_options=player_options,
        selected_player_key=player_key,
        selected_player_label=selected_player_label,
        deck_options=deck_options,
        deck_usage=deck_usage,
        deck_win_rates=deck_win_rates,
        bracket_stats=bracket_stats,
        metrics_action_endpoint=metrics_action_endpoint,
        metrics_metric_value=metrics_metric_value,
        show_related_metric_nav=show_related_metric_nav,
        is_public_dashboard=is_public_dashboard,
    )


def games_metrics_decks():
    return _render_games_metrics_decks(
        current_user.id,
        metrics_action_endpoint="views.games_metrics_decks",
        metrics_metric_value=None,
        show_related_metric_nav=True,
        is_public_dashboard=False,
    )


def games_metrics_decks_public():
    owner_user_id = game_public_dashboard_service.resolve_public_dashboard_owner_user_id()
    if owner_user_id is None:
        range_ctx = metrics_support._resolve_year_or_all_range(request.args)
        return render_template(
            "games/metrics_decks.html",
            metrics=_empty_metrics_summary(),
            range_ctx=range_ctx,
            year_options=[],
            range_params=metrics_support._range_query_params(range_ctx),
            player_options=[],
            selected_player_key="",
            selected_player_label="All players",
            deck_options=[],
            deck_usage=[],
            deck_win_rates=[],
            bracket_stats=[],
            metrics_action_endpoint="views.gamedashboard",
            metrics_metric_value="decks",
            show_related_metric_nav=True,
            is_public_dashboard=True,
        )

    return _render_games_metrics_decks(
        owner_user_id,
        metrics_action_endpoint="views.gamedashboard",
        metrics_metric_value="decks",
        show_related_metric_nav=True,
        is_public_dashboard=True,
    )


def games_metrics_public_dashboard():
    metric = (request.args.get("metric") or "users").strip().lower()
    if metric == "decks":
        return games_metrics_decks_public()
    if metric == "logs":
        return game_overview_service.games_overview_public()
    return games_metrics_users_public()
