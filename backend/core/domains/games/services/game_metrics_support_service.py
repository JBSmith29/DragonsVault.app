"""Shared metrics filters, ranges, and option helpers for the games domain."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, text

from extensions import db
from models import (
    GameDeck,
    GamePlayer,
    GamePod,
    GamePodMember,
    GameRosterPlayer,
    GameSeat,
    GameSeatAssignment,
    GameSession,
    User,
)
from shared.validation import ValidationError, log_validation_error, parse_positive_int

__all__ = [
    "_apply_notes_search",
    "_available_years",
    "_canonical_player_identity",
    "_deck_key_filter",
    "_merge_scope_filters",
    "_metrics_cache_key",
    "_parse_date_value",
    "_player_key_filter",
    "_player_label_expr",
    "_pod_metrics_scope",
    "_pod_options_for_user",
    "_range_query_params",
    "_resolve_date_range",
    "_resolve_year_or_all_range",
    "_seat_count_breakdown",
    "_seat_counts_subquery",
    "_session_filter_for_deck",
    "_session_filter_for_player",
    "_session_filters",
    "_session_visibility_filter",
]


def _available_years(user_id: int, scope: dict[str, Any] | None = None) -> list[int]:
    visibility_filter = _session_visibility_filter(user_id)
    if db.engine.dialect.name == "sqlite":
        year_expr = func.strftime("%Y", GameSession.played_at)
    else:
        year_expr = func.extract("year", GameSession.played_at)
    query = db.session.query(year_expr).filter(visibility_filter)
    if scope and scope.get("session_filter") is not None:
        query = query.filter(scope["session_filter"])
    rows = query.distinct().order_by(year_expr.desc()).all()
    years: list[int] = []
    for (value,) in rows:
        if value is None:
            continue
        try:
            years.append(int(value))
        except (TypeError, ValueError):
            continue
    if not years:
        years.append(date.today().year)
    return years


def _apply_notes_search(query, q: str):
    if not q:
        return query
    if db.engine.dialect.name == "sqlite":
        try:
            rows = db.session.execute(
                text("SELECT rowid FROM game_sessions_fts WHERE game_sessions_fts MATCH :q"),
                {"q": q},
            ).fetchall()
            ids = [row[0] for row in rows]
            if not ids:
                return query.filter(text("1 = 0"))
            return query.filter(GameSession.id.in_(ids))
        except Exception:
            db.session.rollback()
    return query.filter(GameSession.notes.ilike(f"%{q}%"))


def _session_visibility_filter(user_id: int):
    return or_(
        GameSession.owner_user_id == user_id,
        GameSession.seats.any(
            GameSeat.assignment.has(
                GameSeatAssignment.player.has(GamePlayer.user_id == user_id)
            )
        ),
    )


def _session_filters(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> list:
    filters = [_session_visibility_filter(user_id)]
    if scope and scope.get("session_filter") is not None:
        filters.append(scope["session_filter"])
    if start_at:
        filters.append(GameSession.played_at >= start_at)
    if end_at:
        filters.append(GameSession.played_at <= end_at)
    return filters


def _pod_options_for_user(user_id: int) -> list[dict[str, Any]]:
    member_pod_ids = [
        pod_id
        for (pod_id,) in db.session.query(GamePodMember.pod_id)
        .join(GameRosterPlayer, GameRosterPlayer.id == GamePodMember.roster_player_id)
        .filter(GameRosterPlayer.user_id == user_id)
        .distinct()
        .all()
    ]
    pod_filters = [GamePod.owner_user_id == user_id]
    if member_pod_ids:
        pod_filters.append(GamePod.id.in_(member_pod_ids))
    pods = (
        GamePod.query.filter(or_(*pod_filters))
        .order_by(func.lower(GamePod.name), GamePod.id.asc())
        .all()
    )
    if not pods:
        return []
    owner_ids = {pod.owner_user_id for pod in pods}
    owner_label_map: dict[int, str] = {}
    if owner_ids:
        for user in User.query.filter(User.id.in_(owner_ids)).all():
            owner_label_map[user.id] = (
                user.display_name
                or user.username
                or user.email
                or f"User {user.id}"
            )
    options = []
    for pod in pods:
        label = pod.name
        if pod.owner_user_id != user_id:
            owner_label = owner_label_map.get(pod.owner_user_id) or f"User {pod.owner_user_id}"
            label = f"{label} · {owner_label}"
        options.append({"id": pod.id, "label": label})
    return options


def _pod_metrics_scope(user_id: int, pod_id: int | None = None) -> dict[str, Any] | None:
    member_pod_ids = [
        pod_id
        for (pod_id,) in db.session.query(GamePodMember.pod_id)
        .join(GameRosterPlayer, GameRosterPlayer.id == GamePodMember.roster_player_id)
        .filter(GameRosterPlayer.user_id == user_id)
        .distinct()
        .all()
    ]
    pod_filters = [GamePod.owner_user_id == user_id]
    if member_pod_ids:
        pod_filters.append(GamePod.id.in_(member_pod_ids))
    access_filter = or_(*pod_filters)
    if pod_id:
        access_filter = and_(access_filter, GamePod.id == pod_id)

    rows = (
        db.session.query(
            GameRosterPlayer.user_id.label("user_id"),
            GameRosterPlayer.display_name.label("display_name"),
            User.display_name.label("user_display_name"),
            User.username.label("user_username"),
            User.email.label("user_email"),
        )
        .join(GamePodMember, GamePodMember.roster_player_id == GameRosterPlayer.id)
        .join(GamePod, GamePod.id == GamePodMember.pod_id)
        .outerjoin(User, User.id == GameRosterPlayer.user_id)
        .filter(access_filter)
        .distinct()
        .all()
    )
    if not rows:
        return None

    allowed_user_ids: set[int] = set()
    allowed_names: set[str] = set()
    alias_map: dict[str, int] = {}
    alias_names_by_user: dict[int, set[str]] = {}
    user_label_map: dict[int, str] = {}
    name_label_map: dict[str, str] = {}

    for row in rows:
        if row.user_id:
            allowed_user_ids.add(int(row.user_id))
            user_label_map.setdefault(
                int(row.user_id),
                row.user_display_name
                or row.user_username
                or row.user_email
                or f"User {row.user_id}",
            )
            alias_candidates = {
                row.display_name,
                row.user_display_name,
                row.user_username,
                row.user_email,
            }
        else:
            alias_candidates = {row.display_name}

        for alias in {value for value in alias_candidates if value}:
            normalized = alias.strip().lower()
            if not normalized:
                continue
            allowed_names.add(normalized)
            name_label_map.setdefault(normalized, alias.strip())
            if row.user_id:
                alias_map[normalized] = int(row.user_id)
                alias_names_by_user.setdefault(int(row.user_id), set()).add(normalized)

    if not allowed_user_ids and not allowed_names:
        return None

    session_filter = None
    if allowed_user_ids or allowed_names:
        allowed_expr = or_(
            and_(GamePlayer.user_id.isnot(None), GamePlayer.user_id.in_(allowed_user_ids))
            if allowed_user_ids
            else False,
            and_(GamePlayer.user_id.is_(None), func.lower(GamePlayer.display_name).in_(allowed_names))
            if allowed_names
            else False,
        )
        invalid_exists = (
            db.session.query(GameSeat.id)
            .join(GameSeatAssignment, GameSeatAssignment.seat_id == GameSeat.id)
            .join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id)
            .filter(GameSeat.session_id == GameSession.id)
            .filter(~allowed_expr)
            .exists()
        )
        session_filter = ~invalid_exists

    return {
        "allowed_user_ids": allowed_user_ids,
        "allowed_names": allowed_names,
        "alias_map": alias_map,
        "alias_names_by_user": alias_names_by_user,
        "user_label_map": user_label_map,
        "name_label_map": name_label_map,
        "session_filter": session_filter,
    }


def _canonical_player_identity(
    user_id: int | None,
    display_name: str | None,
    scope: dict[str, Any] | None,
) -> tuple[str, str]:
    name_key = (display_name or "").strip().lower()
    if user_id:
        key = f"user:{int(user_id)}"
        label = (
            (scope.get("user_label_map") or {}).get(int(user_id))
            if scope
            else None
        ) or display_name or f"User {user_id}"
        return key, label
    if scope and name_key and name_key in scope.get("alias_map", {}):
        mapped_id = scope["alias_map"][name_key]
        label = scope.get("user_label_map", {}).get(mapped_id) or display_name or f"User {mapped_id}"
        return f"user:{mapped_id}", label
    label = (
        (scope.get("name_label_map") or {}).get(name_key) if scope else None
    ) or display_name or "Unknown"
    key = f"name:{name_key}" if name_key else "name:unknown"
    return key, label


def _seat_counts_subquery(filters: list) -> Any:
    return (
        db.session.query(
            GameSeat.session_id.label("session_id"),
            func.count(GameSeat.id).label("seat_count"),
        )
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters)
        .group_by(GameSeat.session_id)
        .subquery()
    )


def _parse_date_value(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _resolve_date_range(params) -> dict[str, Any]:
    today = date.today()
    range_key = (params.get("range") or "last30").strip().lower()
    year_raw = params.get("year")
    start_raw = params.get("start")
    end_raw = params.get("end")

    start_date = None
    end_date = None
    label = "Last 30 days"
    year_value = None

    if range_key == "all":
        label = "All time"
    elif range_key == "last90":
        start_date = today - timedelta(days=90)
        end_date = today
        label = "Last 90 days"
    elif range_key == "ytd":
        start_date = date(today.year, 1, 1)
        end_date = today
        label = "Year to date"
    elif range_key == "year":
        try:
            year_value = int(year_raw)
        except (TypeError, ValueError):
            year_value = today.year
        start_date = date(year_value, 1, 1)
        end_date = date(year_value, 12, 31)
        label = f"{year_value}"
    elif range_key == "custom":
        start_date = _parse_date_value(start_raw)
        end_date = _parse_date_value(end_raw)
        label = "Custom range"
    else:
        start_date = today - timedelta(days=30)
        end_date = today
        label = "Last 30 days"

    start_at = datetime.combine(start_date, datetime.min.time()) if start_date else None
    end_at = datetime.combine(end_date, datetime.max.time()) if end_date else None

    return {
        "range_key": range_key,
        "label": label,
        "start_at": start_at,
        "end_at": end_at,
        "start_value": start_date.isoformat() if start_date else "",
        "end_value": end_date.isoformat() if end_date else "",
        "year_value": year_value or "",
    }


def _resolve_year_or_all_range(params) -> dict[str, Any]:
    range_key = (params.get("range") or "all").strip().lower()
    if range_key == "year":
        return _resolve_date_range({
            "range": "year",
            "year": params.get("year"),
        })
    return _resolve_date_range({"range": "all"})


def _range_query_params(range_ctx: dict[str, Any]) -> dict[str, str]:
    params: dict[str, str] = {"range": range_ctx.get("range_key") or ""}
    if range_ctx.get("range_key") == "year" and range_ctx.get("year_value"):
        params["year"] = str(range_ctx["year_value"])
    if range_ctx.get("range_key") == "custom":
        if range_ctx.get("start_value"):
            params["start"] = str(range_ctx["start_value"])
        if range_ctx.get("end_value"):
            params["end"] = str(range_ctx["end_value"])
    return params


def _metrics_cache_key(
    user_id: int,
    range_ctx: dict[str, Any],
    *,
    pod_id: int | None = None,
    player_key: str | None = None,
    deck_key: str | None = None,
    suffix: str = "payload",
) -> str:
    range_key = range_ctx.get("range_key") or ""
    start_value = range_ctx.get("start_value") or ""
    end_value = range_ctx.get("end_value") or ""
    year_value = range_ctx.get("year_value") or ""
    return (
        f"metrics_{suffix}_{user_id}_{pod_id or 'all'}_"
        f"{player_key or 'all'}_{deck_key or 'all'}_"
        f"{range_key}_{start_value}_{end_value}_{year_value}"
    )


def _player_key_filter(player_key: str | None, scope: dict[str, Any] | None = None):
    if not player_key:
        return None
    key = player_key.strip()
    if key.startswith("user:"):
        try:
            user_id = parse_positive_int(key.split(":", 1)[1], field="player")
        except ValidationError as exc:
            log_validation_error(exc, context="metrics_player")
            return None
        if scope:
            alias_names = scope.get("alias_names_by_user", {}).get(user_id)
            if alias_names:
                return or_(
                    GamePlayer.user_id == user_id,
                    and_(
                        GamePlayer.user_id.is_(None),
                        func.lower(GamePlayer.display_name).in_(alias_names),
                    ),
                )
        return GamePlayer.user_id == user_id
    if key.startswith("name:"):
        name = key.split(":", 1)[1].strip().lower()
        if not name:
            return None
        if scope and name in scope.get("alias_map", {}):
            user_id = scope["alias_map"][name]
            alias_names = scope.get("alias_names_by_user", {}).get(user_id)
            if alias_names:
                return or_(
                    GamePlayer.user_id == user_id,
                    and_(
                        GamePlayer.user_id.is_(None),
                        func.lower(GamePlayer.display_name).in_(alias_names),
                    ),
                )
            return GamePlayer.user_id == user_id
        return func.lower(GamePlayer.display_name) == name
    return None


def _deck_key_filter(deck_key: str | None):
    if not deck_key:
        return None
    key = deck_key.strip()
    if key.startswith("folder:"):
        try:
            folder_id = parse_positive_int(key.split(":", 1)[1], field="deck")
        except ValidationError as exc:
            log_validation_error(exc, context="metrics_deck_filter")
            return None
        return GameDeck.folder_id == folder_id
    if key.startswith("name:"):
        deck_name = key.split(":", 1)[1].strip()
        if not deck_name:
            return None
        return func.lower(GameDeck.deck_name) == deck_name.lower()
    return None


def _session_filter_for_player(player_key: str | None, scope: dict[str, Any] | None = None):
    player_filter = _player_key_filter(player_key, scope=scope)
    if player_filter is None:
        return None
    return GameSession.seats.any(
        GameSeat.assignment.has(
            GameSeatAssignment.player.has(player_filter)
        )
    )


def _session_filter_for_deck(deck_key: str | None):
    deck_filter = _deck_key_filter(deck_key)
    if deck_filter is None:
        return None
    return GameSession.seats.any(
        GameSeat.assignment.has(
            GameSeatAssignment.deck.has(deck_filter)
        )
    )


def _merge_scope_filters(
    scope: dict[str, Any] | None,
    extra_filters: list[Any] | None = None,
) -> dict[str, Any] | None:
    if not extra_filters:
        return scope
    combined = scope.get("session_filter") if scope else None
    for extra in extra_filters:
        if extra is None:
            continue
        combined = extra if combined is None else and_(combined, extra)
    if combined is None:
        return scope
    next_scope = dict(scope or {})
    next_scope["session_filter"] = combined
    return next_scope


def _seat_count_breakdown(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
    seat_counts = _seat_counts_subquery(filters)
    rows = (
        db.session.query(
            seat_counts.c.seat_count,
            func.count(seat_counts.c.session_id).label("games"),
        )
        .group_by(seat_counts.c.seat_count)
        .order_by(seat_counts.c.seat_count.asc())
        .all()
    )
    total = sum(int(row.games or 0) for row in rows) or 0
    breakdown = []
    for row in rows:
        count = int(row.games or 0)
        pct = round((count / total) * 100, 1) if total else 0
        breakdown.append(
            {
                "seat_count": int(row.seat_count or 0),
                "games": count,
                "percent": pct,
            }
        )
    return breakdown


def _player_label_expr():
    return func.coalesce(GamePlayer.display_name, "Unknown")
