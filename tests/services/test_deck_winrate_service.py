"""Tests for per-deck win-rate analytics."""

from __future__ import annotations

from datetime import timedelta

from core.domains.games.services.deck_winrate_service import (
    deck_winrate_for_folder,
    deck_winrate_for_manual_deck,
)
from core.shared.utils.time import utcnow
from extensions import db
from models import (
    GameDeck,
    GamePlayer,
    GameSeat,
    GameSeatAssignment,
    GameSession,
    User,
)
from tests.factories import create_folder


def _create_user(email="gamer@example.com"):
    user = User(email=email, username=email.split("@")[0])
    user.set_password("password123")
    db.session.add(user)
    db.session.flush()
    return user


def _create_session(
    user,
    *,
    played_at=None,
    winner_seat=None,
):
    session = GameSession(
        owner_user_id=user.id,
        played_at=played_at or utcnow(),
    )
    db.session.add(session)
    db.session.flush()
    if winner_seat:
        session.winner_seat_id = winner_seat.id
    return session


def _create_seat(session, *, seat_number, turn_order=None):
    seat = GameSeat(
        session_id=session.id,
        seat_number=seat_number,
        turn_order=turn_order or seat_number,
    )
    db.session.add(seat)
    db.session.flush()
    return seat


def _create_deck(session, *, folder_id=None, deck_name=None, commander_name=None):
    deck = GameDeck(
        session_id=session.id,
        folder_id=folder_id,
        deck_name=deck_name or (f"Deck {folder_id}" if folder_id else "Borrowed"),
        commander_name=commander_name,
    )
    db.session.add(deck)
    db.session.flush()
    return deck


def _assign(session, seat, deck, *, player=None):
    assignment = GameSeatAssignment(
        session_id=session.id,
        seat_id=seat.id,
        deck_id=deck.id,
        player_id=player.id if player else None,
    )
    db.session.add(assignment)
    db.session.flush()
    return assignment


def test_deck_winrate_for_folder_counts_wins_and_losses(app, db_session):
    with app.app_context():
        user = _create_user()
        deck_folder = create_folder(name="My Atraxa")
        deck_folder.owner_user_id = user.id
        deck_folder.commander_name = "Atraxa, Praetors' Voice"

        # Game 1: player seat 1, wins
        session_a = _create_session(user)
        seat_a1 = _create_seat(session_a, seat_number=1)
        seat_a2 = _create_seat(session_a, seat_number=2)
        deck_a1 = _create_deck(session_a, folder_id=deck_folder.id)
        deck_a2 = _create_deck(session_a, deck_name="Opponent Kenrith", commander_name="Kenrith")
        _assign(session_a, seat_a1, deck_a1)
        _assign(session_a, seat_a2, deck_a2)
        session_a.winner_seat_id = seat_a1.id

        # Game 2: player seat 2, loses to opponent
        session_b = _create_session(user)
        seat_b1 = _create_seat(session_b, seat_number=1)
        seat_b2 = _create_seat(session_b, seat_number=2)
        deck_b1 = _create_deck(session_b, deck_name="Opponent", commander_name="Sisay")
        deck_b2 = _create_deck(session_b, folder_id=deck_folder.id)
        _assign(session_b, seat_b1, deck_b1)
        _assign(session_b, seat_b2, deck_b2)
        session_b.winner_seat_id = seat_b1.id

        db.session.commit()
        report = deck_winrate_for_folder(user_id=user.id, folder=deck_folder)

    assert report.games == 2
    assert report.wins == 1
    assert report.losses == 1
    assert report.win_rate == 0.5
    assert report.commander_name == "Atraxa, Praetors' Voice"


