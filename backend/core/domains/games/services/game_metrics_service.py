"""Games metrics overview, player, and pod renderers."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import text

from shared.validation import ValidationError, log_validation_error, parse_positive_int

from . import game_metrics_query_service as metrics_query
from . import game_metrics_support_service as metrics_support

__all__ = [
    "games_metrics",
    "games_metrics_decks",
    "games_metrics_decks_public",
    "games_metrics_player",
    "games_metrics_pods",
    "games_metrics_public_dashboard",
    "games_metrics_users",
    "games_metrics_users_public",
]


def games_metrics():
    range_ctx = metrics_support._resolve_date_range(request.args)
    pod_options = metrics_support._pod_options_for_user(current_user.id)
    pod_lookup = {option["id"]: option for option in pod_options}
    pod_raw = (request.args.get("pod") or "").strip()
    selected_pod_id = None
    selected_pod_label = "All pods"
    if pod_raw:
        try:
            selected_pod_id = parse_positive_int(pod_raw, field="pod")
        except ValidationError as exc:
            log_validation_error(exc, context="metrics_pod_filter")
            selected_pod_id = None
        if selected_pod_id not in pod_lookup:
            selected_pod_id = None
    if selected_pod_id:
        selected_pod_label = pod_lookup[selected_pod_id]["label"]

    base_scope = metrics_support._pod_metrics_scope(current_user.id, selected_pod_id) if selected_pod_id else None
    if selected_pod_id and base_scope is None:
        base_scope = {"session_filter": text("1 = 0")}

    player_key = (request.args.get("player") or "").strip()
    deck_key = (request.args.get("deck") or "").strip()
    player_session_filter = metrics_support._session_filter_for_player(player_key, scope=base_scope)
    deck_session_filter = metrics_support._session_filter_for_deck(deck_key)
    metrics_scope = metrics_support._merge_scope_filters(base_scope, [player_session_filter, deck_session_filter])
    player_scope = metrics_support._merge_scope_filters(base_scope, [deck_session_filter])
    deck_scope = metrics_support._merge_scope_filters(base_scope, [player_session_filter])

    from extensions import cache

    metrics_cache_key = metrics_support._metrics_cache_key(
        current_user.id,
        range_ctx,
        pod_id=selected_pod_id,
        player_key=player_key,
        deck_key=deck_key,
    )
    metrics = cache.get(metrics_cache_key)
    if metrics is None:
        metrics = metrics_query._metrics_payload(
            current_user.id,
            range_ctx["start_at"],
            range_ctx["end_at"],
            scope=metrics_scope,
        )
        cache.set(metrics_cache_key, metrics, timeout=300)
    year_options = metrics_support._available_years(current_user.id, scope=base_scope)
    range_params = dict(metrics_support._range_query_params(range_ctx))
    if selected_pod_id:
        range_params["pod"] = str(selected_pod_id)

    player_options = metrics_query._player_options(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=player_scope,
    )
    selected_player_label = ""
    for option in player_options:
        if option["key"] == player_key:
            selected_player_label = option["label"]
            break

    deck_options = metrics_query._deck_options(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=deck_scope,
    )
    selected_deck_label = ""
    for option in deck_options:
        if option["key"] == deck_key:
            selected_deck_label = option["label"]
            break

    player_metrics = metrics_query._player_stats(
        current_user.id,
        player_key,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
    )
    player_filter_active = bool(player_key)
    deck_usage = metrics_query._deck_usage(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key if player_filter_active else None,
        scope=metrics_scope,
    )
    top_players = metrics_query._top_players_by_plays(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
    )
    combo_winners = metrics_query._combo_winners(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
    )
    turn_order_metrics = metrics_query._turn_order_metrics(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
    )
    player_win_rates = metrics_query._player_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
    )
    deck_win_rates = metrics_query._deck_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key if player_filter_active else None,
        scope=metrics_scope,
    )
    bracket_stats = metrics_query._bracket_stats(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key if player_filter_active else None,
        scope=metrics_scope,
    )
    games = metrics_query._metrics_games(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
        limit=metrics_query.METRICS_GAMES_LIMIT,
    )
    return render_template(
        "games/metrics.html",
        metrics=metrics,
        range_ctx=range_ctx,
        year_options=year_options,
        range_params=range_params,
        pod_options=pod_options,
        selected_pod_id=selected_pod_id,
        selected_pod_label=selected_pod_label,
        player_options=player_options,
        selected_player_key=player_key,
        selected_player_label=selected_player_label,
        deck_options=deck_options,
        selected_deck_key=deck_key,
        selected_deck_label=selected_deck_label,
        player_metrics=player_metrics,
        player_filter_active=player_filter_active,
        deck_usage=deck_usage,
        top_players=top_players,
        combo_winners=combo_winners,
        turn_order_metrics=turn_order_metrics,
        player_win_rates=player_win_rates,
        deck_win_rates=deck_win_rates,
        bracket_stats=bracket_stats,
        games=games,
    )


def games_metrics_player():
    range_ctx = metrics_support._resolve_date_range(request.args)
    pod_options = metrics_support._pod_options_for_user(current_user.id)
    pod_lookup = {option["id"]: option for option in pod_options}
    pod_raw = (request.args.get("pod") or "").strip()
    selected_pod_id = None
    if pod_raw:
        try:
            selected_pod_id = parse_positive_int(pod_raw, field="pod")
        except ValidationError as exc:
            log_validation_error(exc, context="metrics_pod_filter")
            selected_pod_id = None
        if selected_pod_id not in pod_lookup:
            selected_pod_id = None
    scope = metrics_support._pod_metrics_scope(current_user.id, selected_pod_id) if selected_pod_id else None
    if selected_pod_id and scope is None:
        scope = {"session_filter": text("1 = 0")}
    player_key = (request.args.get("player") or "").strip()
    if not player_key:
        flash("Select a player to view detailed metrics.", "warning")
        return redirect(url_for("views.games_metrics"))

    player_options = metrics_query._player_options(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=scope,
    )
    player_label = ""
    for option in player_options:
        if option["key"] == player_key:
            player_label = option["label"]
            break
    if not player_label:
        player_label = "Selected player"

    player_metrics = metrics_query._player_stats(
        current_user.id,
        player_key,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=scope,
    )
    deck_stats = metrics_query._player_deck_stats(
        current_user.id,
        player_key,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=scope,
    )
    commander_win_rates = metrics_query._commander_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key,
        scope=scope,
    )
    deck_win_rates = metrics_query._deck_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key,
        scope=scope,
        limit=9999,
    )
    range_params = dict(metrics_support._range_query_params(range_ctx))
    if selected_pod_id:
        range_params["pod"] = str(selected_pod_id)

    return render_template(
        "games/metrics_player.html",
        range_ctx=range_ctx,
        year_options=metrics_support._available_years(current_user.id, scope=scope),
        range_params=range_params,
        player_key=player_key,
        player_label=player_label,
        player_metrics=player_metrics,
        deck_stats=deck_stats,
        deck_win_rates=deck_win_rates,
        commander_win_rates=commander_win_rates,
    )


def games_metrics_pods():
    """Pod-focused metrics page."""
    range_ctx = metrics_support._resolve_date_range(request.args)
    pod_options = metrics_support._pod_options_for_user(current_user.id)
    pod_lookup = {option["id"]: option for option in pod_options}
    pod_raw = (request.args.get("pod") or "").strip()
    selected_pod_id = None
    selected_pod_label = "All pods"
    if pod_raw:
        try:
            selected_pod_id = parse_positive_int(pod_raw, field="pod")
        except ValidationError as exc:
            log_validation_error(exc, context="metrics_pod_filter")
            selected_pod_id = None
        if selected_pod_id not in pod_lookup:
            selected_pod_id = None
    if selected_pod_id:
        selected_pod_label = pod_lookup[selected_pod_id]["label"]

    base_scope = metrics_support._pod_metrics_scope(current_user.id, selected_pod_id) if selected_pod_id else None
    if selected_pod_id and base_scope is None:
        base_scope = {"session_filter": text("1 = 0")}

    from extensions import cache

    metrics_cache_key = metrics_support._metrics_cache_key(
        current_user.id,
        range_ctx,
        pod_id=selected_pod_id,
    )
    metrics = cache.get(metrics_cache_key)
    if metrics is None:
        metrics = metrics_query._metrics_payload(
            current_user.id,
            range_ctx["start_at"],
            range_ctx["end_at"],
            scope=base_scope,
        )
        cache.set(metrics_cache_key, metrics, timeout=300)
    year_options = metrics_support._available_years(current_user.id, scope=base_scope)
    range_params = dict(metrics_support._range_query_params(range_ctx))
    if selected_pod_id:
        range_params["pod"] = str(selected_pod_id)

    pod_breakdown = []
    if not selected_pod_id:
        cache_ttl = 300
        range_key = range_ctx.get("range_key") or ""
        start_value = range_ctx.get("start_value") or ""
        end_value = range_ctx.get("end_value") or ""
        for pod in pod_options:
            cache_key = f"pod_metrics_{current_user.id}_{pod['id']}_{range_key}_{start_value}_{end_value}"
            pod_metrics = cache.get(cache_key)
            if pod_metrics is None:
                pod_scope = metrics_support._pod_metrics_scope(current_user.id, pod["id"])
                if not pod_scope:
                    continue
                pod_metrics = metrics_query._metrics_payload(
                    current_user.id,
                    range_ctx["start_at"],
                    range_ctx["end_at"],
                    scope=pod_scope,
                )
                cache.set(cache_key, pod_metrics, timeout=cache_ttl)
            pod_breakdown.append(
                {
                    "id": pod["id"],
                    "label": pod["label"],
                    "metrics": pod_metrics,
                }
            )

    games = metrics_query._metrics_games(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=base_scope,
        limit=metrics_query.POD_METRICS_GAMES_LIMIT,
    )

    return render_template(
        "games/metrics_pods.html",
        metrics=metrics,
        range_ctx=range_ctx,
        year_options=year_options,
        range_params=range_params,
        pod_options=pod_options,
        selected_pod_id=selected_pod_id,
        selected_pod_label=selected_pod_label,
        pod_breakdown=pod_breakdown,
        games=games,
    )


def _render_games_metrics_users(
    owner_user_id: int,
    *,
    metrics_action_endpoint: str,
    player_filter_endpoint: str,
    player_detail_endpoint: str | None,
    show_related_metric_nav: bool,
    is_public_dashboard: bool,
):
    from . import game_metrics_breakdown_service as metrics_breakdown

    return metrics_breakdown._render_games_metrics_users(
        owner_user_id,
        metrics_action_endpoint=metrics_action_endpoint,
        player_filter_endpoint=player_filter_endpoint,
        player_detail_endpoint=player_detail_endpoint,
        show_related_metric_nav=show_related_metric_nav,
        is_public_dashboard=is_public_dashboard,
    )


def games_metrics_users():
    from . import game_metrics_breakdown_service as metrics_breakdown

    return metrics_breakdown.games_metrics_users()


def games_metrics_users_public():
    from . import game_metrics_breakdown_service as metrics_breakdown

    return metrics_breakdown.games_metrics_users_public()


def _render_games_metrics_decks(
    owner_user_id: int,
    *,
    metrics_action_endpoint: str,
    metrics_metric_value: str | None,
    show_related_metric_nav: bool,
    is_public_dashboard: bool,
):
    from . import game_metrics_breakdown_service as metrics_breakdown

    return metrics_breakdown._render_games_metrics_decks(
        owner_user_id,
        metrics_action_endpoint=metrics_action_endpoint,
        metrics_metric_value=metrics_metric_value,
        show_related_metric_nav=show_related_metric_nav,
        is_public_dashboard=is_public_dashboard,
    )


def games_metrics_decks():
    from . import game_metrics_breakdown_service as metrics_breakdown

    return metrics_breakdown.games_metrics_decks()


def games_metrics_decks_public():
    from . import game_metrics_breakdown_service as metrics_breakdown

    return metrics_breakdown.games_metrics_decks_public()


def games_metrics_public_dashboard():
    from . import game_metrics_breakdown_service as metrics_breakdown

    return metrics_breakdown.games_metrics_public_dashboard()
