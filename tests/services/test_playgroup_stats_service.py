"""Tests for playgroup stats."""

from __future__ import annotations

import pytest

from core.domains.games.services.playgroup_stats_service import (
    playgroup_stats_for_pod,
)
from core.shared.utils.time import utcnow
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


def _create_user(email):
    user = User(email=email, username=email.split("@")[0])
    user.set_password("password123")
    db.session.add(user)
    db.session.flush()
    return user


def _log_game(owner_user, seat_configs, *, winner_seat_index=0, played_at=None):
    session = GameSession(
        owner_user_id=owner_user.id,
        played_at=played_at or utcnow(),
    )
    db.session.add(session)
    db.session.flush()
    seats = []
    for idx, (player, commander_name) in enumerate(seat_configs, start=1):
        seat = GameSeat(session_id=session.id, seat_number=idx, turn_order=idx)
        db.session.add(seat)
        db.session.flush()
        deck = GameDeck(
            session_id=session.id,
            deck_name=f"{commander_name} Deck",
            commander_name=commander_name,
        )
        db.session.add(deck)
        db.session.flush()
        assignment = GameSeatAssignment(
            session_id=session.id,
            seat_id=seat.id,
            deck_id=deck.id,
            player_id=player.id,
        )
        db.session.add(assignment)
        seats.append(seat)
    db.session.flush()
    session.winner_seat_id = seats[winner_seat_index].id
    db.session.flush()
    return session


def test_playgroup_stats_aggregates_wins_and_commanders(app, db_session):
    with app.app_context():
        owner = _create_user("owner@example.com")
        alice = _create_user("alice@example.com")
        bob = _create_user("bob@example.com")

        roster_alice = GameRosterPlayer(
            owner_user_id=owner.id,
            user_id=alice.id,
            display_name="Alice",
        )
        roster_bob = GameRosterPlayer(
            owner_user_id=owner.id,
            user_id=bob.id,
            display_name="Bob",
        )
        pod = GamePod(owner_user_id=owner.id, name="Tuesday Night")
        db.session.add_all([roster_alice, roster_bob, pod])
        db.session.flush()
        db.session.add_all(
            [
                GamePodMember(pod_id=pod.id, roster_player_id=roster_alice.id),
                GamePodMember(pod_id=pod.id, roster_player_id=roster_bob.id),
            ]
        )

        alice_player = GamePlayer(user_id=alice.id, display_name="Alice")
        bob_player = GamePlayer(user_id=bob.id, display_name="Bob")
        db.session.add_all([alice_player, bob_player])
        db.session.flush()

        # Alice wins game 1 with Atraxa; Bob wins game 2 with Kenrith; Alice wins game 3 with Atraxa.
        _log_game(
            owner,
            [(alice_player, "Atraxa"), (bob_player, "Kenrith")],
            winner_seat_index=0,
        )
        _log_game(
            owner,
            [(alice_player, "Atraxa"), (bob_player, "Kenrith")],
            winner_seat_index=1,
        )
        _log_game(
            owner,
            [(alice_player, "Atraxa"), (bob_player, "Kenrith")],
            winner_seat_index=0,
        )
        db.session.commit()
        report = playgroup_stats_for_pod(user_id=owner.id, pod_id=pod.id)

    by_name = {p.display_name: p for p in report.players}
    assert by_name["Alice"].wins == 2
    assert by_name["Alice"].games == 3
    assert by_name["Bob"].wins == 1
    assert report.total_games == 3

    commanders_by_name = {c.commander_name: c for c in report.commanders}
    assert commanders_by_name["Atraxa"].games == 3
    assert commanders_by_name["Kenrith"].games == 3


def test_playgroup_stats_ignores_sessions_outside_pod(app, db_session):
    with app.app_context():
        owner = _create_user("pod-owner@example.com")
        outsider = _create_user("outsider@example.com")
        roster = GameRosterPlayer(
            owner_user_id=owner.id,
            user_id=outsider.id,
            display_name="Outsider",
        )
        pod = GamePod(owner_user_id=owner.id, name="Just Me")
        db.session.add_all([roster, pod])
        db.session.flush()
        db.session.add(GamePodMember(pod_id=pod.id, roster_player_id=roster.id))

        # Another player not in the pod.
        stranger_user = _create_user("stranger@example.com")
        stranger_player = GamePlayer(user_id=stranger_user.id, display_name="Stranger")
        outsider_player = GamePlayer(user_id=outsider.id, display_name="Outsider")
        db.session.add_all([stranger_player, outsider_player])
        db.session.flush()

        # Session with a pod member — should count.
        _log_game(
            owner,
            [(outsider_player, "Kenrith"), (stranger_player, "Yuriko")],
            winner_seat_index=0,
        )
        # Session without any pod member — should be ignored.
        unrelated = _create_user("unrelated@example.com")
        other_player = GamePlayer(user_id=unrelated.id, display_name="Unrelated")
        db.session.add(other_player)
        db.session.flush()
        _log_game(
            owner,
            [(other_player, "Marchesa"), (stranger_player, "Yuriko")],
            winner_seat_index=0,
        )
        db.session.commit()
        report = playgroup_stats_for_pod(user_id=owner.id, pod_id=pod.id)

    assert report.total_games == 1


def test_playgroup_stats_raises_for_unknown_pod(app, db_session):
    with app.app_context():
        owner = _create_user("nopod@example.com")
        with pytest.raises(LookupError):
            playgroup_stats_for_pod(user_id=owner.id, pod_id=99999)
