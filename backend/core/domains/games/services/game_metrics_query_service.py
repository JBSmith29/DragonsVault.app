"""Games metrics query and aggregation helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, func
from sqlalchemy.orm import selectinload

from extensions import db
from models import GameDeck, GamePlayer, GameSeat, GameSeatAssignment, GameSession

from . import game_metrics_support_service as support
from . import game_session_shared_service as session_shared

__all__ = [
    "METRICS_GAMES_LIMIT",
    "POD_METRICS_GAMES_LIMIT",
    "_bracket_stats",
    "_combo_winners",
    "_commander_usage",
    "_commander_win_rates",
    "_deck_options",
    "_deck_usage",
    "_deck_win_rates",
    "_metrics_games",
    "_metrics_payload",
    "_player_deck_stats",
    "_player_options",
    "_player_stats",
    "_player_win_rates",
    "_top_players_by_plays",
    "_turn_order_metrics",
]


def _metrics_payload(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)

    total_games = db.session.query(func.count(GameSession.id)).filter(*filters).scalar() or 0
    combo_wins = (
        db.session.query(func.count(GameSession.id))
        .filter(*filters, GameSession.win_via_combo.is_(True))
        .scalar()
        or 0
    )
    combo_rate = round((combo_wins / total_games) * 100, 1) if total_games else 0

    seat_counts = support._seat_counts_subquery(filters)
    avg_players = db.session.query(func.avg(seat_counts.c.seat_count)).scalar()

    player_rows = (
        db.session.query(
            GamePlayer.user_id.label("user_id"),
            GamePlayer.display_name.label("display_name"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.player_id == GamePlayer.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters)
        .distinct()
        .all()
    )
    unique_keys = {
        support._canonical_player_identity(row.user_id, row.display_name, scope)[0]
        for row in player_rows
    }
    unique_players = len(unique_keys)

    winners = (
        db.session.query(
            GamePlayer.user_id.label("user_id"),
            GamePlayer.display_name.label("display_name"),
            func.count(func.distinct(GameSession.id)).label("wins"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.player_id == GamePlayer.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.winner_seat_id == GameSeat.id)
        .filter(*filters)
        .group_by(GamePlayer.user_id, GamePlayer.display_name)
        .all()
    )
    top_winners: list[dict[str, Any]] = []
    if winners:
        merged: dict[str, dict[str, Any]] = {}
        for row in winners:
            key, label = support._canonical_player_identity(row.user_id, row.display_name, scope)
            entry = merged.setdefault(key, {"label": label, "count": 0})
            entry["count"] += int(row.wins or 0)
        top_winners = sorted(
            merged.values(),
            key=lambda item: (-item["count"], (item["label"] or "").lower()),
        )[:5]

    top_decks = (
        db.session.query(
            GameDeck.deck_name,
            func.count(GameSeatAssignment.id).label("plays"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters)
        .group_by(GameDeck.deck_name)
        .order_by(func.count(GameSeatAssignment.id).desc(), GameDeck.deck_name.asc())
        .limit(5)
        .all()
    )

    top_commanders = (
        db.session.query(
            GameDeck.commander_name,
            func.count(GameSeatAssignment.id).label("plays"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters, GameDeck.commander_name.isnot(None))
        .group_by(GameDeck.commander_name)
        .order_by(func.count(GameSeatAssignment.id).desc(), GameDeck.commander_name.asc())
        .limit(5)
        .all()
    )

    avg_bracket_score = (
        db.session.query(func.avg(GameDeck.bracket_score))
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters, GameDeck.bracket_score.isnot(None))
        .scalar()
    )

    return {
        "total_games": int(total_games or 0),
        "combo_wins": int(combo_wins or 0),
        "combo_rate": combo_rate,
        "avg_players": round(float(avg_players), 2) if avg_players else None,
        "unique_players": int(unique_players or 0),
        "avg_bracket_score": round(float(avg_bracket_score), 2) if avg_bracket_score else None,
        "top_winners": top_winners,
        "top_decks": [{"label": row[0] or "Unknown deck", "count": int(row[1] or 0)} for row in top_decks],
        "top_commanders": [{"label": row[0] or "Unknown", "count": int(row[1] or 0)} for row in top_commanders],
    }


METRICS_GAMES_LIMIT = 200
POD_METRICS_GAMES_LIMIT = 10


def _metrics_games(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    query = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .filter(*filters)
        .order_by(GameSession.played_at.desc().nullslast(), GameSession.created_at.desc())
    )
    if limit is not None and limit > 0:
        query = query.limit(limit)
    sessions = query.all()
    return [session_shared._game_session_payload(session, user_id) for session in sessions]


def _top_players_by_plays(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 5,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    plays_expr = func.count(func.distinct(GameSession.id))
    rows = (
        db.session.query(
            GamePlayer.user_id.label("user_id"),
            GamePlayer.display_name.label("display_name"),
            plays_expr.label("plays"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.player_id == GamePlayer.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters)
        .group_by(GamePlayer.user_id, GamePlayer.display_name)
        .all()
    )
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key, label = support._canonical_player_identity(row.user_id, row.display_name, scope)
        entry = merged.setdefault(key, {"key": key, "label": label, "count": 0})
        entry["count"] += int(row.plays or 0)
    results = sorted(
        merged.values(),
        key=lambda item: (-item["count"], (item["label"] or "").lower()),
    )
    return results[:limit]


def _combo_winners(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 5,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    wins_expr = func.count(func.distinct(GameSession.id))
    rows = (
        db.session.query(
            GamePlayer.user_id.label("user_id"),
            GamePlayer.display_name.label("display_name"),
            wins_expr.label("wins"),
        )
        .join(GameSeat, GameSeat.id == GameSession.winner_seat_id)
        .join(GameSeatAssignment, GameSeatAssignment.seat_id == GameSeat.id)
        .join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id)
        .filter(*filters, GameSession.win_via_combo.is_(True))
        .group_by(GamePlayer.user_id, GamePlayer.display_name)
        .all()
    )
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key, label = support._canonical_player_identity(row.user_id, row.display_name, scope)
        entry = merged.setdefault(key, {"label": label, "count": 0})
        entry["count"] += int(row.wins or 0)
    results = sorted(
        merged.values(),
        key=lambda item: (-item["count"], (item["label"] or "").lower()),
    )
    return results[:limit]


def _deck_usage(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 5,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    query = (
        db.session.query(
            GameDeck.deck_name,
            func.count(GameSeatAssignment.id).label("plays"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
    )
    player_filter = support._player_key_filter(player_key, scope=scope)
    if player_filter is not None:
        query = query.join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id).filter(player_filter)
    rows = (
        query.filter(*filters)
        .group_by(GameDeck.deck_name)
        .order_by(func.count(GameSeatAssignment.id).desc(), GameDeck.deck_name.asc())
        .limit(limit)
        .all()
    )
    return [{"label": row.deck_name or "Unknown deck", "count": int(row.plays or 0)} for row in rows]


def _commander_usage(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 5,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    query = (
        db.session.query(
            GameDeck.commander_name,
            func.count(GameSeatAssignment.id).label("plays"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
    )
    player_filter = support._player_key_filter(player_key, scope=scope)
    if player_filter is not None:
        query = query.join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id).filter(player_filter)
    rows = (
        query.filter(*filters, GameDeck.commander_name.isnot(None))
        .group_by(GameDeck.commander_name)
        .order_by(func.count(GameSeatAssignment.id).desc(), GameDeck.commander_name.asc())
        .limit(limit)
        .all()
    )
    return [{"label": row.commander_name or "Unknown", "count": int(row.plays or 0)} for row in rows]


def _commander_win_rates(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 6,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    plays_expr = func.count(GameSeatAssignment.id)
    wins_expr = func.coalesce(func.sum(case((GameSession.winner_seat_id == GameSeat.id, 1), else_=0)), 0)
    query = (
        db.session.query(
            GameDeck.commander_name,
            plays_expr.label("plays"),
            wins_expr.label("wins"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
    )
    player_filter = support._player_key_filter(player_key, scope=scope)
    if player_filter is not None:
        query = query.join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id).filter(player_filter)
    rows = (
        query.filter(*filters, GameDeck.commander_name.isnot(None))
        .group_by(GameDeck.commander_name)
        .order_by(plays_expr.desc(), GameDeck.commander_name.asc())
        .limit(limit)
        .all()
    )
    results = []
    for row in rows:
        plays = int(row.plays or 0)
        wins = int(row.wins or 0)
        win_rate = round((wins / plays) * 100, 1) if plays else 0
        results.append(
            {
                "label": row.commander_name or "Unknown",
                "plays": plays,
                "wins": wins,
                "win_rate": win_rate,
            }
        )
    return results


def _bracket_stats(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 6,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    label_expr = func.coalesce(GameDeck.bracket_label, GameDeck.bracket_level)
    plays_expr = func.count(GameSeatAssignment.id)
    wins_expr = func.coalesce(func.sum(case((GameSession.winner_seat_id == GameSeat.id, 1), else_=0)), 0)
    query = (
        db.session.query(
            label_expr.label("label"),
            plays_expr.label("plays"),
            wins_expr.label("wins"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
    )
    player_filter = support._player_key_filter(player_key, scope=scope)
    if player_filter is not None:
        query = query.join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id).filter(player_filter)
    rows = (
        query.filter(*filters, label_expr.isnot(None))
        .group_by(label_expr)
        .order_by(plays_expr.desc(), label_expr.asc())
        .limit(limit)
        .all()
    )
    results = []
    for row in rows:
        plays = int(row.plays or 0)
        wins = int(row.wins or 0)
        win_rate = round((wins / plays) * 100, 1) if plays else 0
        results.append(
            {
                "label": row.label or "Unknown",
                "plays": plays,
                "wins": wins,
                "win_rate": win_rate,
            }
        )
    return results


def _turn_order_metrics(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    seat_counts = support._seat_counts_subquery(filters)
    turn_order_expr = func.coalesce(GameSeat.turn_order, GameSeat.seat_number)
    rows = (
        db.session.query(
            seat_counts.c.seat_count.label("seat_count"),
            turn_order_expr.label("turn_order"),
            func.count(GameSeat.id).label("plays"),
            func.coalesce(func.sum(case((GameSession.winner_seat_id == GameSeat.id, 1), else_=0)), 0).label("wins"),
        )
        .join(GameSeat, GameSeat.session_id == seat_counts.c.session_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(
            seat_counts.c.seat_count.in_([3, 4]),
            turn_order_expr.isnot(None),
        )
        .group_by(seat_counts.c.seat_count, turn_order_expr)
        .order_by(seat_counts.c.seat_count.asc(), turn_order_expr.asc())
        .all()
    )
    buckets: dict[int, list[dict[str, Any]]] = {3: [], 4: []}
    for row in rows:
        seat_count = int(row.seat_count or 0)
        turn_order = int(row.turn_order or 0)
        if seat_count not in buckets:
            continue
        if not (1 <= turn_order <= seat_count):
            continue
        plays = int(row.plays or 0)
        wins = int(row.wins or 0)
        win_rate = round((wins / plays) * 100, 1) if plays else 0
        buckets[seat_count].append(
            {
                "turn_order": turn_order,
                "plays": plays,
                "wins": wins,
                "win_rate": win_rate,
            }
        )
    return {
        "three_player": buckets[3],
        "four_player": buckets[4],
    }


def _player_options(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    rows = (
        db.session.query(
            GamePlayer.user_id.label("user_id"),
            GamePlayer.display_name.label("display_name"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.player_id == GamePlayer.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters)
        .distinct()
        .all()
    )
    options_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        key, label = support._canonical_player_identity(row.user_id, row.display_name, scope)
        if key not in options_map:
            options_map[key] = {"key": key, "label": label}
    options = list(options_map.values())
    options.sort(key=lambda item: (item["label"] or "").lower())
    return options


def _deck_options(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    query = (
        db.session.query(
            GameDeck.folder_id,
            GameDeck.deck_name,
            GameDeck.commander_name,
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
    )
    player_filter = support._player_key_filter(player_key, scope=scope)
    if player_filter is not None:
        query = query.join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id).filter(player_filter)
    rows = query.filter(*filters).distinct().all()
    options_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        deck_name = (row.deck_name or "").strip() or "Unknown deck"
        if row.folder_id:
            key = f"folder:{row.folder_id}"
        else:
            key = f"name:{deck_name}"
        label = deck_name
        if row.commander_name:
            label = f"{label} · {row.commander_name}"
        options_map.setdefault(key, {"key": key, "label": label})
    options = list(options_map.values())
    options.sort(key=lambda item: (item["label"] or "").lower())
    return options


def _player_stats(
    user_id: int,
    player_key: str | None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    player_filter = support._player_key_filter(player_key, scope=scope)
    if player_filter is None:
        return None
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    wins_expr = func.count(
        func.distinct(
            case((GameSession.winner_seat_id == GameSeat.id, GameSession.id), else_=None)
        )
    )
    combo_expr = func.count(
        func.distinct(
            case(
                (
                    and_(
                        GameSession.winner_seat_id == GameSeat.id,
                        GameSession.win_via_combo.is_(True),
                    ),
                    GameSession.id,
                ),
                else_=None,
            )
        )
    )
    games_played, wins, combo_wins = (
        db.session.query(
            func.count(func.distinct(GameSession.id)),
            wins_expr,
            combo_expr,
        )
        .join(GameSeatAssignment, GameSeatAssignment.session_id == GameSession.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id)
        .filter(*filters, player_filter)
        .one()
    )
    games_played = int(games_played or 0)
    wins = int(wins or 0)
    combo_wins = int(combo_wins or 0)
    win_rate = round((wins / games_played) * 100, 1) if games_played else 0

    deck_rows = (
        db.session.query(
            GameDeck.deck_name,
            func.count(func.distinct(GameSession.id)).label("plays"),
            func.count(
                func.distinct(
                    case((GameSession.winner_seat_id == GameSeat.id, GameSession.id), else_=None)
                )
            ).label("wins"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id)
        .filter(*filters, player_filter)
        .group_by(GameDeck.deck_name)
        .order_by(func.count(func.distinct(GameSession.id)).desc(), GameDeck.deck_name.asc())
        .limit(6)
        .all()
    )
    deck_stats = []
    for row in deck_rows:
        plays = int(row.plays or 0)
        wins_count = int(row.wins or 0)
        deck_stats.append(
            {
                "label": row.deck_name or "Unknown deck",
                "plays": plays,
                "wins": wins_count,
                "win_rate": round((wins_count / plays) * 100, 1) if plays else 0,
            }
        )
    return {
        "games_played": games_played,
        "wins": wins,
        "combo_wins": combo_wins,
        "win_rate": win_rate,
        "deck_stats": deck_stats,
    }


def _player_deck_stats(
    user_id: int,
    player_key: str | None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    player_filter = support._player_key_filter(player_key, scope=scope)
    if player_filter is None:
        return []
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    rows = (
        db.session.query(
            GameDeck.deck_name,
            GameDeck.commander_name,
            func.count(func.distinct(GameSession.id)).label("plays"),
            func.count(
                func.distinct(
                    case((GameSession.winner_seat_id == GameSeat.id, GameSession.id), else_=None)
                )
            ).label("wins"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id)
        .filter(*filters, player_filter)
        .group_by(GameDeck.deck_name, GameDeck.commander_name)
        .order_by(func.count(func.distinct(GameSession.id)).desc(), GameDeck.deck_name.asc())
        .all()
    )
    stats: list[dict[str, Any]] = []
    for row in rows:
        plays = int(row.plays or 0)
        wins = int(row.wins or 0)
        stats.append(
            {
                "label": row.deck_name or "Unknown deck",
                "commander": row.commander_name or "",
                "plays": plays,
                "wins": wins,
                "win_rate": round((wins / plays) * 100, 1) if plays else 0,
            }
        )
    return stats


def _player_win_rates(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 6,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    rows = (
        db.session.query(
            GamePlayer.user_id.label("user_id"),
            GamePlayer.display_name.label("display_name"),
            func.count(func.distinct(GameSession.id)).label("plays"),
            func.count(
                func.distinct(
                    case((GameSession.winner_seat_id == GameSeat.id, GameSession.id), else_=None)
                )
            ).label("wins"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.player_id == GamePlayer.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters)
        .group_by(GamePlayer.user_id, GamePlayer.display_name)
        .all()
    )

    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key, label = support._canonical_player_identity(row.user_id, row.display_name, scope)
        if key in merged:
            merged[key]["plays"] += int(row.plays or 0)
            merged[key]["wins"] += int(row.wins or 0)
        else:
            merged[key] = {
                "key": key,
                "label": label,
                "plays": int(row.plays or 0),
                "wins": int(row.wins or 0),
            }

    results = []
    for entry in merged.values():
        plays = entry["plays"]
        wins = entry["wins"]
        entry["win_rate"] = round((wins / plays) * 100, 1) if plays else 0
        results.append(entry)

    results.sort(key=lambda item: (-item["plays"], (item["label"] or "").lower()))
    return results[:limit]


def _deck_win_rates(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int | None = 6,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = support._session_filters(user_id, start_at, end_at, scope=scope)
    plays_expr = func.count(GameSeatAssignment.id)
    wins_expr = func.coalesce(func.sum(case((GameSession.winner_seat_id == GameSeat.id, 1), else_=0)), 0)
    query = (
        db.session.query(
            GameDeck.deck_name,
            plays_expr.label("plays"),
            wins_expr.label("wins"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
    )
    player_filter = support._player_key_filter(player_key, scope=scope)
    if player_filter is not None:
        query = query.join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id).filter(player_filter)

    query = (
        query.filter(*filters)
        .group_by(GameDeck.deck_name)
        .order_by(plays_expr.desc(), GameDeck.deck_name.asc())
    )
    if limit is not None and limit > 0:
        query = query.limit(limit)
    rows = query.all()

    results = []
    for row in rows:
        plays = int(row.plays or 0)
        wins = int(row.wins or 0)
        win_rate = round((wins / plays) * 100, 1) if plays else 0
        results.append(
            {
                "label": row.deck_name or "Unknown deck",
                "plays": plays,
                "wins": wins,
                "win_rate": win_rate,
            }
        )
    return results
