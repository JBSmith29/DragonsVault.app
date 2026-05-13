"""Per-deck win rate analytics for the game log.

This service reads the existing game log tables (``game_sessions``,
``game_seats``, ``game_decks``, ``game_seat_assignments``) to produce:

* overall record for a deck (wins, losses, draws-as-wins excluded)
* win rate by seat position (turn order)
* matchup breakdown against the commander or deck opposite each win/loss
* performance across recent date windows

It does **not** mutate any rows and has no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from extensions import db
from models import (
    Folder,
    GameDeck,
    GameSeat,
    GameSeatAssignment,
    GameSession,
)
from core.shared.utils.time import utcnow


__all__ = [
    "DeckWinRateReport",
    "deck_winrate_for_folder",
    "deck_winrate_for_manual_deck",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MatchupRow:
    opponent_commander: str
    games: int = 0
    wins: int = 0
    losses: int = 0

    @property
    def win_rate(self) -> float | None:
        total = self.games
        return (self.wins / total) if total else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "opponent_commander": self.opponent_commander,
            "games": self.games,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
        }


@dataclass
class SeatRow:
    seat_number: int
    games: int = 0
    wins: int = 0

    @property
    def win_rate(self) -> float | None:
        return (self.wins / self.games) if self.games else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat_number": self.seat_number,
            "games": self.games,
            "wins": self.wins,
            "win_rate": self.win_rate,
        }


@dataclass
class DeckWinRateReport:
    scope: str  # "folder" or "manual"
    identifier: str
    deck_name: str | None
    commander_name: str | None
    games: int
    wins: int
    losses: int
    win_rate: float | None
    last_played: datetime | None
    seat_performance: list[SeatRow]
    matchups: list[MatchupRow]
    recent_window_days: int
    recent_games: int
    recent_wins: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "identifier": self.identifier,
            "deck_name": self.deck_name,
            "commander_name": self.commander_name,
            "games": self.games,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "last_played": self.last_played.isoformat() if self.last_played else None,
            "seat_performance": [row.to_dict() for row in self.seat_performance],
            "matchups": [row.to_dict() for row in self.matchups],
            "recent_window_days": self.recent_window_days,
            "recent_games": self.recent_games,
            "recent_wins": self.recent_wins,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _SessionContext:
    session: GameSession
    deck_seat: GameSeat
    deck_assignment: GameSeatAssignment


def _assignments_for_session(session: GameSession) -> list[GameSeatAssignment]:
    return [seat.assignment for seat in session.seats if seat.assignment]


def _opponent_labels(
    session: GameSession, own_assignment: GameSeatAssignment
) -> list[str]:
    labels: list[str] = []
    for assignment in _assignments_for_session(session):
        if assignment.id == own_assignment.id:
            continue
        deck = assignment.deck
        if deck and deck.commander_name:
            labels.append(deck.commander_name.strip())
        elif deck and deck.deck_name:
            labels.append(deck.deck_name.strip())
    return labels


def _count_games(
    contexts: Iterable[_SessionContext],
    *,
    recent_cutoff: datetime | None,
) -> tuple[int, int, int, int, int, dict[int, SeatRow], list[MatchupRow], datetime | None]:
    games = 0
    wins = 0
    losses = 0
    recent_games = 0
    recent_wins = 0
    seat_rows: dict[int, SeatRow] = {}
    matchups: dict[str, MatchupRow] = {}
    last_played: datetime | None = None

    for ctx in contexts:
        games += 1
        session = ctx.session
        played_at = session.played_at or session.created_at
        if played_at and (last_played is None or played_at > last_played):
            last_played = played_at

        seat_number = ctx.deck_seat.seat_number
        seat_row = seat_rows.setdefault(seat_number, SeatRow(seat_number=seat_number))
        seat_row.games += 1

        is_win = session.winner_seat_id == ctx.deck_seat.id
        if is_win:
            wins += 1
            seat_row.wins += 1
        elif session.winner_seat_id is not None:
            losses += 1

        if recent_cutoff and played_at and played_at >= recent_cutoff:
            recent_games += 1
            if is_win:
                recent_wins += 1

        for opponent_label in _opponent_labels(session, ctx.deck_assignment):
            row = matchups.setdefault(
                opponent_label, MatchupRow(opponent_commander=opponent_label)
            )
            row.games += 1
            if is_win:
                row.wins += 1
            elif session.winner_seat_id is not None:
                row.losses += 1

    matchup_list = sorted(
        matchups.values(),
        key=lambda row: (-row.games, -row.wins, row.opponent_commander.lower()),
    )
    return (
        games,
        wins,
        losses,
        recent_games,
        recent_wins,
        seat_rows,
        matchup_list,
        last_played,
    )


def _build_report(
    *,
    scope: str,
    identifier: str,
    deck_name: str | None,
    commander_name: str | None,
    contexts: list[_SessionContext],
    recent_days: int,
) -> DeckWinRateReport:
    cutoff = utcnow() - timedelta(days=recent_days) if recent_days > 0 else None
    (
        games,
        wins,
        losses,
        recent_games,
        recent_wins,
        _seat_map,
        matchup_list,
        last_played,
    ) = _count_games(contexts, recent_cutoff=cutoff)

    win_rate = (wins / games) if games else None
    seat_numbers = sorted({ctx.deck_seat.seat_number for ctx in contexts})
    seat_performance: list[SeatRow] = []
    for seat_number in seat_numbers:
        matching = [ctx for ctx in contexts if ctx.deck_seat.seat_number == seat_number]
        row = SeatRow(seat_number=seat_number, games=len(matching))
        row.wins = sum(
            1 for ctx in matching if ctx.session.winner_seat_id == ctx.deck_seat.id
        )
        seat_performance.append(row)

    return DeckWinRateReport(
        scope=scope,
        identifier=identifier,
        deck_name=deck_name,
        commander_name=commander_name,
        games=games,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        last_played=last_played,
        seat_performance=seat_performance,
        matchups=matchup_list,
        recent_window_days=recent_days,
        recent_games=recent_games,
        recent_wins=recent_wins,
    )


def _load_context_by_deck_filter(
    *,
    user_id: int,
    deck_filter,
) -> list[_SessionContext]:
    sessions = (
        db.session.query(GameSession)
        .join(GameDeck, GameDeck.session_id == GameSession.id)
        .join(
            GameSeatAssignment,
            GameSeatAssignment.deck_id == GameDeck.id,
        )
        .join(GameSeat, GameSeat.id == GameSeatAssignment.seat_id)
        .filter(GameSession.owner_user_id == user_id)
        .filter(deck_filter)
        .options(
            selectinload(GameSession.seats).selectinload(GameSeat.assignment),
        )
        .all()
    )

    contexts: list[_SessionContext] = []
    for session in sessions:
        for seat in session.seats:
            assignment = seat.assignment
            if not assignment or not assignment.deck:
                continue
            if deck_filter.compare(assignment.deck):
                contexts.append(
                    _SessionContext(
                        session=session,
                        deck_seat=seat,
                        deck_assignment=assignment,
                    )
                )
    return contexts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def deck_winrate_for_folder(
    *,
    user_id: int,
    folder: Folder,
    recent_days: int = 30,
) -> DeckWinRateReport:
    """Win-rate analytics for a registered folder (real deck)."""
    sessions = (
        db.session.query(GameSession)
        .join(GameDeck, GameDeck.session_id == GameSession.id)
        .filter(GameSession.owner_user_id == user_id)
        .filter(GameDeck.folder_id == folder.id)
        .options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .all()
    )
    contexts: list[_SessionContext] = []
    for session in sessions:
        for seat in session.seats:
            assignment = seat.assignment
            if not assignment or not assignment.deck:
                continue
            if assignment.deck.folder_id == folder.id:
                contexts.append(
                    _SessionContext(
                        session=session,
                        deck_seat=seat,
                        deck_assignment=assignment,
                    )
                )

    return _build_report(
        scope="folder",
        identifier=str(folder.id),
        deck_name=folder.name,
        commander_name=folder.commander_name,
        contexts=contexts,
        recent_days=recent_days,
    )


def deck_winrate_for_manual_deck(
    *,
    user_id: int,
    deck_name: str,
    recent_days: int = 30,
) -> DeckWinRateReport:
    """Win-rate analytics for a deck tracked only as a string (no folder id)."""
    normalized = (deck_name or "").strip()
    if not normalized:
        raise ValueError("deck_name must be non-empty")

    sessions = (
        db.session.query(GameSession)
        .join(GameDeck, GameDeck.session_id == GameSession.id)
        .filter(GameSession.owner_user_id == user_id)
        .filter(GameDeck.folder_id.is_(None))
        .filter(func.lower(GameDeck.deck_name) == normalized.lower())
        .options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .all()
    )
    contexts: list[_SessionContext] = []
    commander_name: str | None = None
    for session in sessions:
        for seat in session.seats:
            assignment = seat.assignment
            if not assignment or not assignment.deck:
                continue
            deck = assignment.deck
            if deck.folder_id is not None:
                continue
            if (deck.deck_name or "").strip().lower() != normalized.lower():
                continue
            contexts.append(
                _SessionContext(
                    session=session,
                    deck_seat=seat,
                    deck_assignment=assignment,
                )
            )
            if commander_name is None and deck.commander_name:
                commander_name = deck.commander_name

    return _build_report(
        scope="manual",
        identifier=normalized.lower(),
        deck_name=normalized,
        commander_name=commander_name,
        contexts=contexts,
        recent_days=recent_days,
    )
