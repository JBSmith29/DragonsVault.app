"""Commander game tracking service layer."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import csv
import io
from typing import Any

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import case, func, or_, text
from sqlalchemy.orm import selectinload

from extensions import db
from models import (
    CommanderBracketCache,
    Folder,
    FolderRole,
    GameDeck,
    GamePlayer,
    GameSeat,
    GameSeatAssignment,
    GameSession,
    GamePod,
    GamePodMember,
    GameRosterDeck,
    GameRosterPlayer,
    User,
)
from utils.time import utcnow
from utils.validation import ValidationError, log_validation_error, parse_optional_positive_int, parse_positive_int


def _accessible_deck_options(owner_user_id: int | None = None) -> list[dict[str, Any]]:
    query = (
        db.session.query(
            Folder.id,
            Folder.name,
            Folder.commander_name,
            Folder.owner,
        )
        .join(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(
            FolderRole.role.in_(FolderRole.DECK_ROLES),
        )
    )
    if owner_user_id is not None:
        query = query.filter(Folder.owner_user_id == owner_user_id)
    rows = (
        query.group_by(Folder.id, Folder.name, Folder.commander_name, Folder.owner)
        .order_by(func.lower(Folder.name))
        .all()
    )
    options: list[dict[str, Any]] = []
    for row in rows:
        label = row.name or f"Deck {row.id}"
        if row.commander_name:
            label = f"{label} · {row.commander_name}"
        if row.owner:
            label = f"{label} · {row.owner}"
        options.append({"id": row.id, "label": label, "ref": f"folder:{row.id}"})
    return options


def _roster_players(owner_user_id: int) -> list[dict[str, Any]]:
    players = (
        GameRosterPlayer.query.options(
            selectinload(GameRosterPlayer.user),
            selectinload(GameRosterPlayer.decks),
        )
        .filter(GameRosterPlayer.owner_user_id == owner_user_id)
        .order_by(func.lower(func.coalesce(GameRosterPlayer.display_name, "")), GameRosterPlayer.id.asc())
        .all()
    )
    deck_ids = {
        deck.folder_id
        for player in players
        for deck in (player.decks or [])
        if deck.folder_id is not None
    }
    folders = Folder.query.filter(Folder.id.in_(deck_ids)).all() if deck_ids else []
    folder_map = {folder.id: folder for folder in folders}

    payloads: list[dict[str, Any]] = []
    for player in players:
        label = (
            player.display_name
            or (player.user.display_name if player.user else None)
            or (player.user.username if player.user else None)
            or (player.user.email if player.user else None)
            or "Player"
        )
        assigned_decks: list[dict[str, Any]] = []
        for assignment in player.decks or []:
            if assignment.folder_id:
                folder = folder_map.get(assignment.folder_id)
                if not folder:
                    continue
                deck_label = folder.name or f"Deck {folder.id}"
                if folder.commander_name:
                    deck_label = f"{deck_label} · {folder.commander_name}"
                assigned_decks.append({"ref": f"folder:{folder.id}", "label": deck_label})
            elif assignment.deck_name:
                assigned_decks.append({"ref": f"manual:{assignment.id}", "label": assignment.deck_name})
        assigned_decks.sort(key=lambda item: item["label"].lower())
        payloads.append(
            {
                "id": player.id,
                "label": label,
                "user_id": player.user_id,
                "deck_options": assigned_decks,
            }
        )
    return payloads


def _roster_payloads_for_owner(owner_user_id: int) -> list[dict[str, Any]]:
    roster_players = (
        GameRosterPlayer.query.options(
            selectinload(GameRosterPlayer.user),
            selectinload(GameRosterPlayer.decks),
        )
        .filter(GameRosterPlayer.owner_user_id == owner_user_id)
        .order_by(func.lower(func.coalesce(GameRosterPlayer.display_name, "")), GameRosterPlayer.id.asc())
        .all()
    )
    deck_ids = {
        deck.folder_id
        for player in roster_players
        for deck in (player.decks or [])
        if deck.folder_id is not None
    }
    folders = Folder.query.filter(Folder.id.in_(deck_ids)).all() if deck_ids else []
    folder_map = {folder.id: folder for folder in folders}
    roster_decks_map: dict[int, list[dict[str, Any]]] = {player.id: [] for player in roster_players}
    for player in roster_players:
        for deck in player.decks or []:
            if deck.folder_id:
                folder = folder_map.get(deck.folder_id)
                if not folder:
                    continue
                label = folder.name or f"Deck {folder.id}"
                if folder.commander_name:
                    label = f"{label} · {folder.commander_name}"
                roster_decks_map[player.id].append(
                    {
                        "assignment_id": deck.id,
                        "deck_id": folder.id,
                        "label": label,
                        "is_manual": False,
                    }
                )
            elif deck.deck_name:
                roster_decks_map[player.id].append(
                    {
                        "assignment_id": deck.id,
                        "deck_id": None,
                        "label": deck.deck_name,
                        "is_manual": True,
                    }
                )
        roster_decks_map[player.id].sort(key=lambda item: item["label"].lower())

    payloads: list[dict[str, Any]] = []
    for player in roster_players:
        label = (
            player.display_name
            or (player.user.display_name if player.user else None)
            or (player.user.username if player.user else None)
            or (player.user.email if player.user else None)
            or "Player"
        )
        payloads.append(
            {
                "id": player.id,
                "owner_user_id": owner_user_id,
                "user_id": player.user_id,
                "label": label,
                "user_label": (player.user.username or player.user.email) if player.user else None,
                "deck_assignments": roster_decks_map.get(player.id, []),
            }
        )
    return payloads


def _pod_access_flags(pod: GamePod, user_id: int) -> tuple[bool, bool]:
    is_owner = pod.owner_user_id == user_id
    is_member = any(
        member.roster_player and member.roster_player.user_id == user_id
        for member in (pod.members or [])
    )
    return is_owner, is_member


def _pod_payloads_for_owner(owner_user_id: int, roster_players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roster_label_map = {player["id"]: player["label"] for player in roster_players}
    pods = (
        GamePod.query.options(
            selectinload(GamePod.members).selectinload(GamePodMember.roster_player).selectinload(GameRosterPlayer.user)
        )
        .filter(GamePod.owner_user_id == owner_user_id)
        .order_by(func.lower(GamePod.name), GamePod.id.asc())
        .all()
    )
    payloads: list[dict[str, Any]] = []
    for pod in pods:
        members: list[dict[str, Any]] = []
        member_ids: list[int] = []
        for member in pod.members or []:
            roster_player = member.roster_player
            if not roster_player:
                continue
            label = roster_label_map.get(roster_player.id)
            if not label:
                label = (
                    roster_player.display_name
                    or (roster_player.user.display_name if roster_player.user else None)
                    or (roster_player.user.username if roster_player.user else None)
                    or (roster_player.user.email if roster_player.user else None)
                    or "Player"
                )
            members.append(
                {
                    "member_id": member.id,
                    "roster_id": roster_player.id,
                    "label": label,
                }
            )
            member_ids.append(roster_player.id)
        members.sort(key=lambda item: item["label"].lower())
        payloads.append(
            {
                "id": pod.id,
                "name": pod.name,
                "members": members,
                "member_ids": member_ids,
            }
        )
    return payloads


def _pod_payloads_for_management(
    pods: list[GamePod],
    roster_label_map_by_owner: dict[int, dict[int, str]],
    roster_options_by_owner: dict[int, list[dict[str, Any]]],
    owner_label_map: dict[int, str],
    current_user_id: int,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for pod in pods:
        roster_label_map = roster_label_map_by_owner.get(pod.owner_user_id, {})
        members: list[dict[str, Any]] = []
        member_ids: list[int] = []
        self_member_id = None
        for member in pod.members or []:
            roster_player = member.roster_player
            if not roster_player:
                continue
            label = roster_label_map.get(roster_player.id)
            if not label:
                label = (
                    roster_player.display_name
                    or (roster_player.user.display_name if roster_player.user else None)
                    or (roster_player.user.username if roster_player.user else None)
                    or (roster_player.user.email if roster_player.user else None)
                    or "Player"
                )
            members.append(
                {
                    "member_id": member.id,
                    "roster_id": roster_player.id,
                    "label": label,
                }
            )
            member_ids.append(roster_player.id)
            if roster_player.user_id == current_user_id:
                self_member_id = member.id
        members.sort(key=lambda item: item["label"].lower())
        is_owner, is_member = _pod_access_flags(pod, current_user_id)
        payloads.append(
            {
                "id": pod.id,
                "name": pod.name,
                "members": members,
                "member_ids": member_ids,
                "owner_user_id": pod.owner_user_id,
                "owner_label": owner_label_map.get(pod.owner_user_id) or "Unknown owner",
                "is_owner": is_owner,
                "can_manage": is_owner or is_member,
                "self_member_id": self_member_id,
                "roster_options": roster_options_by_owner.get(pod.owner_user_id, []),
            }
        )
    return payloads


def _parse_deck_ref(raw_value: str | None, *, seat_number: int, errors: list[str]) -> tuple[str | None, int | None]:
    raw = (raw_value or "").strip()
    if not raw:
        errors.append(f"Seat {seat_number}: select a deck.")
        return None, None
    if raw.isdigit():
        return "folder", int(raw)
    if raw.startswith("folder:"):
        try:
            deck_id = parse_positive_int(raw.split(":", 1)[1], field="deck", min_value=1)
        except ValidationError as exc:
            log_validation_error(exc, context="game_deck_ref")
            errors.append(f"Seat {seat_number}: select a deck.")
            return None, None
        return "folder", deck_id
    if raw.startswith("manual:"):
        try:
            manual_id = parse_positive_int(raw.split(":", 1)[1], field="manual deck", min_value=1)
        except ValidationError as exc:
            log_validation_error(exc, context="game_deck_ref")
            errors.append(f"Seat {seat_number}: select a deck.")
            return None, None
        return "manual", manual_id
    errors.append(f"Seat {seat_number}: select a deck.")
    return None, None


def _snapshot_deck(folder: Folder) -> dict[str, Any]:
    bracket_level = None
    bracket_label = None
    bracket_score = None
    power_score = None
    if folder.id:
        cache = CommanderBracketCache.query.filter_by(folder_id=folder.id).first()
        if cache and cache.payload:
            bracket_level = cache.payload.get("level")
            bracket_label = cache.payload.get("label")
            bracket_score = cache.payload.get("score")
            power_score = cache.payload.get("score")
    return {
        "folder_id": folder.id,
        "deck_name": folder.name or f"Deck {folder.id}",
        "commander_name": folder.commander_name,
        "commander_oracle_id": folder.commander_oracle_id,
        "bracket_level": bracket_level,
        "bracket_label": bracket_label,
        "bracket_score": bracket_score,
        "power_score": power_score,
    }


def _parse_played_at(raw: str | None, errors: list[str]) -> datetime:
    if not raw:
        return utcnow()
    raw_value = raw.strip()
    try:
        if len(raw_value) <= 10:
            return datetime.combine(date.fromisoformat(raw_value), datetime.min.time())
        return datetime.fromisoformat(raw_value)
    except ValueError:
        errors.append("Played at must be a valid date.")
        return utcnow()


def _player_label(player: GamePlayer | None) -> str:
    if not player:
        return "Unknown"
    return player.display_name or "Unknown"


def _game_session_payload(session: GameSession, user_id: int | None = None) -> dict[str, Any]:
    seats = sorted(session.seats or [], key=lambda s: (s.seat_number or 0, s.turn_order or 0))
    seat_payloads = []
    winner_label = None
    for seat in seats:
        assignment = seat.assignment
        player = assignment.player if assignment else None
        deck = assignment.deck if assignment else None
        is_winner = bool(session.winner_seat_id and seat.id == session.winner_seat_id)
        if is_winner:
            winner_label = _player_label(player)
        seat_payloads.append(
            {
                "seat_number": seat.seat_number,
                "turn_order": seat.seat_number,
                "player_label": _player_label(player),
                "deck_name": deck.deck_name if deck else "Unknown deck",
                "commander_name": deck.commander_name if deck else None,
                "bracket_level": deck.bracket_level if deck else None,
                "bracket_label": deck.bracket_label if deck else None,
                "bracket_score": deck.bracket_score if deck else None,
                "power_score": deck.power_score if deck else None,
                "is_winner": is_winner,
            }
        )
    played_at = session.played_at or session.created_at
    played_label = played_at.strftime("%Y-%m-%d") if played_at else "Unknown"
    played_iso = played_at.date().isoformat() if played_at else None
    can_edit = bool(user_id and session.owner_user_id == user_id)
    return {
        "id": session.id,
        "owner_user_id": session.owner_user_id,
        "can_edit": can_edit,
        "played_at_iso": played_iso,
        "played_at_label": played_label,
        "notes": session.notes or "",
        "seat_count": len(seats),
        "winner_label": winner_label,
        "win_via_combo": bool(session.win_via_combo),
        "seats": seat_payloads,
    }


def _games_summary(user_id: int) -> dict[str, int]:
    filters = _session_filters(user_id)
    total, combo_wins = (
        db.session.query(
            func.count(GameSession.id),
            func.coalesce(func.sum(case((GameSession.win_via_combo.is_(True), 1), else_=0)), 0),
        )
        .filter(*filters)
        .one()
    )
    return {"total_games": int(total or 0), "combo_wins": int(combo_wins or 0)}


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


def _session_filters(user_id: int, start_at: datetime | None = None, end_at: datetime | None = None) -> list:
    filters = [_session_visibility_filter(user_id)]
    if start_at:
        filters.append(GameSession.played_at >= start_at)
    if end_at:
        filters.append(GameSession.played_at <= end_at)
    return filters


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


def _metrics_payload(user_id: int, start_at: datetime | None = None, end_at: datetime | None = None) -> dict[str, Any]:
    filters = _session_filters(user_id, start_at, end_at)

    total_games = db.session.query(func.count(GameSession.id)).filter(*filters).scalar() or 0
    combo_wins = (
        db.session.query(func.count(GameSession.id))
        .filter(*filters, GameSession.win_via_combo.is_(True))
        .scalar()
        or 0
    )

    seat_counts = (
        db.session.query(
            GameSeat.session_id.label("session_id"),
            func.count(GameSeat.id).label("seat_count"),
        )
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters)
        .group_by(GameSeat.session_id)
        .subquery()
    )
    avg_players = db.session.query(func.avg(seat_counts.c.seat_count)).scalar()

    unique_players = (
        db.session.query(func.count(func.distinct(GamePlayer.display_name)))
        .join(GameSeatAssignment, GameSeatAssignment.player_id == GamePlayer.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters)
        .scalar()
        or 0
    )

    winners = (
        db.session.query(
            GamePlayer.display_name,
            func.count(GameSession.id).label("wins"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.player_id == GamePlayer.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.winner_seat_id == GameSeat.id)
        .filter(*filters)
        .group_by(GamePlayer.display_name)
        .order_by(func.count(GameSession.id).desc(), GamePlayer.display_name.asc())
        .limit(5)
        .all()
    )

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

    return {
        "total_games": int(total_games or 0),
        "combo_wins": int(combo_wins or 0),
        "avg_players": round(float(avg_players), 2) if avg_players else None,
        "unique_players": int(unique_players or 0),
        "top_winners": [{"label": row[0] or "Unknown", "count": int(row[1] or 0)} for row in winners],
        "top_decks": [{"label": row[0] or "Unknown deck", "count": int(row[1] or 0)} for row in top_decks],
        "top_commanders": [{"label": row[0] or "Unknown", "count": int(row[1] or 0)} for row in top_commanders],
    }



def games_landing():
    visibility_filter = _session_visibility_filter(current_user.id)
    recent_sessions = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .filter(visibility_filter)
        .order_by(GameSession.played_at.desc().nullslast(), GameSession.created_at.desc())
        .limit(6)
        .all()
    )
    recent_games = [_game_session_payload(session, current_user.id) for session in recent_sessions]
    quick_all = _metrics_payload(current_user.id)
    last30_range = _resolve_date_range({"range": "last30"})
    quick_30 = _metrics_payload(current_user.id, last30_range["start_at"], last30_range["end_at"])
    return render_template(
        "games/index.html",
        recent_games=recent_games,
        quick_all=quick_all,
        quick_30=quick_30,
    )


def games_overview():
    q = (request.args.get("q") or "").strip()
    query = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .filter(_session_visibility_filter(current_user.id))
    )
    query = _apply_notes_search(query, q)
    sessions = query.order_by(GameSession.played_at.desc().nullslast(), GameSession.created_at.desc()).all()
    games = [_game_session_payload(session, current_user.id) for session in sessions]
    summary = _games_summary(current_user.id)
    return render_template(
        "games/logs.html",
        games=games,
        summary=summary,
        search_query=q,
    )


def games_export():
    sessions = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .filter(_session_visibility_filter(current_user.id))
        .order_by(GameSession.played_at.desc().nullslast(), GameSession.created_at.desc())
        .all()
    )
    output = io.StringIO()
    writer = csv.writer(output)
    headers = [
        "game_id",
        "played_at",
        "notes",
        "win_via_combo",
        "winner_seat",
        "seat_number",
        "turn_order",
        "player_user_id",
        "player_name",
        "deck_folder_id",
        "deck_name",
        "commander_name",
        "commander_oracle_id",
        "bracket_level",
        "bracket_label",
        "bracket_score",
        "power_score",
    ]
    writer.writerow(headers)
    for session in sessions:
        played_at = session.played_at or session.created_at
        played_label = played_at.date().isoformat() if played_at else ""
        winner_seat_number = None
        for seat in session.seats or []:
            if session.winner_seat_id and seat.id == session.winner_seat_id:
                winner_seat_number = seat.seat_number
                break
        seats = sorted(session.seats or [], key=lambda s: s.seat_number or 0)
        for seat in seats:
            assignment = seat.assignment
            player = assignment.player if assignment else None
            deck = assignment.deck if assignment else None
            writer.writerow(
                [
                    session.id,
                    played_label,
                    session.notes or "",
                    "1" if session.win_via_combo else "0",
                    winner_seat_number or "",
                    seat.seat_number or "",
                    seat.turn_order or "",
                    player.user_id if player else "",
                    _player_label(player),
                    deck.folder_id if deck else "",
                    deck.deck_name if deck else "",
                    deck.commander_name if deck else "",
                    deck.commander_oracle_id if deck else "",
                    deck.bracket_level if deck else "",
                    deck.bracket_label if deck else "",
                    deck.bracket_score if deck else "",
                    deck.power_score if deck else "",
                ]
            )
    filename = f"dragonsvault-games-{date.today().isoformat()}.csv"
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def games_import():
    upload = request.files.get("import_file")
    raw_text = (request.form.get("import_csv") or "").strip()
    if upload:
        raw = upload.read()
        try:
            raw_text = raw.decode("utf-8")
        except Exception:
            raw_text = raw.decode("utf-8", errors="ignore")
    if not raw_text:
        flash("Select a game log CSV file to import.", "warning")
        return redirect(url_for("views.games_overview"))

    try:
        sample = raw_text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(raw_text), dialect=dialect)
        rows = [row for row in reader if row]
    except Exception:
        flash("Import file must be valid CSV.", "danger")
        return redirect(url_for("views.games_overview"))

    if not rows:
        flash("Import file is missing rows.", "danger")
        return redirect(url_for("views.games_overview"))

    def _norm_key(value: str | None) -> str:
        return (value or "").strip().lower()

    def _parse_int(value: str) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _parse_float(value: str) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    normalized_rows = []
    for row in rows:
        normalized = {_norm_key(k): (v or "").strip() for k, v in row.items() if k}
        normalized_rows.append(normalized)

    grouped: dict[str, list[dict]] = {}
    for index, row in enumerate(normalized_rows, start=1):
        game_key = row.get("game_id") or row.get("game_key")
        if not game_key:
            flash(f"Row {index}: missing game_id column.", "warning")
            continue
        grouped.setdefault(str(game_key), []).append(row)

    if not grouped:
        flash("No valid games found in the CSV.", "danger")
        return redirect(url_for("views.games_overview"))

    folder_ids: set[int] = set()
    for seat_rows in grouped.values():
        for row in seat_rows:
            folder_id = _parse_int(row.get("deck_folder_id") or "")
            if folder_id:
                folder_ids.add(folder_id)
    folder_map = {
        folder.id: folder
        for folder in Folder.query.filter(
            Folder.id.in_(folder_ids),
            Folder.owner_user_id == current_user.id,
        ).all()
    } if folder_ids else {}

    imported = 0
    skipped = 0
    errors: list[str] = []
    user_cache: dict[int, User | None] = {}

    for game_key, seat_rows in grouped.items():
        seat_numbers = {
            _parse_int(row.get("seat_number") or "")
            for row in seat_rows
            if row.get("seat_number")
        }
        seat_numbers = {num for num in seat_numbers if num}
        seat_count = len(seat_numbers) if seat_numbers else len(seat_rows)
        if not (2 <= seat_count <= 4):
            errors.append(f"Game {game_key}: seat count must be 2-4.")
            skipped += 1
            continue

        first_row = seat_rows[0]
        played_at_raw = first_row.get("played_at") or ""
        notes = (first_row.get("notes") or "").strip()
        win_via_combo = (first_row.get("win_via_combo") or "").strip().lower() in {"1", "true", "yes", "y"}
        winner_seat_number = _parse_int(first_row.get("winner_seat") or "")

        session_errors: list[str] = []
        played_at = _parse_played_at(str(played_at_raw or ""), session_errors)
        if session_errors:
            errors.extend([f"Game {game_key}: {msg}" for msg in session_errors])
            skipped += 1
            continue

        session = GameSession(
            owner_user_id=current_user.id,
            played_at=played_at,
            notes=notes or None,
            win_via_combo=bool(win_via_combo),
        )
        db.session.add(session)
        db.session.flush()

        seats_by_number: dict[int, GameSeat] = {}
        seat_payloads: list[dict[str, Any]] = []

        for seat_entry in seat_rows:
            seat_number = _parse_int(seat_entry.get("seat_number") or "")
            if not seat_number:
                seat_number = max(seats_by_number.keys(), default=0) + 1
            if seat_number > 4 or seat_number < 1:
                continue
            if seat_number in seats_by_number:
                continue
            turn_order = _parse_int(seat_entry.get("turn_order") or "") or seat_number
            if not turn_order or turn_order < 1 or turn_order > 4:
                turn_order = seat_number
            seat = GameSeat(
                session_id=session.id,
                seat_number=seat_number,
                turn_order=turn_order,
            )
            db.session.add(seat)
            seats_by_number[seat_number] = seat

            player_user = None
            display_name = (seat_entry.get("player_name") or "").strip() or None
            user_id = _parse_int(seat_entry.get("player_user_id") or "")
            if user_id:
                if user_id in user_cache:
                    player_user = user_cache[user_id]
                else:
                    player_user = User.query.filter(User.id == user_id).first()
                    user_cache[user_id] = player_user
            if player_user and not display_name:
                display_name = (
                    (player_user.display_name or "").strip()
                    or (player_user.username or "").strip()
                    or (player_user.email or "").strip()
                    or None
                )
            if not display_name:
                display_name = f"Player {seat_number}"

            deck_folder_id = _parse_int(seat_entry.get("deck_folder_id") or "")
            if deck_folder_id and deck_folder_id in folder_map:
                deck_snapshot = _snapshot_deck(folder_map[deck_folder_id])
            else:
                deck_snapshot = {
                    "folder_id": None,
                    "deck_name": seat_entry.get("deck_name") or "Unknown deck",
                    "commander_name": seat_entry.get("commander_name") or None,
                    "commander_oracle_id": seat_entry.get("commander_oracle_id") or None,
                    "bracket_level": seat_entry.get("bracket_level") or None,
                    "bracket_label": seat_entry.get("bracket_label") or None,
                    "bracket_score": _parse_float(seat_entry.get("bracket_score") or ""),
                    "power_score": _parse_float(seat_entry.get("power_score") or ""),
                }

            seat_payloads.append(
                {
                    "seat_number": seat_number,
                    "player_user": player_user,
                    "player_label": display_name,
                    "deck_snapshot": deck_snapshot,
                }
            )

        for payload in seat_payloads:
            seat = seats_by_number.get(payload["seat_number"])
            if not seat:
                continue
            player = GamePlayer(
                user_id=payload["player_user"].id if payload.get("player_user") else None,
                display_name=payload.get("player_label"),
            )
            db.session.add(player)
            deck_data = payload.get("deck_snapshot") or {}
            deck = GameDeck(
                session_id=session.id,
                folder_id=deck_data.get("folder_id"),
                deck_name=deck_data.get("deck_name") or "Unknown deck",
                commander_name=deck_data.get("commander_name"),
                commander_oracle_id=deck_data.get("commander_oracle_id"),
                bracket_level=deck_data.get("bracket_level"),
                bracket_label=deck_data.get("bracket_label"),
                bracket_score=deck_data.get("bracket_score"),
                power_score=deck_data.get("power_score"),
            )
            db.session.add(deck)
            assignment = GameSeatAssignment(
                session_id=session.id,
                seat=seat,
                player=player,
                deck=deck,
            )
            db.session.add(assignment)

        if winner_seat_number and winner_seat_number in seats_by_number:
            session.winner_seat = seats_by_number[winner_seat_number]
        if not session.winner_seat:
            session.win_via_combo = False

        try:
            db.session.commit()
            imported += 1
        except Exception:
            db.session.rollback()
            errors.append(f"Game {game_key}: unable to import.")
            skipped += 1

    if imported:
        flash(f"Imported {imported} game log{'s' if imported != 1 else ''}.", "success")
    if skipped:
        flash(f"Skipped {skipped} game log{'s' if skipped != 1 else ''}.", "warning")
    if errors:
        for message in errors[:5]:
            flash(message, "warning")
        if len(errors) > 5:
            flash(f"{len(errors) - 5} more import errors were skipped.", "warning")
    return redirect(url_for("views.games_overview"))


def games_metrics():
    range_ctx = _resolve_date_range(request.args)
    metrics = _metrics_payload(current_user.id, range_ctx["start_at"], range_ctx["end_at"])
    return render_template(
        "games/metrics.html",
        metrics=metrics,
        range_ctx=range_ctx,
    )


def games_players():
    member_pod_ids = [
        pod_id
        for (pod_id,) in db.session.query(GamePodMember.pod_id)
        .join(GameRosterPlayer, GameRosterPlayer.id == GamePodMember.roster_player_id)
        .filter(GameRosterPlayer.user_id == current_user.id)
        .distinct()
        .all()
    ]
    pod_filters = [GamePod.owner_user_id == current_user.id]
    if member_pod_ids:
        pod_filters.append(GamePod.id.in_(member_pod_ids))
    pods = (
        GamePod.query.options(
            selectinload(GamePod.members).selectinload(GamePodMember.roster_player).selectinload(GameRosterPlayer.user)
        )
        .filter(or_(*pod_filters))
        .order_by(func.lower(GamePod.name), GamePod.id.asc())
        .all()
    )
    pod_owner_ids = {pod.owner_user_id for pod in pods}
    managed_owner_ids = {current_user.id, *pod_owner_ids}

    owner_label_map: dict[int, str] = {
        current_user.id: (
            current_user.display_name
            or current_user.username
            or current_user.email
            or f"User {current_user.id}"
        )
    }
    other_owner_ids = {owner_id for owner_id in managed_owner_ids if owner_id != current_user.id}
    if other_owner_ids:
        for user in User.query.filter(User.id.in_(other_owner_ids)).all():
            owner_label_map[user.id] = (
                user.display_name
                or user.username
                or user.email
                or f"User {user.id}"
            )

    sorted_owner_ids = sorted(
        managed_owner_ids,
        key=lambda owner_id: (
            0 if owner_id == current_user.id else 1,
            (owner_label_map.get(owner_id) or "").lower(),
            owner_id,
        ),
    )
    roster_groups: list[dict[str, Any]] = []
    roster_owner_options: list[dict[str, Any]] = []
    for owner_id in sorted_owner_ids:
        owner_label = owner_label_map.get(owner_id) or f"User {owner_id}"
        roster_groups.append(
            {
                "owner_user_id": owner_id,
                "owner_label": owner_label,
                "is_owner": owner_id == current_user.id,
                "players": _roster_payloads_for_owner(owner_id),
                "deck_options": _accessible_deck_options(owner_id),
            }
        )
        roster_owner_options.append({"id": owner_id, "label": owner_label})

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "create_pod":
            pod_name = (request.form.get("pod_name") or "").strip()
            if not pod_name:
                flash("Enter a pod name.", "warning")
                return redirect(url_for("views.games_players"))
            existing = GamePod.query.filter_by(owner_user_id=current_user.id, name=pod_name).first()
            if existing:
                flash("Pod name already exists.", "warning")
                return redirect(url_for("views.games_players"))
            pod = GamePod(owner_user_id=current_user.id, name=pod_name)
            db.session.add(pod)
            try:
                db.session.commit()
                flash("Pod created.", "success")
            except Exception:
                db.session.rollback()
                flash("Unable to create pod.", "danger")
            return redirect(url_for("views.games_players"))

        if action == "remove_pod":
            pod_id_raw = request.form.get("pod_id")
            try:
                pod_id = parse_positive_int(pod_id_raw, field="pod")
            except ValidationError as exc:
                log_validation_error(exc, context="game_pod_remove")
                flash("Invalid pod selection.", "warning")
                return redirect(url_for("views.games_players"))
            pod = GamePod.query.filter_by(id=pod_id, owner_user_id=current_user.id).first()
            if pod:
                db.session.delete(pod)
                db.session.commit()
                flash("Pod removed.", "info")
            return redirect(url_for("views.games_players"))

        if action == "add_pod_member":
            pod_id_raw = request.form.get("pod_id")
            roster_id_raw = request.form.get("roster_player_id")
            try:
                pod_id = parse_positive_int(pod_id_raw, field="pod")
                roster_id = parse_positive_int(roster_id_raw, field="player")
            except ValidationError as exc:
                log_validation_error(exc, context="game_pod_member")
                flash("Select a pod and player.", "warning")
                return redirect(url_for("views.games_players"))
            pod = (
                GamePod.query.options(
                    selectinload(GamePod.members).selectinload(GamePodMember.roster_player)
                )
                .filter(GamePod.id == pod_id)
                .first()
            )
            if not pod:
                flash("Pod not found.", "warning")
                return redirect(url_for("views.games_players"))
            is_owner, is_member = _pod_access_flags(pod, current_user.id)
            if not (is_owner or is_member):
                flash("Pod not found.", "warning")
                return redirect(url_for("views.games_players"))
            roster_player = GameRosterPlayer.query.filter_by(id=roster_id, owner_user_id=pod.owner_user_id).first()
            if not roster_player:
                flash("Player not found.", "warning")
                return redirect(url_for("views.games_players"))
            existing = GamePodMember.query.filter_by(pod_id=pod_id, roster_player_id=roster_id).first()
            if existing:
                flash("Player already in this pod.", "info")
                return redirect(url_for("views.games_players"))
            db.session.add(GamePodMember(pod_id=pod_id, roster_player_id=roster_id))
            try:
                db.session.commit()
                flash("Player added to pod.", "success")
            except Exception:
                db.session.rollback()
                flash("Unable to add player to pod.", "danger")
            return redirect(url_for("views.games_players"))

        if action == "remove_pod_member":
            member_id_raw = request.form.get("member_id")
            try:
                member_id = parse_positive_int(member_id_raw, field="pod member")
            except ValidationError as exc:
                log_validation_error(exc, context="game_pod_member_remove")
                flash("Invalid pod member.", "warning")
                return redirect(url_for("views.games_players"))
            member = GamePodMember.query.filter_by(id=member_id).first()
            if not member:
                flash("Pod member not found.", "warning")
                return redirect(url_for("views.games_players"))
            pod = (
                GamePod.query.options(
                    selectinload(GamePod.members).selectinload(GamePodMember.roster_player)
                )
                .filter(GamePod.id == member.pod_id)
                .first()
            )
            if not pod:
                flash("Pod not found.", "warning")
                return redirect(url_for("views.games_players"))
            is_owner, is_member = _pod_access_flags(pod, current_user.id)
            if not (is_owner or is_member):
                flash("Pod member not found.", "warning")
                return redirect(url_for("views.games_players"))
            db.session.delete(member)
            db.session.commit()
            flash("Pod member removed.", "info")
            return redirect(url_for("views.games_players"))

        if action == "add_player":
            roster_owner_id = current_user.id
            roster_owner_raw = request.form.get("roster_owner_id")
            if roster_owner_raw:
                try:
                    roster_owner_id = parse_positive_int(roster_owner_raw, field="roster owner", min_value=1)
                except ValidationError as exc:
                    log_validation_error(exc, context="game_roster_owner")
                    roster_owner_id = current_user.id
            if roster_owner_id not in managed_owner_ids:
                flash("Select a valid roster owner.", "warning")
                return redirect(url_for("views.games_players"))
            kind = (request.form.get("player_kind") or "guest").strip().lower()
            identifier = (request.form.get("player_identifier") or "").strip()
            display_name = (request.form.get("display_name") or "").strip()
            if kind == "user":
                if not identifier:
                    flash("Enter a username or email.", "warning")
                else:
                    user = (
                        User.query.filter(func.lower(User.username) == identifier.lower()).first()
                        or User.query.filter(func.lower(User.email) == identifier.lower()).first()
                    )
                    if not user:
                        flash("User not found.", "warning")
                    else:
                        label = display_name or user.display_name or user.username or user.email
                        player = GameRosterPlayer(
                            owner_user_id=roster_owner_id,
                            user_id=user.id,
                            display_name=label,
                        )
                        db.session.add(player)
                        try:
                            db.session.flush()
                        except Exception:
                            db.session.rollback()
                            flash("Unable to add player.", "danger")
                            return redirect(url_for("views.games_players"))

                        auto_added = 0
                        deck_ids = (
                            db.session.query(Folder.id)
                            .join(FolderRole, FolderRole.folder_id == Folder.id)
                            .filter(
                                FolderRole.role.in_(FolderRole.DECK_ROLES),
                                Folder.owner_user_id == user.id,
                            )
                            .all()
                        )
                        for (deck_id,) in deck_ids:
                            db.session.add(
                                GameRosterDeck(
                                    roster_player_id=player.id,
                                    owner_user_id=roster_owner_id,
                                    folder_id=deck_id,
                                )
                            )
                            auto_added += 1
                        try:
                            db.session.commit()
                            if auto_added:
                                flash(f"Player added with {auto_added} deck(s).", "success")
                            else:
                                flash("Player added.", "success")
                        except Exception:
                            db.session.rollback()
                            flash("Unable to add player.", "danger")
            else:
                if not display_name:
                    flash("Enter a display name.", "warning")
                else:
                    player = GameRosterPlayer(
                        owner_user_id=roster_owner_id,
                        display_name=display_name,
                    )
                    db.session.add(player)
                    try:
                        db.session.commit()
                        flash("Guest player added.", "success")
                    except Exception:
                        db.session.rollback()
                        flash("Unable to add player.", "danger")
            return redirect(url_for("views.games_players"))

        if action == "assign_deck":
            roster_id_raw = request.form.get("roster_player_id")
            deck_id_raw = request.form.get("deck_id")
            manual_name = (request.form.get("manual_deck_name") or "").strip()
            try:
                roster_id = parse_positive_int(roster_id_raw, field="player")
            except ValidationError as exc:
                log_validation_error(exc, context="game_roster_assign")
                flash("Select a player.", "warning")
                return redirect(url_for("views.games_players"))

            roster_player = GameRosterPlayer.query.filter_by(id=roster_id).first()
            if not roster_player or roster_player.owner_user_id not in managed_owner_ids:
                flash("Player not found.", "warning")
                return redirect(url_for("views.games_players"))
            roster_owner_id = roster_player.owner_user_id

            if manual_name:
                assignment = GameRosterDeck(
                    roster_player_id=roster_id,
                    owner_user_id=roster_owner_id,
                    deck_name=manual_name,
                )
                db.session.add(assignment)
                try:
                    db.session.commit()
                    flash("Manual deck added.", "success")
                except Exception:
                    db.session.rollback()
                    flash("Unable to add manual deck.", "danger")
                return redirect(url_for("views.games_players"))

            try:
                deck_id = parse_positive_int(deck_id_raw, field="deck")
            except ValidationError as exc:
                log_validation_error(exc, context="game_roster_assign")
                flash("Select a deck.", "warning")
                return redirect(url_for("views.games_players"))

            folder = Folder.query.filter(Folder.id == deck_id).first()
            if not folder:
                flash("Deck not found.", "warning")
                return redirect(url_for("views.games_players"))
            if folder.owner_user_id != roster_owner_id:
                flash("Deck not available for this roster.", "warning")
                return redirect(url_for("views.games_players"))

            existing = GameRosterDeck.query.filter_by(roster_player_id=roster_id, folder_id=deck_id).first()
            if existing:
                flash("Deck already assigned.", "info")
                return redirect(url_for("views.games_players"))

            assignment = GameRosterDeck(
                roster_player_id=roster_id,
                owner_user_id=roster_owner_id,
                folder_id=deck_id,
            )
            db.session.add(assignment)
            try:
                db.session.commit()
                flash("Deck assigned.", "success")
            except Exception:
                db.session.rollback()
                flash("Unable to assign deck.", "danger")
            return redirect(url_for("views.games_players"))

        if action == "remove_deck":
            assignment_id_raw = request.form.get("assignment_id")
            try:
                assignment_id = parse_positive_int(assignment_id_raw, field="assignment")
            except ValidationError as exc:
                log_validation_error(exc, context="game_roster_remove")
                flash("Invalid assignment.", "warning")
                return redirect(url_for("views.games_players"))
            assignment = GameRosterDeck.query.filter_by(id=assignment_id).first()
            if not assignment:
                return redirect(url_for("views.games_players"))
            roster_player = GameRosterPlayer.query.filter_by(id=assignment.roster_player_id).first()
            if not roster_player or roster_player.owner_user_id not in managed_owner_ids:
                flash("Deck not found.", "warning")
                return redirect(url_for("views.games_players"))
            db.session.delete(assignment)
            db.session.commit()
            flash("Deck removed.", "info")
            return redirect(url_for("views.games_players"))

        if action == "remove_player":
            roster_id_raw = request.form.get("roster_player_id")
            try:
                roster_id = parse_positive_int(roster_id_raw, field="player")
            except ValidationError as exc:
                log_validation_error(exc, context="game_roster_remove_player")
                flash("Invalid player.", "warning")
                return redirect(url_for("views.games_players"))
            roster_player = GameRosterPlayer.query.filter_by(id=roster_id).first()
            if not roster_player or roster_player.owner_user_id not in managed_owner_ids:
                flash("Player not found.", "warning")
                return redirect(url_for("views.games_players"))
            db.session.delete(roster_player)
            db.session.commit()
            flash("Player removed.", "info")
            return redirect(url_for("views.games_players"))

        flash("Unknown action.", "warning")
        return redirect(url_for("views.games_players"))

    roster_label_map_by_owner: dict[int, dict[int, str]] = {}
    roster_options_by_owner: dict[int, list[dict[str, Any]]] = {}
    for owner_id in pod_owner_ids:
        owner_roster = _roster_players(owner_id)
        roster_label_map_by_owner[owner_id] = {player["id"]: player["label"] for player in owner_roster}
        roster_options_by_owner[owner_id] = [
            {"id": player["id"], "label": player["label"]}
            for player in owner_roster
        ]
    owner_label_map: dict[int, str] = {}
    if pod_owner_ids:
        for user in User.query.filter(User.id.in_(pod_owner_ids)).all():
            owner_label_map[user.id] = (
                user.display_name
                or user.username
                or user.email
                or f"User {user.id}"
            )
    pods_payloads = _pod_payloads_for_management(
        pods,
        roster_label_map_by_owner,
        roster_options_by_owner,
        owner_label_map,
        current_user.id,
    )
    has_roster_players = any(group.get("players") for group in roster_groups)

    return render_template(
        "games/players.html",
        roster_groups=roster_groups,
        roster_owner_options=roster_owner_options,
        current_owner_id=current_user.id,
        has_roster_players=has_roster_players,
        pods=pods_payloads,
    )


def game_detail(game_id: int):
    session = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .filter(GameSession.id == game_id)
        .first()
    )
    if not session:
        flash("Game session not found.", "warning")
        return redirect(url_for("views.games_overview"))
    is_participant = any(
        seat.assignment
        and seat.assignment.player
        and seat.assignment.player.user_id == current_user.id
        for seat in (session.seats or [])
    )
    if not (session.owner_user_id == current_user.id or is_participant):
        flash("Game session not found.", "warning")
        return redirect(url_for("views.games_overview"))
    game = _game_session_payload(session, current_user.id)
    can_edit = session.owner_user_id == current_user.id
    return render_template("games/detail.html", game=game, can_edit=can_edit)


def _game_form_context() -> dict[str, Any]:
    roster_players = _roster_players(current_user.id)
    roster_map = {player["id"]: player for player in roster_players}
    roster_deck_refs = {
        player["id"]: {deck["ref"] for deck in player.get("deck_options", [])}
        for player in roster_players
    }
    guest_deck_options = _accessible_deck_options(current_user.id)
    guest_deck_refs = {deck["ref"] for deck in guest_deck_options}
    pods = _pod_payloads_for_owner(current_user.id, roster_players)
    pod_member_map = {str(pod["id"]): pod["member_ids"] for pod in pods}
    return {
        "roster_players": roster_players,
        "roster_map": roster_map,
        "roster_deck_refs": roster_deck_refs,
        "guest_deck_options": guest_deck_options,
        "guest_deck_refs": guest_deck_refs,
        "pods": pods,
        "pod_member_map": pod_member_map,
    }


def _default_game_form_data(context: dict[str, Any]) -> dict[str, Any]:
    roster_players = context["roster_players"]
    pods = context["pods"]
    pod_member_map = context["pod_member_map"]

    default_pod_id = pods[0]["id"] if pods else None
    default_roster_ids: list[int] = []
    if default_pod_id:
        default_roster_ids = pod_member_map.get(str(default_pod_id)) or []
    elif roster_players:
        default_roster_ids = [player["id"] for player in roster_players]

    default_type = "roster"
    seats = {}
    for seat_number in range(1, 5):
        roster_id = (
            default_roster_ids[seat_number - 1]
            if default_type == "roster" and len(default_roster_ids) >= seat_number
            else None
        )
        seats[seat_number] = {
            "player_type": default_type,
            "roster_id": roster_id,
            "guest_name": "",
            "deck_ref": None,
            "manual_deck_name": "",
            "turn_order": seat_number,
        }

    return {
        "played_at": "",
        "notes": "",
        "pod_id": default_pod_id,
        "seat_count": 4,
        "winner_seat": None,
        "win_via_combo": False,
        "seats": seats,
    }


def _game_form_data_from_session(session: GameSession, context: dict[str, Any]) -> dict[str, Any]:
    roster_players = context["roster_players"]
    pods = context["pods"]
    roster_by_user_id = {
        player["user_id"]: player["id"] for player in roster_players if player.get("user_id")
    }
    roster_by_label = {
        (player["label"] or "").strip().lower(): player["id"]
        for player in roster_players
        if player.get("label")
    }

    seats_sorted = sorted(session.seats or [], key=lambda s: s.seat_number or 0)
    seat_count = len(seats_sorted) or 4
    winner_seat = None
    roster_ids: list[int] = []
    seats: dict[int, dict[str, Any]] = {}
    for seat in seats_sorted:
        assignment = seat.assignment
        player = assignment.player if assignment else None
        deck = assignment.deck if assignment else None
        roster_id = None
        player_type = "guest"
        guest_name = player.display_name if player else ""
        if player and player.user_id and player.user_id in roster_by_user_id:
            roster_id = roster_by_user_id[player.user_id]
            player_type = "roster"
        elif player and player.display_name:
            roster_id = roster_by_label.get(player.display_name.strip().lower())
            if roster_id:
                player_type = "roster"

        if roster_id:
            roster_ids.append(roster_id)
            guest_name = ""

        deck_ref = None
        manual_name = ""
        if deck:
            if deck.folder_id:
                deck_ref = f"folder:{deck.folder_id}"
            else:
                manual_name = deck.deck_name or ""
                if manual_name:
                    deck_ref = "manual:new"

        seats[seat.seat_number] = {
            "player_type": player_type,
            "roster_id": roster_id,
            "guest_name": guest_name or "",
            "deck_ref": deck_ref,
            "manual_deck_name": manual_name,
            "turn_order": seat.seat_number,
        }
        if session.winner_seat_id and seat.id == session.winner_seat_id:
            winner_seat = seat.seat_number

    pod_id = None
    if roster_ids:
        for pod in pods:
            if all(rid in pod["member_ids"] for rid in roster_ids):
                pod_id = pod["id"]
                break

    played_at = session.played_at or session.created_at
    played_value = played_at.date().isoformat() if played_at else ""
    return {
        "played_at": played_value,
        "notes": session.notes or "",
        "pod_id": pod_id,
        "seat_count": seat_count,
        "winner_seat": winner_seat,
        "win_via_combo": bool(session.win_via_combo),
        "seats": seats,
    }


def _parse_game_form(context: dict[str, Any]) -> dict[str, Any]:
    roster_players = context["roster_players"]
    roster_map = context["roster_map"]
    roster_deck_refs = context["roster_deck_refs"]
    guest_deck_refs = context["guest_deck_refs"]
    pods = context["pods"]

    errors: list[str] = []
    seat_count_raw = request.form.get("seat_count")
    try:
        seat_count = parse_positive_int(seat_count_raw, field="player count", min_value=2)
    except ValidationError as exc:
        log_validation_error(exc, context="game_create")
        errors.append("Player count must be between 2 and 4.")
        seat_count = 4
    if seat_count > 4:
        errors.append("Player count must be between 2 and 4.")
        seat_count = 4

    notes = (request.form.get("notes") or "").strip()
    played_at_raw = request.form.get("played_at")
    played_at = _parse_played_at(played_at_raw, errors)
    win_via_combo = request.form.get("win_via_combo") in {"1", "true", "on", "yes"}
    try:
        winner_seat = parse_optional_positive_int(request.form.get("winner_seat"), field="winner seat", min_value=1)
    except ValidationError as exc:
        log_validation_error(exc, context="game_create")
        errors.append("Winner seat must be 1-4.")
        winner_seat = None
    if winner_seat and winner_seat > seat_count:
        errors.append("Winner seat must be within the player count.")
        winner_seat = None
        win_via_combo = False
    if not winner_seat:
        win_via_combo = False

    pod_id_raw = request.form.get("pod_id")
    pod_id = None
    pod_member_ids: set[int] = set()
    if pod_id_raw:
        try:
            pod_id = parse_positive_int(pod_id_raw, field="pod")
        except ValidationError as exc:
            log_validation_error(exc, context="game_create")
            errors.append("Select a valid pod.")
            pod_id = None
        if pod_id:
            pod = GamePod.query.filter_by(id=pod_id, owner_user_id=current_user.id).first()
            if not pod:
                errors.append("Selected pod not found.")
                pod_id = None
            else:
                pod_member_ids = {
                    member.roster_player_id
                    for member in (pod.members or [])
                    if member.roster_player_id
                }

    form_data = {
        "played_at": played_at_raw or "",
        "notes": notes,
        "pod_id": pod_id_raw,
        "seat_count": seat_count,
        "winner_seat": winner_seat,
        "win_via_combo": win_via_combo,
        "seats": {},
    }

    seat_payloads: list[dict[str, Any]] = []
    deck_ids_to_load: set[int] = set()
    manual_deck_ids_to_load: set[int] = set()

    for seat_number in range(1, seat_count + 1):
        player_type = (request.form.get(f"seat_{seat_number}_player_type") or "").strip().lower()
        roster_id_raw = request.form.get(f"seat_{seat_number}_roster_id")
        guest_name = (request.form.get(f"seat_{seat_number}_guest_name") or "").strip()
        deck_ref_raw = request.form.get(f"seat_{seat_number}_deck_ref")
        manual_deck_name = (request.form.get(f"seat_{seat_number}_manual_deck_name") or "").strip()

        form_data["seats"][seat_number] = {
            "player_type": player_type or "roster",
            "roster_id": roster_id_raw,
            "guest_name": guest_name,
            "deck_ref": deck_ref_raw,
            "manual_deck_name": manual_deck_name,
            "turn_order": seat_number,
        }

        deck_kind = None
        deck_lookup_id = None
        if manual_deck_name:
            deck_kind = "manual_entry"
        elif deck_ref_raw == "manual:new":
            errors.append(f"Seat {seat_number}: enter a manual deck name.")
        else:
            deck_kind, deck_lookup_id = _parse_deck_ref(deck_ref_raw, seat_number=seat_number, errors=errors)

        turn_order = seat_number

        if player_type not in {"roster", "guest"}:
            errors.append(f"Seat {seat_number}: select pod or guest.")

        roster_player = None
        player_user = None
        player_label = None
        if player_type == "roster":
            try:
                roster_id = parse_positive_int(roster_id_raw, field="player", min_value=1)
            except ValidationError as exc:
                log_validation_error(exc, context="game_create")
                errors.append(f"Seat {seat_number}: select a pod player.")
                roster_id = None
            roster_player = roster_map.get(roster_id) if roster_id else None
            if not roster_player:
                errors.append(f"Seat {seat_number}: pod player not found.")
            else:
                if pod_id and roster_id not in pod_member_ids:
                    errors.append(f"Seat {seat_number}: player not in selected pod.")
                player_label = roster_player.get("label")
                player_user = (
                    User.query.filter(User.id == roster_player.get("user_id")).first()
                    if roster_player.get("user_id")
                    else None
                )
                if deck_kind in {"manual", "folder"} and deck_ref_raw not in roster_deck_refs.get(roster_player["id"], set()):
                    errors.append(f"Seat {seat_number}: deck not assigned to this player.")
        else:
            if not guest_name:
                errors.append(f"Seat {seat_number}: enter a guest name.")
            player_label = guest_name
            if deck_kind == "manual_entry":
                pass
            elif deck_kind is None:
                pass
            elif deck_kind != "folder":
                errors.append(f"Seat {seat_number}: deck not available.")
            elif deck_ref_raw not in guest_deck_refs:
                errors.append(f"Seat {seat_number}: deck not available.")

        if deck_kind == "folder" and deck_lookup_id:
            deck_ids_to_load.add(deck_lookup_id)
        if deck_kind == "manual" and deck_lookup_id:
            manual_deck_ids_to_load.add(deck_lookup_id)

        seat_payloads.append(
            {
                "seat_number": seat_number,
                "turn_order": turn_order,
                "player_type": player_type,
                "player_label": player_label,
                "player_user": player_user,
                "deck_kind": deck_kind,
                "deck_lookup_id": deck_lookup_id,
                "manual_deck_name": manual_deck_name,
            }
        )

    return {
        "errors": errors,
        "seat_count": seat_count,
        "notes": notes,
        "played_at": played_at,
        "played_at_raw": played_at_raw or "",
        "winner_seat": winner_seat,
        "win_via_combo": win_via_combo,
        "pod_id": pod_id,
        "form_data": form_data,
        "seat_payloads": seat_payloads,
        "deck_ids_to_load": deck_ids_to_load,
        "manual_deck_ids_to_load": manual_deck_ids_to_load,
    }


def _persist_game_session(
    session: GameSession,
    seat_payloads: list[dict[str, Any]],
    folders_by_id: dict[int, Folder],
    manual_decks_by_id: dict[int, GameRosterDeck],
    winner_seat: int | None,
) -> None:
    seats_by_number: dict[int, GameSeat] = {}
    for payload in seat_payloads:
        seat = GameSeat(
            session_id=session.id,
            seat_number=payload["seat_number"],
            turn_order=payload["turn_order"],
        )
        db.session.add(seat)
        seats_by_number[seat.seat_number] = seat
    db.session.flush()

    if winner_seat:
        winner = seats_by_number.get(winner_seat)
        if winner:
            session.winner_seat = winner
        else:
            session.win_via_combo = False

    for payload in seat_payloads:
        player_label = payload["player_label"] or "Guest"
        player_user = payload["player_user"]

        player = GamePlayer(user_id=getattr(player_user, "id", None), display_name=player_label)
        db.session.add(player)

        deck_kind = payload.get("deck_kind")
        deck_id = payload.get("deck_lookup_id")
        if deck_kind == "folder":
            folder = folders_by_id.get(deck_id)
            deck_snapshot = _snapshot_deck(folder)
        elif deck_kind == "manual_entry":
            deck_snapshot = {
                "folder_id": None,
                "deck_name": payload.get("manual_deck_name") or "Deck",
                "commander_name": None,
                "commander_oracle_id": None,
                "bracket_level": None,
                "bracket_label": None,
                "bracket_score": None,
                "power_score": None,
            }
        else:
            manual = manual_decks_by_id.get(deck_id)
            deck_snapshot = {
                "folder_id": None,
                "deck_name": (manual.deck_name if manual else None) or "Deck",
                "commander_name": None,
                "commander_oracle_id": None,
                "bracket_level": None,
                "bracket_label": None,
                "bracket_score": None,
                "power_score": None,
            }
        deck = GameDeck(
            session_id=session.id,
            folder_id=deck_snapshot["folder_id"],
            deck_name=deck_snapshot["deck_name"],
            commander_name=deck_snapshot["commander_name"],
            commander_oracle_id=deck_snapshot["commander_oracle_id"],
            bracket_level=deck_snapshot["bracket_level"],
            bracket_label=deck_snapshot["bracket_label"],
            bracket_score=deck_snapshot["bracket_score"],
            power_score=deck_snapshot["power_score"],
        )
        db.session.add(deck)

        assignment = GameSeatAssignment(
            session_id=session.id,
            seat=seats_by_number[payload["seat_number"]],
            player=player,
            deck=deck,
        )
        db.session.add(assignment)


def games_new():
    context = _game_form_context()
    roster_players = context["roster_players"]
    roster_map = context["roster_map"]
    roster_deck_refs = context["roster_deck_refs"]
    guest_deck_options = context["guest_deck_options"]
    guest_deck_refs = context["guest_deck_refs"]
    pods = context["pods"]
    pod_member_map = context["pod_member_map"]
    form_data = _default_game_form_data(context)

    if request.method == "GET":
        return render_template(
            "games/new.html",
            roster_players=roster_players,
            roster_deck_map=roster_map,
            guest_deck_options=guest_deck_options,
            pods=pods,
            pod_member_map=pod_member_map,
            form_data=form_data,
            is_edit=False,
            game_id=None,
        )

    parsed = _parse_game_form(context)
    errors = parsed["errors"]
    form_data = parsed["form_data"]
    seat_payloads = parsed["seat_payloads"]
    deck_ids_to_load = parsed["deck_ids_to_load"]
    manual_deck_ids_to_load = parsed["manual_deck_ids_to_load"]
    winner_seat = parsed["winner_seat"]
    win_via_combo = parsed["win_via_combo"]
    notes = parsed["notes"]
    played_at = parsed["played_at"]

    folders_by_id = (
        {folder.id: folder for folder in Folder.query.filter(Folder.id.in_(deck_ids_to_load)).all()}
        if deck_ids_to_load
        else {}
    )

    manual_decks_by_id = (
        {
            deck.id: deck
            for deck in GameRosterDeck.query.filter(
                GameRosterDeck.id.in_(manual_deck_ids_to_load),
                GameRosterDeck.owner_user_id == current_user.id,
            ).all()
        }
        if manual_deck_ids_to_load
        else {}
    )

    for payload in seat_payloads:
        deck_kind = payload.get("deck_kind")
        deck_id = payload.get("deck_lookup_id")
        if deck_kind == "folder" and deck_id and deck_id not in folders_by_id:
            errors.append("Selected deck is not accessible.")
        if deck_kind == "manual" and deck_id and deck_id not in manual_decks_by_id:
            errors.append("Selected manual deck is not accessible.")

    if errors:
        for message in errors:
            flash(message, "warning")
        return render_template(
            "games/new.html",
            roster_players=roster_players,
            roster_deck_map=roster_map,
            guest_deck_options=guest_deck_options,
            pods=pods,
            pod_member_map=pod_member_map,
            form_data=form_data,
            is_edit=False,
            game_id=None,
        )

    session = GameSession(
        owner_user_id=current_user.id,
        played_at=played_at,
        notes=notes or None,
        win_via_combo=bool(win_via_combo),
    )
    db.session.add(session)
    db.session.flush()
    _persist_game_session(
        session,
        seat_payloads,
        folders_by_id,
        manual_decks_by_id,
        winner_seat,
    )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to save the game right now.", "danger")
        return render_template(
            "games/new.html",
            roster_players=roster_players,
            roster_deck_map=roster_map,
            guest_deck_options=guest_deck_options,
            pods=pods,
            pod_member_map=pod_member_map,
            form_data=form_data,
        )

    flash("Game logged.", "success")
    return redirect(url_for("views.games_detail", game_id=session.id))


def games_edit(game_id: int):
    session = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .filter(GameSession.id == game_id, GameSession.owner_user_id == current_user.id)
        .first()
    )
    if not session:
        flash("Game session not found.", "warning")
        return redirect(url_for("views.games_overview"))

    context = _game_form_context()
    roster_players = context["roster_players"]
    roster_map = context["roster_map"]
    guest_deck_options = context["guest_deck_options"]
    pods = context["pods"]
    pod_member_map = context["pod_member_map"]

    if request.method == "GET":
        form_data = _game_form_data_from_session(session, context)
        return render_template(
            "games/new.html",
            roster_players=roster_players,
            roster_deck_map=roster_map,
            guest_deck_options=guest_deck_options,
            pods=pods,
            pod_member_map=pod_member_map,
            form_data=form_data,
            is_edit=True,
            game_id=session.id,
        )

    parsed = _parse_game_form(context)
    errors = parsed["errors"]
    form_data = parsed["form_data"]
    seat_payloads = parsed["seat_payloads"]
    deck_ids_to_load = parsed["deck_ids_to_load"]
    manual_deck_ids_to_load = parsed["manual_deck_ids_to_load"]
    winner_seat = parsed["winner_seat"]
    win_via_combo = parsed["win_via_combo"]
    notes = parsed["notes"]
    played_at = parsed["played_at"]

    folders_by_id = (
        {folder.id: folder for folder in Folder.query.filter(Folder.id.in_(deck_ids_to_load)).all()}
        if deck_ids_to_load
        else {}
    )

    manual_decks_by_id = (
        {
            deck.id: deck
            for deck in GameRosterDeck.query.filter(
                GameRosterDeck.id.in_(manual_deck_ids_to_load),
                GameRosterDeck.owner_user_id == current_user.id,
            ).all()
        }
        if manual_deck_ids_to_load
        else {}
    )

    for payload in seat_payloads:
        deck_kind = payload.get("deck_kind")
        deck_id = payload.get("deck_lookup_id")
        if deck_kind == "folder" and deck_id and deck_id not in folders_by_id:
            errors.append("Selected deck is not accessible.")
        if deck_kind == "manual" and deck_id and deck_id not in manual_decks_by_id:
            errors.append("Selected manual deck is not accessible.")

    if errors:
        for message in errors:
            flash(message, "warning")
        return render_template(
            "games/new.html",
            roster_players=roster_players,
            roster_deck_map=roster_map,
            guest_deck_options=guest_deck_options,
            pods=pods,
            pod_member_map=pod_member_map,
            form_data=form_data,
            is_edit=True,
            game_id=session.id,
        )

    session.played_at = played_at
    session.notes = notes or None
    session.win_via_combo = bool(win_via_combo)
    session.winner_seat = None
    session.winner_seat_id = None

    existing_assignments = GameSeatAssignment.query.filter_by(session_id=session.id).all()
    player_ids = {assignment.player_id for assignment in existing_assignments if assignment.player_id}
    for assignment in existing_assignments:
        db.session.delete(assignment)
    for seat in session.seats:
        db.session.delete(seat)
    for deck in session.decks:
        db.session.delete(deck)
    if player_ids:
        GamePlayer.query.filter(GamePlayer.id.in_(player_ids)).delete(synchronize_session=False)
    db.session.flush()

    _persist_game_session(
        session,
        seat_payloads,
        folders_by_id,
        manual_decks_by_id,
        winner_seat,
    )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to update the game right now.", "danger")
        return render_template(
            "games/new.html",
            roster_players=roster_players,
            roster_deck_map=roster_map,
            guest_deck_options=guest_deck_options,
            pods=pods,
            pod_member_map=pod_member_map,
            form_data=form_data,
            is_edit=True,
            game_id=session.id,
        )

    flash("Game updated.", "success")
    return redirect(url_for("views.games_detail", game_id=session.id))


__all__ = [
    "games_landing",
    "games_overview",
    "games_export",
    "games_import",
    "games_metrics",
    "games_players",
    "games_new",
    "games_edit",
    "game_detail",
]
