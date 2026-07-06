"""Shared helpers for game session parsing, deck lookup, and view payloads."""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from sqlalchemy import case, func

from extensions import db
from models import CommanderBracketCache, Folder, FolderRole, GameDeck, GamePlayer, GameSession
from core.domains.cards.services import scryfall_cache as sc
from core.shared.utils.time import utcnow
from shared.validation import ValidationError, log_validation_error, parse_positive_int

from . import game_metrics_support_service as metrics_support

__all__ = [
    "_find_deck_by_name",
    "_game_session_payload",
    "_games_summary",
    "_manual_deck_summary",
    "_oracle_image",
    "_oracle_name_from_id",
    "_parse_deck_ref",
    "_parse_played_at",
    "_player_label",
    "_snapshot_deck",
]


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
    total, combo_wins = (
        db.session.query(
            func.count(GameSession.id),
            func.coalesce(func.sum(case((GameSession.win_via_combo.is_(True), 1), else_=0)), 0),
        )
        .filter(*metrics_support._session_filters(user_id))
        .one()
    )
    return {"total_games": int(total or 0), "combo_wins": int(combo_wins or 0)}


def _manual_deck_summary(owner_user_id: int) -> list[dict[str, Any]]:
    played_expr = func.coalesce(GameSession.played_at, GameSession.created_at)
    rows = (
        db.session.query(
            GameDeck.deck_name,
            GameDeck.commander_name,
            func.count(GameDeck.id).label("instances"),
            func.count(func.distinct(GameSession.id)).label("games"),
            func.max(played_expr).label("last_played"),
            func.max(GameDeck.bracket_level).label("bracket_level"),
            func.max(GameDeck.bracket_label).label("bracket_label"),
            func.max(GameDeck.bracket_score).label("bracket_score"),
        )
        .join(GameSession, GameSession.id == GameDeck.session_id)
        .filter(
            GameSession.owner_user_id == owner_user_id,
            GameDeck.folder_id.is_(None),
            GameDeck.deck_name.isnot(None),
        )
        .group_by(GameDeck.deck_name, GameDeck.commander_name)
        .order_by(func.count(GameDeck.id).desc(), func.lower(GameDeck.deck_name))
        .all()
    )
    manual_decks: list[dict[str, Any]] = []
    for row in rows:
        deck_name = (row.deck_name or "").strip()
        if not deck_name:
            continue
        commander_name = (row.commander_name or "").strip()
        last_played = row.last_played.date().isoformat() if row.last_played else ""
        manual_decks.append(
            {
                "deck_name": deck_name,
                "commander_name": commander_name,
                "instances": int(row.instances or 0),
                "games": int(row.games or 0),
                "last_played": last_played,
                "bracket_level": row.bracket_level,
                "bracket_label": row.bracket_label,
                "bracket_score": float(row.bracket_score) if row.bracket_score is not None else None,
            }
        )
    return manual_decks
