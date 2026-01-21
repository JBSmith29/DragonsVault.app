"""Commander game tracking service layer."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import csv
import io
from typing import Any
from functools import lru_cache
from urllib.parse import quote

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import and_, case, func, or_, text
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
from services import scryfall_cache as sc
from utils.time import utcnow
from utils.validation import ValidationError, log_validation_error, parse_optional_positive_int, parse_positive_int


def _accessible_deck_options(owner_user_id: int | None = None) -> list[dict[str, Any]]:
    query = (
        db.session.query(
            Folder.id,
            Folder.name,
            Folder.commander_name,
            Folder.owner,
            Folder.is_proxy,
        )
        .outerjoin(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(
            or_(
                FolderRole.role.in_(FolderRole.DECK_ROLES),
                Folder.category == Folder.CATEGORY_DECK,
            )
        )
    )
    if owner_user_id is not None:
        query = query.filter(Folder.owner_user_id == owner_user_id)
    rows = (
        query.group_by(Folder.id, Folder.name, Folder.commander_name, Folder.owner, Folder.is_proxy)
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
        if row.is_proxy:
            label = f"{label} · Proxy"
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


@lru_cache(maxsize=1024)
def _oracle_image(oracle_id: str | None) -> str | None:
    if not oracle_id:
        return None
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return None
    if not prints:
        return None
    pr = prints[0]
    image_uris = pr.get("image_uris") or {}
    if not image_uris:
        faces = pr.get("card_faces") or []
        if faces:
            image_uris = (faces[0] or {}).get("image_uris") or {}
    return image_uris.get("normal") or image_uris.get("large") or image_uris.get("small")


@lru_cache(maxsize=1024)
def _oracle_name_from_id(oracle_id: str | None) -> str | None:
    if not oracle_id:
        return None
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return None
    for pr in prints:
        name = (pr.get("name") or "").strip()
        if name:
            return name
    return None


def _find_deck_by_name(owner_user_id: int, deck_name: str | None) -> Folder | None:
    if not deck_name:
        return None
    normalized = deck_name.strip().lower()
    if not normalized:
        return None
    matches = (
        Folder.query.join(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(
            FolderRole.role.in_(FolderRole.DECK_ROLES),
            Folder.owner_user_id == owner_user_id,
            func.lower(Folder.name) == normalized,
        )
        .limit(2)
        .all()
    )
    return matches[0] if len(matches) == 1 else None


def _parse_played_at(raw: str | None, errors: list[str]) -> datetime:
    if not raw:
        return utcnow()
    raw_value = raw.strip()
    try:
        if len(raw_value) <= 10:
            return datetime.combine(date.fromisoformat(raw_value), datetime.min.time())
        return datetime.fromisoformat(raw_value)
    except ValueError:
        try:
            if raw_value.endswith("Z"):
                return datetime.fromisoformat(raw_value[:-1] + "+00:00")
        except ValueError:
            pass
        if "T" in raw_value:
            try:
                return datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            except ValueError:
                pass
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw_value, fmt)
            except ValueError:
                continue
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
        commander_oracle_id = deck.commander_oracle_id if deck else None
        commander_image = _oracle_image(commander_oracle_id)
        if not commander_image and deck and deck.commander_name:
            commander_image = (
                "https://api.scryfall.com/cards/named?format=image&version=normal&exact="
                + quote(deck.commander_name)
            )
        seat_payloads.append(
            {
                "seat_number": seat.seat_number,
                "turn_order": seat.turn_order,
                "player_label": _player_label(player),
                "deck_name": deck.deck_name if deck else "Unknown deck",
                "commander_name": deck.commander_name if deck else None,
                "commander_oracle_id": commander_oracle_id,
                "commander_image": commander_image,
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


def _game_csv_headers_wide(include_game_id: bool = True) -> list[str]:
    headers = [
        "played_at",
        "notes",
        "win_via_combo",
        "winner_seat",
        "seat_count",
    ]
    if include_game_id:
        headers.insert(0, "game_id")
    for seat_number in range(1, 5):
        headers.extend(
            [
                f"seat_{seat_number}_player_name",
                f"seat_{seat_number}_player_user_id",
                f"seat_{seat_number}_deck_name",
                f"seat_{seat_number}_deck_folder_id",
                f"seat_{seat_number}_commander_name",
                f"seat_{seat_number}_commander_oracle_id",
                f"seat_{seat_number}_bracket_level",
                f"seat_{seat_number}_bracket_label",
                f"seat_{seat_number}_bracket_score",
                f"seat_{seat_number}_power_score",
                f"seat_{seat_number}_turn_order",
            ]
        )
    return headers


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


def _metrics_payload(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    filters = _session_filters(user_id, start_at, end_at, scope=scope)

    total_games = db.session.query(func.count(GameSession.id)).filter(*filters).scalar() or 0
    combo_wins = (
        db.session.query(func.count(GameSession.id))
        .filter(*filters, GameSession.win_via_combo.is_(True))
        .scalar()
        or 0
    )
    combo_rate = round((combo_wins / total_games) * 100, 1) if total_games else 0

    seat_counts = _seat_counts_subquery(filters)
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
        _canonical_player_identity(row.user_id, row.display_name, scope)[0]
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
            key, label = _canonical_player_identity(row.user_id, row.display_name, scope)
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


def _metrics_games(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
    sessions = (
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
        .all()
    )
    return [_game_session_payload(session, user_id) for session in sessions]


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


def _top_players_by_plays(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 5,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
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
        key, label = _canonical_player_identity(row.user_id, row.display_name, scope)
        entry = merged.setdefault(key, {"label": label, "count": 0})
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
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
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
        key, label = _canonical_player_identity(row.user_id, row.display_name, scope)
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
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
    query = (
        db.session.query(
            GameDeck.deck_name,
            func.count(GameSeatAssignment.id).label("plays"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
    )
    player_filter = _player_key_filter(player_key, scope=scope)
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
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
    query = (
        db.session.query(
            GameDeck.commander_name,
            func.count(GameSeatAssignment.id).label("plays"),
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
    )
    player_filter = _player_key_filter(player_key, scope=scope)
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
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
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
    player_filter = _player_key_filter(player_key, scope=scope)
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
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
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
    player_filter = _player_key_filter(player_key, scope=scope)
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
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
    seat_counts = _seat_counts_subquery(filters)
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
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
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
        key, label = _canonical_player_identity(row.user_id, row.display_name, scope)
        if key not in options_map:
            options_map[key] = {"key": key, "label": label}
    options = list(options_map.values())
    options.sort(key=lambda item: (item["label"] or "").lower())
    return options


def _deck_options(
    user_id: int,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
    rows = (
        db.session.query(
            GameDeck.folder_id,
            GameDeck.deck_name,
            GameDeck.commander_name,
        )
        .join(GameSeatAssignment, GameSeatAssignment.deck_id == GameDeck.id)
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .join(GameSession, GameSession.id == GameSeat.session_id)
        .filter(*filters)
        .distinct()
        .all()
    )
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
    player_filter = _player_key_filter(player_key, scope=scope)
    if player_filter is None:
        return None
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
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
    player_filter = _player_key_filter(player_key, scope=scope)
    if player_filter is None:
        return []
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
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
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
    
    # Get all player records first, then merge by canonical identity
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
    
    # Merge by canonical player identity to avoid duplicates
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key, label = _canonical_player_identity(row.user_id, row.display_name, scope)
        if key in merged:
            # Merge stats for the same canonical player
            merged[key]["plays"] += int(row.plays or 0)
            merged[key]["wins"] += int(row.wins or 0)
        else:
            merged[key] = {
                "key": key,
                "label": label,
                "plays": int(row.plays or 0),
                "wins": int(row.wins or 0)
            }
    
    # Calculate win rates and sort
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
    limit: int = 6,
    player_key: str | None = None,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filters = _session_filters(user_id, start_at, end_at, scope=scope)
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
    player_filter = _player_key_filter(player_key, scope=scope)
    if player_filter is not None:
        query = query.join(GamePlayer, GamePlayer.id == GameSeatAssignment.player_id).filter(player_filter)
    rows = (
        query.filter(*filters)
        .group_by(GameDeck.deck_name)
        .order_by(plays_expr.desc(), GameDeck.deck_name.asc())
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
                "label": row.deck_name or "Unknown deck",
                "plays": plays,
                "wins": wins,
                "win_rate": win_rate,
            }
        )
    return results



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


def games_dashboard():
    """New unified dashboard with better UX and admin controls."""
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
        .limit(8)
        .all()
    )
    recent_games = [_game_session_payload(session, current_user.id) for session in recent_sessions]
    
    # Cache metrics for better performance
    from extensions import cache
    cache_key = f"user_metrics_{current_user.id}"
    quick_all = cache.get(cache_key)
    if quick_all is None:
        quick_all = _metrics_payload(current_user.id)
        cache.set(cache_key, quick_all, timeout=300)  # 5 minutes
    
    last30_range = _resolve_date_range({"range": "last30"})
    cache_key_30 = f"user_metrics_30_{current_user.id}"
    quick_30 = cache.get(cache_key_30)
    if quick_30 is None:
        quick_30 = _metrics_payload(current_user.id, last30_range["start_at"], last30_range["end_at"])
        cache.set(cache_key_30, quick_30, timeout=300)  # 5 minutes
    
    return render_template(
        "games/dashboard.html",
        recent_games=recent_games,
        quick_all=quick_all,
        quick_30=quick_30,
    )


def games_admin():
    """Admin-only dashboard for system-wide game management."""
    from models import User
    
    # Check admin permissions
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for("views.games_dashboard"))
    
    # Get system-wide statistics
    total_games = db.session.query(func.count(GameSession.id)).scalar() or 0
    total_users = db.session.query(func.count(User.id)).scalar() or 0
    
    # Games today
    today = date.today()
    games_today = (
        db.session.query(func.count(GameSession.id))
        .filter(func.date(GameSession.played_at) == today)
        .scalar() or 0
    )
    
    # Average games per user
    avg_games_per_user = round(total_games / total_users, 1) if total_users > 0 else 0
    
    # Total pods
    total_pods = db.session.query(func.count(GamePod.id)).scalar() or 0
    
    # System combo rate
    combo_wins = (
        db.session.query(func.count(GameSession.id))
        .filter(GameSession.win_via_combo.is_(True))
        .scalar() or 0
    )
    combo_rate = round((combo_wins / total_games) * 100, 1) if total_games > 0 else 0
    
    system_stats = {
        "total_games": total_games,
        "total_users": total_users,
        "games_today": games_today,
        "avg_games_per_user": avg_games_per_user,
        "total_pods": total_pods,
        "combo_rate": combo_rate,
    }
    
    # Recent games for admin review
    recent_sessions = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .order_by(GameSession.created_at.desc())
        .limit(20)
        .all()
    )
    recent_games = [_game_session_payload(session, current_user.id) for session in recent_sessions]
    
    return render_template(
        "games/admin.html",
        system_stats=system_stats,
        recent_games=recent_games,
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
    headers = _game_csv_headers_wide()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for session in sessions:
        played_at = session.played_at or session.created_at
        played_label = played_at.date().isoformat() if played_at else ""
        winner_seat_number = None
        for seat in session.seats or []:
            if session.winner_seat_id and seat.id == session.winner_seat_id:
                winner_seat_number = seat.seat_number
                break
        row = {header: "" for header in headers}
        row["game_id"] = session.id
        row["played_at"] = played_label
        row["notes"] = session.notes or ""
        row["win_via_combo"] = "1" if session.win_via_combo else "0"
        row["winner_seat"] = winner_seat_number or ""
        row["seat_count"] = len(session.seats or [])
        seats = sorted(session.seats or [], key=lambda s: s.seat_number or 0)
        for seat in seats:
            if not seat.seat_number or seat.seat_number < 1 or seat.seat_number > 4:
                continue
            assignment = seat.assignment
            player = assignment.player if assignment else None
            deck = assignment.deck if assignment else None
            prefix = f"seat_{seat.seat_number}_"
            row[f"{prefix}player_name"] = _player_label(player)
            row[f"{prefix}player_user_id"] = player.user_id if player and player.user_id else ""
            row[f"{prefix}deck_name"] = deck.deck_name if deck else ""
            row[f"{prefix}deck_folder_id"] = deck.folder_id if deck and deck.folder_id else ""
            row[f"{prefix}commander_name"] = deck.commander_name if deck else ""
            row[f"{prefix}commander_oracle_id"] = deck.commander_oracle_id if deck else ""
            row[f"{prefix}bracket_level"] = deck.bracket_level if deck else ""
            row[f"{prefix}bracket_label"] = deck.bracket_label if deck else ""
            row[f"{prefix}bracket_score"] = (
                deck.bracket_score if deck and deck.bracket_score is not None else ""
            )
            row[f"{prefix}power_score"] = deck.power_score if deck and deck.power_score is not None else ""
            row[f"{prefix}turn_order"] = seat.turn_order or ""
        writer.writerow(row)
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

    def _truthy(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "y"}

    def _get_seat_value(row: dict[str, str], seat_number: int, key: str) -> str:
        for prefix in (f"seat_{seat_number}_", f"seat{seat_number}_"):
            value = row.get(f"{prefix}{key}")
            if value is not None:
                return value
        return ""

    normalized_rows = []
    for row in rows:
        normalized = {_norm_key(k): (v or "").strip() for k, v in row.items() if k}
        if normalized:
            normalized_rows.append(normalized)

    if not normalized_rows:
        flash("Import file is missing rows.", "danger")
        return redirect(url_for("views.games_overview"))

    key_set = set()
    for row in normalized_rows:
        key_set.update(row.keys())
    wide_format = any(
        key.startswith("seat_1_") or key.startswith("seat1_")
        for key in key_set
    )

    def _build_commander_lookup(owner_user_id: int) -> tuple[dict[str, list[Folder]], dict[str, list[Folder]]]:
        decks = (
            Folder.query.join(FolderRole, FolderRole.folder_id == Folder.id)
            .filter(
                FolderRole.role.in_(FolderRole.DECK_ROLES),
                Folder.owner_user_id == owner_user_id,
            )
            .all()
        )
        by_oracle: dict[str, list[Folder]] = {}
        by_name: dict[str, list[Folder]] = {}
        for deck in decks:
            if deck.commander_oracle_id:
                by_oracle.setdefault(deck.commander_oracle_id, []).append(deck)
            if deck.commander_name:
                by_name.setdefault(deck.commander_name.strip().lower(), []).append(deck)
        return by_oracle, by_name

    def _match_commander_deck(
        lookup: tuple[dict[str, list[Folder]], dict[str, list[Folder]]],
        commander_oracle_id: str | None,
        commander_name: str | None,
    ) -> Folder | None:
        by_oracle, by_name = lookup
        if commander_oracle_id:
            candidates = by_oracle.get(commander_oracle_id, [])
            if len(candidates) == 1:
                return candidates[0]
        if commander_name:
            candidates = by_name.get(commander_name.strip().lower(), [])
            if len(candidates) == 1:
                return candidates[0]
        return None

    def _apply_manual_name(snapshot: dict[str, Any], manual_name: str | None) -> dict[str, Any]:
        name = (manual_name or "").strip()
        if name:
            snapshot["deck_name"] = name
        return snapshot

    commander_lookup = _build_commander_lookup(current_user.id)

    def _build_folder_map(seat_rows: list[dict[str, str]]) -> dict[int, Folder]:
        folder_ids: set[int] = set()
        for seat_row in seat_rows:
            for seat_number in range(1, 5):
                folder_id = _parse_int(_get_seat_value(seat_row, seat_number, "deck_folder_id"))
                if folder_id:
                    folder_ids.add(folder_id)
        if not folder_ids:
            return {}
        return {
            folder.id: folder
            for folder in Folder.query.filter(
                Folder.id.in_(folder_ids),
                Folder.owner_user_id == current_user.id,
            ).all()
        }

    def _import_wide_rows(seat_rows: list[dict[str, str]]):
        imported = 0
        skipped = 0
        errors: list[str] = []
        folder_map = _build_folder_map(seat_rows)
        user_cache: dict[int, User | None] = {}

        for index, row in enumerate(seat_rows, start=1):
            game_key = row.get("game_id") or f"row-{index}"
            notes = (row.get("notes") or "").strip()
            win_via_combo = _truthy(row.get("win_via_combo"))
            winner_seat_number = _parse_int(row.get("winner_seat") or "")
            played_at_raw = row.get("played_at") or ""

            session_errors: list[str] = []
            played_at = _parse_played_at(str(played_at_raw or ""), session_errors)
            if session_errors:
                errors.extend([f"Game {game_key}: {msg}" for msg in session_errors])
                skipped += 1
                continue

            seat_payloads: list[dict[str, Any]] = []
            for seat_number in range(1, 5):
                player_name = _get_seat_value(row, seat_number, "player_name")
                player_user_id = _parse_int(_get_seat_value(row, seat_number, "player_user_id"))
                deck_name = _get_seat_value(row, seat_number, "deck_name")
                deck_folder_id = _parse_int(_get_seat_value(row, seat_number, "deck_folder_id"))
                commander_name = _get_seat_value(row, seat_number, "commander_name")
                commander_oracle_id = _get_seat_value(row, seat_number, "commander_oracle_id")
                bracket_level = _get_seat_value(row, seat_number, "bracket_level")
                bracket_label = _get_seat_value(row, seat_number, "bracket_label")
                bracket_score = _parse_float(_get_seat_value(row, seat_number, "bracket_score"))
                power_score = _parse_float(_get_seat_value(row, seat_number, "power_score"))
                turn_order = _parse_int(_get_seat_value(row, seat_number, "turn_order")) or seat_number
                if not turn_order or turn_order < 1 or turn_order > 4:
                    turn_order = seat_number

                has_data = any(
                    [
                        player_name,
                        player_user_id,
                        deck_name,
                        deck_folder_id,
                        commander_name,
                        commander_oracle_id,
                    ]
                )
                if not has_data:
                    continue

                player_user = None
                if player_user_id:
                    if player_user_id in user_cache:
                        player_user = user_cache[player_user_id]
                    else:
                        player_user = User.query.filter(User.id == player_user_id).first()
                        user_cache[player_user_id] = player_user
                display_name = player_name or None
                if player_user and not display_name:
                    display_name = (
                        (player_user.display_name or "").strip()
                        or (player_user.username or "").strip()
                        or (player_user.email or "").strip()
                        or None
                    )
                if not display_name:
                    display_name = f"Player {seat_number}"

                if deck_folder_id and deck_folder_id in folder_map:
                    deck_snapshot = _snapshot_deck(folder_map[deck_folder_id])
                    deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
                else:
                    owner_id = player_user.id if player_user else current_user.id
                    name_match = _find_deck_by_name(owner_id, deck_name)
                    if name_match:
                        deck_snapshot = _snapshot_deck(name_match)
                        deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
                    else:
                        matched_deck = _match_commander_deck(
                            commander_lookup,
                            commander_oracle_id or None,
                            commander_name or None,
                        )
                        if matched_deck:
                            deck_snapshot = _snapshot_deck(matched_deck)
                            deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
                        else:
                            deck_snapshot = {
                                "folder_id": None,
                                "deck_name": deck_name or "Unknown deck",
                                "commander_name": commander_name or None,
                                "commander_oracle_id": commander_oracle_id or None,
                                "bracket_level": bracket_level or None,
                                "bracket_label": bracket_label or None,
                                "bracket_score": bracket_score,
                                "power_score": power_score,
                            }

                seat_payloads.append(
                    {
                        "seat_number": seat_number,
                        "turn_order": turn_order,
                        "player_user": player_user,
                        "player_label": display_name,
                        "deck_snapshot": deck_snapshot,
                    }
                )

            seat_count = len(seat_payloads)
            if not (2 <= seat_count <= 4):
                errors.append(f"Game {game_key}: seat count must be 2-4.")
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
            for payload in sorted(seat_payloads, key=lambda item: item["seat_number"]):
                seat = GameSeat(
                    session_id=session.id,
                    seat_number=payload["seat_number"],
                    turn_order=payload["turn_order"],
                )
                db.session.add(seat)
                seats_by_number[seat.seat_number] = seat

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

        return imported, skipped, errors

    def _import_long_rows(seat_rows: list[dict[str, str]]):
        grouped: dict[str, list[dict[str, str]]] = {}
        for index, row in enumerate(seat_rows, start=1):
            game_key = row.get("game_id") or row.get("game_key")
            if not game_key:
                errors.append(f"Row {index}: missing game_id column.")
                continue
            grouped.setdefault(str(game_key), []).append(row)
        if not grouped:
            return 0, len(seat_rows), errors

        folder_ids: set[int] = set()
        for grouped_rows in grouped.values():
            for row in grouped_rows:
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
        user_cache: dict[int, User | None] = {}

        for game_key, grouped_rows in grouped.items():
            seat_numbers = {
                _parse_int(row.get("seat_number") or "")
                for row in grouped_rows
                if row.get("seat_number")
            }
            seat_numbers = {num for num in seat_numbers if num}
            seat_count = len(seat_numbers) if seat_numbers else len(grouped_rows)
            if not (2 <= seat_count <= 4):
                errors.append(f"Game {game_key}: seat count must be 2-4.")
                skipped += 1
                continue

            first_row = grouped_rows[0]
            played_at_raw = first_row.get("played_at") or ""
            notes = (first_row.get("notes") or "").strip()
            win_via_combo = _truthy(first_row.get("win_via_combo"))
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

            for seat_entry in grouped_rows:
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

                deck_name = seat_entry.get("deck_name") or None
                deck_folder_id = _parse_int(seat_entry.get("deck_folder_id") or "")
                if deck_folder_id and deck_folder_id in folder_map:
                    deck_snapshot = _snapshot_deck(folder_map[deck_folder_id])
                    deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
                else:
                    owner_id = player_user.id if player_user else current_user.id
                    name_match = _find_deck_by_name(owner_id, deck_name)
                    if name_match:
                        deck_snapshot = _snapshot_deck(name_match)
                        deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
                    else:
                        commander_name = seat_entry.get("commander_name") or None
                        commander_oracle_id = seat_entry.get("commander_oracle_id") or None
                        matched_deck = _match_commander_deck(
                            commander_lookup,
                            commander_oracle_id,
                            commander_name,
                        )
                        if matched_deck:
                            deck_snapshot = _snapshot_deck(matched_deck)
                            deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
                        else:
                            deck_snapshot = {
                                "folder_id": None,
                                "deck_name": deck_name or "Unknown deck",
                                "commander_name": commander_name,
                                "commander_oracle_id": commander_oracle_id,
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

        return imported, skipped, errors

    errors: list[str] = []
    if wide_format:
        imported, skipped, errors = _import_wide_rows(normalized_rows)
    else:
        imported, skipped, errors = _import_long_rows(normalized_rows)

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


def games_import_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_game_csv_headers_wide(include_game_id=False))
    filename = "dragonsvault-game-import-template.csv"
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def games_metrics():
    range_ctx = _resolve_date_range(request.args)
    pod_options = _pod_options_for_user(current_user.id)
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

    base_scope = _pod_metrics_scope(current_user.id, selected_pod_id) if selected_pod_id else None
    if selected_pod_id and base_scope is None:
        base_scope = {"session_filter": text("1 = 0")}

    player_key = (request.args.get("player") or "").strip()
    deck_key = (request.args.get("deck") or "").strip()
    player_session_filter = _session_filter_for_player(player_key, scope=base_scope)
    deck_session_filter = _session_filter_for_deck(deck_key)
    metrics_scope = _merge_scope_filters(base_scope, [player_session_filter, deck_session_filter])
    player_scope = _merge_scope_filters(base_scope, [deck_session_filter])
    deck_scope = _merge_scope_filters(base_scope, [player_session_filter])

    metrics = _metrics_payload(current_user.id, range_ctx["start_at"], range_ctx["end_at"], scope=metrics_scope)
    year_options = _available_years(current_user.id, scope=base_scope)
    range_params = _range_query_params(range_ctx)
    range_params = dict(range_params)
    if selected_pod_id:
        range_params["pod"] = str(selected_pod_id)

    player_options = _player_options(
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

    deck_options = _deck_options(
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

    player_metrics = _player_stats(
        current_user.id,
        player_key,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
    )
    player_filter_active = bool(player_key)
    deck_usage = _deck_usage(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key if player_filter_active else None,
        scope=metrics_scope,
    )
    top_players = _top_players_by_plays(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
    )
    combo_winners = _combo_winners(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
    )
    turn_order_metrics = _turn_order_metrics(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
    )
    player_win_rates = _player_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
    )
    deck_win_rates = _deck_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key if player_filter_active else None,
        scope=metrics_scope,
    )
    bracket_stats = _bracket_stats(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key if player_filter_active else None,
        scope=metrics_scope,
    )
    games = _metrics_games(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=metrics_scope,
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
    range_ctx = _resolve_date_range(request.args)
    pod_options = _pod_options_for_user(current_user.id)
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
    scope = _pod_metrics_scope(current_user.id, selected_pod_id) if selected_pod_id else None
    if selected_pod_id and scope is None:
        scope = {"session_filter": text("1 = 0")}
    player_key = (request.args.get("player") or "").strip()
    if not player_key:
        flash("Select a player to view detailed metrics.", "warning")
        return redirect(url_for("views.games_metrics"))

    player_options = _player_options(current_user.id, range_ctx["start_at"], range_ctx["end_at"], scope=scope)
    player_label = ""
    for option in player_options:
        if option["key"] == player_key:
            player_label = option["label"]
            break
    if not player_label:
        player_label = "Selected player"

    player_metrics = _player_stats(current_user.id, player_key, range_ctx["start_at"], range_ctx["end_at"], scope=scope)
    deck_stats = _player_deck_stats(
        current_user.id,
        player_key,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=scope,
    )
    commander_win_rates = _commander_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key,
        scope=scope,
    )
    deck_win_rates = _deck_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        player_key=player_key,
        scope=scope,
        limit=9999,
    )
    range_params = _range_query_params(range_ctx)
    range_params = dict(range_params)
    if selected_pod_id:
        range_params["pod"] = str(selected_pod_id)

    return render_template(
        "games/metrics_player.html",
        range_ctx=range_ctx,
        year_options=_available_years(current_user.id, scope=scope),
        range_params=range_params,
        player_key=player_key,
        player_label=player_label,
        player_metrics=player_metrics,
        deck_stats=deck_stats,
        deck_win_rates=deck_win_rates,
        commander_win_rates=commander_win_rates,
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
                "deck_options": _accessible_deck_options(),
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
    manual_link_options = _accessible_deck_options()
    guest_deck_refs = {deck["ref"] for deck in guest_deck_options}
    pods = _pod_payloads_for_owner(current_user.id, roster_players)
    pod_member_map = {str(pod["id"]): pod["member_ids"] for pod in pods}
    return {
        "roster_players": roster_players,
        "roster_map": roster_map,
        "roster_deck_refs": roster_deck_refs,
        "guest_deck_options": guest_deck_options,
        "manual_link_options": manual_link_options,
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
            "manual_commander_name": "",
            "manual_link_ref": "",
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


def _game_form_data_from_session(
    session: GameSession,
    context: dict[str, Any],
    folder_map: dict[int, Folder] | None = None,
) -> dict[str, Any]:
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
        manual_commander_name = ""
        manual_link_ref = ""
        if deck:
            linked_folder = None
            if deck.folder_id:
                linked_folder = folder_map.get(deck.folder_id) if folder_map else None
                linked_name = (linked_folder.name or "").strip() if linked_folder else ""
                deck_name = (deck.deck_name or "").strip()
                if deck_name and linked_name and deck_name.lower() != linked_name.lower():
                    manual_name = deck_name
                    manual_commander_name = deck.commander_name or ""
                    manual_link_ref = f"folder:{deck.folder_id}"
                    deck_ref = "manual:new"
                else:
                    deck_ref = f"folder:{deck.folder_id}"
            else:
                manual_name = deck.deck_name or ""
                manual_commander_name = deck.commander_name or ""
                if manual_name:
                    deck_ref = "manual:new"

            if not manual_commander_name:
                if deck.commander_oracle_id:
                    manual_commander_name = _oracle_name_from_id(deck.commander_oracle_id) or ""
                if not manual_commander_name and linked_folder:
                    manual_commander_name = linked_folder.commander_name or ""
                if not manual_commander_name and linked_folder and linked_folder.commander_oracle_id:
                    manual_commander_name = _oracle_name_from_id(linked_folder.commander_oracle_id) or ""

        seats[seat.seat_number] = {
            "player_type": player_type,
            "roster_id": roster_id,
            "guest_name": guest_name or "",
            "deck_ref": deck_ref,
            "manual_deck_name": manual_name,
            "manual_commander_name": manual_commander_name,
            "manual_link_ref": manual_link_ref,
            "turn_order": seat.turn_order,
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
    auto_assignments: list[tuple[int, int]] = []

    for seat_number in range(1, seat_count + 1):
        player_type = (request.form.get(f"seat_{seat_number}_player_type") or "").strip().lower()
        roster_id_raw = request.form.get(f"seat_{seat_number}_roster_id")
        guest_name = (request.form.get(f"seat_{seat_number}_guest_name") or "").strip()
        deck_ref_raw = request.form.get(f"seat_{seat_number}_deck_ref")
        manual_deck_name = (request.form.get(f"seat_{seat_number}_manual_deck_name") or "").strip()
        manual_commander_name = (request.form.get(f"seat_{seat_number}_manual_commander_name") or "").strip()
        manual_link_ref = (request.form.get(f"seat_{seat_number}_manual_link_ref") or "").strip()

        form_data["seats"][seat_number] = {
            "player_type": player_type or "roster",
            "roster_id": roster_id_raw,
            "guest_name": guest_name,
            "deck_ref": deck_ref_raw,
            "manual_deck_name": manual_deck_name,
            "manual_commander_name": manual_commander_name,
            "manual_link_ref": manual_link_ref,
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

        manual_link_id = None
        if manual_link_ref:
            raw_link = manual_link_ref.strip()
            if raw_link.startswith("folder:") or raw_link.isdigit():
                try:
                    manual_link_id = parse_positive_int(raw_link.split(":", 1)[-1], field="commander deck", min_value=1)
                except ValidationError as exc:
                    log_validation_error(exc, context="game_commander_link")
                    errors.append(f"Seat {seat_number}: select a valid commander deck.")
                    manual_link_id = None
            else:
                errors.append(f"Seat {seat_number}: select a valid commander deck.")
        if deck_kind not in {"manual_entry", "manual"}:
            manual_link_id = None

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
                    if deck_kind == "folder" and deck_ref_raw in guest_deck_refs and deck_lookup_id:
                        auto_assignments.append((roster_player["id"], deck_lookup_id))
                    else:
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
        if manual_link_id:
            deck_ids_to_load.add(manual_link_id)

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
                "manual_commander_name": manual_commander_name,
                "manual_link_id": manual_link_id,
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
        "auto_assignments": auto_assignments,
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
            manual_name = payload.get("manual_deck_name") or "Deck"
            manual_commander = payload.get("manual_commander_name") or None
            manual_link_id = payload.get("manual_link_id")
            manual_link_folder = folders_by_id.get(manual_link_id) if manual_link_id else None
            resolved_folder = (
                _find_deck_by_name(player_user.id, manual_name)
                if player_user and manual_name
                else None
            )
            if manual_link_folder:
                deck_snapshot = _snapshot_deck(manual_link_folder)
                deck_snapshot["deck_name"] = manual_name
            elif resolved_folder:
                deck_snapshot = _snapshot_deck(resolved_folder)
                deck_snapshot["deck_name"] = manual_name
            else:
                commander_oracle_id = None
                if manual_commander:
                    try:
                        sc.ensure_cache_loaded()
                        commander_oracle_id = sc.unique_oracle_by_name(manual_commander)
                    except Exception:
                        commander_oracle_id = None
                deck_snapshot = {
                    "folder_id": None,
                    "deck_name": manual_name,
                    "commander_name": manual_commander,
                    "commander_oracle_id": commander_oracle_id,
                    "bracket_level": None,
                    "bracket_label": None,
                    "bracket_score": None,
                    "power_score": None,
                }
        else:
            manual = manual_decks_by_id.get(deck_id)
            manual_commander = payload.get("manual_commander_name") or None
            manual_link_id = payload.get("manual_link_id")
            manual_link_folder = folders_by_id.get(manual_link_id) if manual_link_id else None
            resolved_folder = (
                _find_deck_by_name(player_user.id, manual.deck_name)
                if manual and player_user and manual.deck_name
                else None
            )
            if manual_link_folder:
                deck_snapshot = _snapshot_deck(manual_link_folder)
                if manual and manual.deck_name:
                    deck_snapshot["deck_name"] = manual.deck_name
                if manual and not manual.folder_id:
                    manual.folder_id = manual_link_folder.id
            elif resolved_folder:
                deck_snapshot = _snapshot_deck(resolved_folder)
                if manual and manual.deck_name:
                    deck_snapshot["deck_name"] = manual.deck_name
                if manual and not manual.folder_id:
                    manual.folder_id = resolved_folder.id
            else:
                commander_oracle_id = None
                if manual_commander:
                    try:
                        sc.ensure_cache_loaded()
                        commander_oracle_id = sc.unique_oracle_by_name(manual_commander)
                    except Exception:
                        commander_oracle_id = None
                deck_snapshot = {
                    "folder_id": None,
                    "deck_name": (manual.deck_name if manual else None) or "Deck",
                    "commander_name": manual_commander,
                    "commander_oracle_id": commander_oracle_id,
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
    manual_link_options = context["manual_link_options"]
    pods = context["pods"]
    pod_member_map = context["pod_member_map"]
    form_data = _default_game_form_data(context)

    if request.method == "GET":
        return render_template(
            "games/new.html",
            roster_players=roster_players,
            roster_deck_map=roster_map,
            guest_deck_options=guest_deck_options,
            manual_link_options=manual_link_options,
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
    auto_assignments = parsed["auto_assignments"]
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
        manual_link_id = payload.get("manual_link_id")
        if deck_kind == "folder" and deck_id and deck_id not in folders_by_id:
            errors.append("Selected deck is not accessible.")
        if deck_kind == "manual" and deck_id and deck_id not in manual_decks_by_id:
            errors.append("Selected manual deck is not accessible.")
        if manual_link_id and manual_link_id not in folders_by_id:
            errors.append("Selected commander deck is not accessible.")

    if errors:
        for message in errors:
            flash(message, "warning")
        return render_template(
            "games/new.html",
            roster_players=roster_players,
            roster_deck_map=roster_map,
            guest_deck_options=guest_deck_options,
            manual_link_options=manual_link_options,
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
    if auto_assignments:
        for roster_id, deck_id in auto_assignments:
            existing = GameRosterDeck.query.filter_by(roster_player_id=roster_id, folder_id=deck_id).first()
            if not existing:
                db.session.add(
                    GameRosterDeck(
                        roster_player_id=roster_id,
                        owner_user_id=current_user.id,
                        folder_id=deck_id,
                    )
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
            manual_link_options=manual_link_options,
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
    manual_link_options = context["manual_link_options"]
    pods = context["pods"]
    pod_member_map = context["pod_member_map"]

    if request.method == "GET":
        folder_ids = {deck.folder_id for deck in (session.decks or []) if deck.folder_id}
        folder_map = (
            {folder.id: folder for folder in Folder.query.filter(Folder.id.in_(folder_ids)).all()}
            if folder_ids
            else {}
        )
        form_data = _game_form_data_from_session(session, context, folder_map)
        return render_template(
            "games/new.html",
            roster_players=roster_players,
            roster_deck_map=roster_map,
            guest_deck_options=guest_deck_options,
            manual_link_options=manual_link_options,
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
    auto_assignments = parsed["auto_assignments"]
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
        manual_link_id = payload.get("manual_link_id")
        if deck_kind == "folder" and deck_id and deck_id not in folders_by_id:
            errors.append("Selected deck is not accessible.")
        if deck_kind == "manual" and deck_id and deck_id not in manual_decks_by_id:
            errors.append("Selected manual deck is not accessible.")
        if manual_link_id and manual_link_id not in folders_by_id:
            errors.append("Selected commander deck is not accessible.")

    if errors:
        for message in errors:
            flash(message, "warning")
        return render_template(
            "games/new.html",
            roster_players=roster_players,
            roster_deck_map=roster_map,
            guest_deck_options=guest_deck_options,
            manual_link_options=manual_link_options,
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
    if auto_assignments:
        for roster_id, deck_id in auto_assignments:
            existing = GameRosterDeck.query.filter_by(roster_player_id=roster_id, folder_id=deck_id).first()
            if not existing:
                db.session.add(
                    GameRosterDeck(
                        roster_player_id=roster_id,
                        owner_user_id=current_user.id,
                        folder_id=deck_id,
                    )
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
            manual_link_options=manual_link_options,
            pods=pods,
            pod_member_map=pod_member_map,
            form_data=form_data,
            is_edit=True,
            game_id=session.id,
        )

    flash("Game updated.", "success")
    return redirect(url_for("views.games_detail", game_id=session.id))


def games_delete(game_id: int):
    session = GameSession.query.filter_by(id=game_id, owner_user_id=current_user.id).first()
    if not session:
        flash("Game session not found.", "warning")
        return redirect(url_for("views.games_overview"))
    db.session.delete(session)
    try:
        db.session.commit()
        flash("Game log deleted.", "info")
    except Exception:
        db.session.rollback()
        flash("Unable to delete the game right now.", "danger")
        return redirect(url_for("views.games_detail", game_id=game_id))
    return redirect(url_for("views.games_overview"))


def games_bulk_delete():
    raw_ids = (request.form.get("game_ids") or "").strip()
    if not raw_ids:
        flash("Select at least one game log.", "warning")
        return redirect(url_for("views.games_overview"))

    game_ids: list[int] = []
    for token in raw_ids.replace(" ", "").split(","):
        if not token:
            continue
        try:
            game_ids.append(parse_positive_int(token, field="game"))
        except ValidationError as exc:
            log_validation_error(exc, context="game_bulk_delete")
            continue

    if not game_ids:
        flash("Select at least one game log.", "warning")
        return redirect(url_for("views.games_overview"))

    sessions = (
        GameSession.query.filter(
            GameSession.id.in_(game_ids),
            GameSession.owner_user_id == current_user.id,
        )
        .all()
    )
    if not sessions:
        flash("No owned game logs were selected.", "warning")
        return redirect(url_for("views.games_overview"))

    deleted = 0
    for session in sessions:
        db.session.delete(session)
        deleted += 1
    try:
        db.session.commit()
        flash(f"Deleted {deleted} game log{'s' if deleted != 1 else ''}.", "info")
    except Exception:
        db.session.rollback()
        flash("Unable to delete the selected game logs.", "danger")
    return redirect(url_for("views.games_overview"))

def games_metrics_pods():
    """Pod-focused metrics page."""
    range_ctx = _resolve_date_range(request.args)
    pod_options = _pod_options_for_user(current_user.id)
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

    base_scope = _pod_metrics_scope(current_user.id, selected_pod_id) if selected_pod_id else None
    if selected_pod_id and base_scope is None:
        base_scope = {"session_filter": text("1 = 0")}

    metrics = _metrics_payload(current_user.id, range_ctx["start_at"], range_ctx["end_at"], scope=base_scope)
    year_options = _available_years(current_user.id, scope=base_scope)
    range_params = _range_query_params(range_ctx)
    range_params = dict(range_params)
    if selected_pod_id:
        range_params["pod"] = str(selected_pod_id)

    # Pod-specific metrics
    pod_breakdown = []
    for pod in pod_options:
        pod_scope = _pod_metrics_scope(current_user.id, pod["id"])
        if pod_scope:
            pod_metrics = _metrics_payload(current_user.id, range_ctx["start_at"], range_ctx["end_at"], scope=pod_scope)
            pod_breakdown.append({
                "id": pod["id"],
                "label": pod["label"],
                "metrics": pod_metrics
            })

    games = _metrics_games(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=base_scope,
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


def games_metrics_users():
    """User-focused metrics page."""
    range_ctx = _resolve_date_range(request.args)
    pod_options = _pod_options_for_user(current_user.id)
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

    base_scope = _pod_metrics_scope(current_user.id, selected_pod_id) if selected_pod_id else None
    if selected_pod_id and base_scope is None:
        base_scope = {"session_filter": text("1 = 0")}

    metrics = _metrics_payload(current_user.id, range_ctx["start_at"], range_ctx["end_at"], scope=base_scope)
    year_options = _available_years(current_user.id, scope=base_scope)
    range_params = _range_query_params(range_ctx)
    range_params = dict(range_params)
    if selected_pod_id:
        range_params["pod"] = str(selected_pod_id)

    # User-specific metrics
    player_options = _player_options(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=base_scope,
    )
    
    top_players = _top_players_by_plays(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=base_scope,
    )
    
    player_win_rates = _player_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=base_scope,
    )
    
    combo_winners = _combo_winners(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=base_scope,
    )

    return render_template(
        "games/metrics_users.html",
        metrics=metrics,
        range_ctx=range_ctx,
        year_options=year_options,
        range_params=range_params,
        pod_options=pod_options,
        selected_pod_id=selected_pod_id,
        player_options=player_options,
        top_players=top_players,
        player_win_rates=player_win_rates,
        combo_winners=combo_winners,
    )


def games_metrics_decks():
    """Deck-focused metrics page."""
    range_ctx = _resolve_date_range(request.args)
    pod_options = _pod_options_for_user(current_user.id)
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

    base_scope = _pod_metrics_scope(current_user.id, selected_pod_id) if selected_pod_id else None
    if selected_pod_id and base_scope is None:
        base_scope = {"session_filter": text("1 = 0")}

    metrics = _metrics_payload(current_user.id, range_ctx["start_at"], range_ctx["end_at"], scope=base_scope)
    year_options = _available_years(current_user.id, scope=base_scope)
    range_params = _range_query_params(range_ctx)
    range_params = dict(range_params)
    if selected_pod_id:
        range_params["pod"] = str(selected_pod_id)

    # Deck-specific metrics
    deck_options = _deck_options(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        scope=base_scope,
    )
    
    deck_usage = _deck_usage(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=base_scope,
    )
    
    deck_win_rates = _deck_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=base_scope,
    )
    
    commander_usage = _commander_usage(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=base_scope,
    )
    
    commander_win_rates = _commander_win_rates(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=base_scope,
    )
    
    bracket_stats = _bracket_stats(
        current_user.id,
        range_ctx["start_at"],
        range_ctx["end_at"],
        limit=10,
        scope=base_scope,
    )

    return render_template(
        "games/metrics_decks.html",
        metrics=metrics,
        range_ctx=range_ctx,
        year_options=year_options,
        range_params=range_params,
        pod_options=pod_options,
        selected_pod_id=selected_pod_id,
        deck_options=deck_options,
        deck_usage=deck_usage,
        deck_win_rates=deck_win_rates,
        commander_usage=commander_usage,
        commander_win_rates=commander_win_rates,
        bracket_stats=bracket_stats,
    )


__all__ = [
    "games_landing",
    "games_dashboard",
    "games_admin",
    "games_overview",
    "games_export",
    "games_import",
    "games_import_template",
    "games_metrics",
    "games_metrics_player",
    "games_metrics_pods",
    "games_metrics_users",
    "games_metrics_decks",
    "games_players",
    "games_new",
    "games_edit",
    "games_delete",
    "games_bulk_delete",
    "game_detail",
]
