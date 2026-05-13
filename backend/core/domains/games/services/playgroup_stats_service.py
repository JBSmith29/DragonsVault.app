"""Aggregated playgroup/pod statistics.

Given a pod id (or an arbitrary list of player ids), produce:

* win counts per player
* commander/deck frequency
* meta diversity (Shannon entropy over commander names)
* longest win streak per player
* total games played together

Everything is read-only and respects the pod owner's authorization.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import selectinload

from extensions import db
from models import (
    GamePod,
    GameSeat,
    GameSeatAssignment,
    GameSession,
)


__all__ = [
    "PlayerStats",
    "CommanderStats",
    "PlaygroupReport",
    "playgroup_stats_for_pod",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PlayerStats:
    player_id: int
    display_name: str
    games: int = 0
    wins: int = 0
    longest_streak: int = 0

    @property
    def win_rate(self) -> float | None:
        return (self.wins / self.games) if self.games else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "display_name": self.display_name,
            "games": self.games,
            "wins": self.wins,
            "win_rate": self.win_rate,
            "longest_streak": self.longest_streak,
        }


@dataclass
class CommanderStats:
    commander_name: str
    games: int = 0
    wins: int = 0

    @property
    def win_rate(self) -> float | None:
        return (self.wins / self.games) if self.games else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "commander_name": self.commander_name,
            "games": self.games,
            "wins": self.wins,
            "win_rate": self.win_rate,
        }


@dataclass
class PlaygroupReport:
    pod_id: int | None
    pod_name: str | None
    total_games: int
    meta_entropy: float | None
    players: list[PlayerStats]
    commanders: list[CommanderStats]
    raw_counts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pod_id": self.pod_id,
            "pod_name": self.pod_name,
            "total_games": self.total_games,
            "meta_entropy": self.meta_entropy,
            "players": [p.to_dict() for p in self.players],
            "commanders": [c.to_dict() for c in self.commanders],
            "raw_counts": dict(self.raw_counts),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_pod(pod_id: int, owner_user_id: int) -> GamePod:
    pod = (
        db.session.query(GamePod)
        .options(selectinload(GamePod.members))
        .filter(GamePod.id == pod_id)
        .filter(GamePod.owner_user_id == owner_user_id)
        .one_or_none()
    )
    if pod is None:
        raise LookupError(f"Pod {pod_id} not found or not owned by user {owner_user_id}.")
    return pod


def _player_identity(assignment: GameSeatAssignment) -> tuple[int | None, str]:
    """Return a stable (player_id, label) key for a game assignment.

    ``GamePlayer`` records may not link to a ``User``, so we fall back to the
    display name to keep unknown opponents distinct.
    """
    player = assignment.player
    if not player:
        return (None, "Unknown player")
    if player.user_id:
        return (player.user_id, player.display_name or f"Player {player.user_id}")
    return (None, player.display_name or f"Player {player.id}")


def _shannon_entropy(counter: Counter[str]) -> float | None:
    total = sum(counter.values())
    if total <= 0:
        return None
    entropy = 0.0
    for count in counter.values():
        if count <= 0:
            continue
        p = count / total
        entropy -= p * math.log2(p)
    return round(entropy, 4)


def _longest_streaks(player_win_sequence: dict[int, list[bool]]) -> dict[int, int]:
    out: dict[int, int] = {}
    for key, sequence in player_win_sequence.items():
        streak = 0
        best = 0
        for win in sequence:
            if win:
                streak += 1
                best = max(best, streak)
            else:
                streak = 0
        out[key] = best
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def playgroup_stats_for_pod(
    *,
    user_id: int,
    pod_id: int,
) -> PlaygroupReport:
    """Compute playgroup stats for a pod owned by ``user_id``."""
    pod = _resolve_pod(pod_id, owner_user_id=user_id)

    # Resolve player identities that belong to the pod (roster-based).
    roster_player_ids: set[int | None] = set()
    for member in pod.members:
        roster_player = member.roster_player
        if roster_player and roster_player.user_id:
            roster_player_ids.add(roster_player.user_id)
        elif roster_player:
            roster_player_ids.add(None)  # signals anonymous roster members

    sessions = (
        db.session.query(GameSession)
        .filter(GameSession.owner_user_id == user_id)
        .options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .order_by(GameSession.played_at.asc(), GameSession.created_at.asc())
        .all()
    )

    commander_counts: Counter[str] = Counter()
    commander_wins: Counter[str] = Counter()
    player_stats: dict[tuple[int | None, str], PlayerStats] = {}
    player_win_sequence: dict[tuple[int | None, str], list[bool]] = defaultdict(list)
    total_games = 0

    for session in sessions:
        # The session must feature at least one roster member for us to
        # count it as a pod game. This keeps solo games and other groups out.
        session_player_ids = set()
        for seat in session.seats:
            if not seat.assignment:
                continue
            pid, _ = _player_identity(seat.assignment)
            session_player_ids.add(pid)
        if None in session_player_ids and not session_player_ids & roster_player_ids:
            # No linked users recognised from the pod roster.
            continue
        if not (session_player_ids & roster_player_ids):
            continue
        total_games += 1

        for seat in session.seats:
            assignment = seat.assignment
            if not assignment:
                continue
            player_key, label = _player_identity(assignment)
            key = (player_key, label)
            stats = player_stats.setdefault(
                key,
                PlayerStats(player_id=player_key or 0, display_name=label),
            )
            stats.games += 1
            is_winner = session.winner_seat_id == seat.id
            if is_winner:
                stats.wins += 1
            player_win_sequence[key].append(is_winner)

            deck = assignment.deck
            if deck and deck.commander_name:
                name = deck.commander_name.strip()
                commander_counts[name] += 1
                if is_winner:
                    commander_wins[name] += 1

    streak_map = _longest_streaks(
        {key: sequence for key, sequence in player_win_sequence.items()}
    )
    for key, stats in player_stats.items():
        stats.longest_streak = streak_map.get(key, 0)

    commanders = [
        CommanderStats(
            commander_name=name,
            games=count,
            wins=commander_wins.get(name, 0),
        )
        for name, count in commander_counts.most_common()
    ]
    entropy = _shannon_entropy(commander_counts)
    ordered_players = sorted(
        player_stats.values(),
        key=lambda row: (-row.wins, -row.games, row.display_name.lower()),
    )

    return PlaygroupReport(
        pod_id=pod.id,
        pod_name=pod.name,
        total_games=total_games,
        meta_entropy=entropy,
        players=ordered_players,
        commanders=commanders,
        raw_counts={
            "commanders": dict(commander_counts),
            "player_wins": {
                f"{key[1]} ({key[0]})": row.wins for key, row in player_stats.items()
            },
        },
    )