def test_deck_winrate_seat_performance_splits_by_seat(app, db_session):
    with app.app_context():
        user = _create_user()
        deck = create_folder(name="Seat Test Deck")
        deck.owner_user_id = user.id

        # Two wins from seat 1, one loss from seat 3.
        for seat_num, win in [(1, True), (1, True), (3, False)]:
            session = _create_session(user)
            seat_own = _create_seat(session, seat_number=seat_num)
            seat_other = _create_seat(session, seat_number=4)
            deck_row = _create_deck(session, folder_id=deck.id)
            opp = _create_deck(session, deck_name="Opp", commander_name="Opp Commander")
            _assign(session, seat_own, deck_row)
            _assign(session, seat_other, opp)
            session.winner_seat_id = seat_own.id if win else seat_other.id
        db.session.commit()
        report = deck_winrate_for_folder(user_id=user.id, folder=deck)

    seat_1 = next(row for row in report.seat_performance if row.seat_number == 1)
    seat_3 = next(row for row in report.seat_performance if row.seat_number == 3)
    assert seat_1.games == 2 and seat_1.wins == 2
    assert seat_3.games == 1 and seat_3.wins == 0


def test_deck_winrate_matchups_grouped_by_opponent_commander(app, db_session):
    with app.app_context():
        user = _create_user()
        deck = create_folder(name="Matchup Deck")
        deck.owner_user_id = user.id

        for winner_is_own, opp_name in [
            (True, "Kenrith"),
            (False, "Kenrith"),
            (True, "Yuriko"),
        ]:
            session = _create_session(user)
            own_seat = _create_seat(session, seat_number=1)
            opp_seat = _create_seat(session, seat_number=2)
            own_deck = _create_deck(session, folder_id=deck.id)
            opp_deck = _create_deck(session, deck_name=f"{opp_name} Deck", commander_name=opp_name)
            _assign(session, own_seat, own_deck)
            _assign(session, opp_seat, opp_deck)
            session.winner_seat_id = own_seat.id if winner_is_own else opp_seat.id

        db.session.commit()
        report = deck_winrate_for_folder(user_id=user.id, folder=deck)

    by_opponent = {m.opponent_commander: m for m in report.matchups}
    assert by_opponent["Kenrith"].games == 2
    assert by_opponent["Kenrith"].wins == 1
    assert by_opponent["Kenrith"].losses == 1
    assert by_opponent["Yuriko"].wins == 1


def test_deck_winrate_recent_window(app, db_session):
    with app.app_context():
        user = _create_user()
        deck = create_folder(name="Window Deck")
        deck.owner_user_id = user.id

        now = utcnow()
        # One win 5 days ago, one loss 90 days ago
        recent_session = _create_session(user, played_at=now - timedelta(days=5))
        own_seat = _create_seat(recent_session, seat_number=1)
        opp_seat = _create_seat(recent_session, seat_number=2)
        own_deck = _create_deck(recent_session, folder_id=deck.id)
        opp_deck = _create_deck(recent_session, deck_name="Opp", commander_name="Opp")
        _assign(recent_session, own_seat, own_deck)
        _assign(recent_session, opp_seat, opp_deck)
        recent_session.winner_seat_id = own_seat.id

        old_session = _create_session(user, played_at=now - timedelta(days=90))
        own_seat = _create_seat(old_session, seat_number=1)
        opp_seat = _create_seat(old_session, seat_number=2)
        own_deck = _create_deck(old_session, folder_id=deck.id)
        opp_deck = _create_deck(old_session, deck_name="Opp", commander_name="Opp")
        _assign(old_session, own_seat, own_deck)
        _assign(old_session, opp_seat, opp_deck)
        old_session.winner_seat_id = opp_seat.id

        db.session.commit()
        report = deck_winrate_for_folder(user_id=user.id, folder=deck, recent_days=30)

    assert report.games == 2
    assert report.recent_games == 1
    assert report.recent_wins == 1


def test_deck_winrate_for_manual_deck_matches_by_name(app, db_session):
    with app.app_context():
        user = _create_user()
        session = _create_session(user)
        own_seat = _create_seat(session, seat_number=1)
        opp_seat = _create_seat(session, seat_number=2)
        own_deck = _create_deck(session, deck_name="Borrowed Uro")
        opp_deck = _create_deck(session, deck_name="Gisela", commander_name="Gisela")
        _assign(session, own_seat, own_deck)
        _assign(session, opp_seat, opp_deck)
        session.winner_seat_id = own_seat.id
        db.session.commit()

        report = deck_winrate_for_manual_deck(user_id=user.id, deck_name="Borrowed Uro")

    assert report.games == 1
    assert report.wins == 1
    assert report.scope == "manual"
