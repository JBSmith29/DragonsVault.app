"""Rich, filterable analytics for Game Vault.

compute_metrics() takes a filter set (date range, player, win condition, minimum
games) and returns a bundle of metric groups the Metrics tab renders. All work
is scoped to the owner and confined to the gv_* tables.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

from extensions import db

from ..models import GVDeck, GVGame, GVGameParticipant, GVPlayer, WIN_CONDITIONS


def _parse_date(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) <= 10:
            return datetime.combine(date.fromisoformat(text), datetime.min.time())
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _rate(wins: int, games: int) -> float:
    return round(wins / games * 100, 1) if games else 0.0


def _opt_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def filter_options(owner_user_id: int) -> dict[str, Any]:
    """Players + date bounds to populate the filter bar."""
    players = (
        GVPlayer.query.filter(GVPlayer.owner_user_id == owner_user_id, GVPlayer.archived_at.is_(None))
        .order_by(db.func.lower(GVPlayer.name))
        .all()
    )
    bounds = db.session.query(db.func.min(GVGame.played_at), db.func.max(GVGame.played_at)).filter(
        GVGame.owner_user_id == owner_user_id
    ).one()
    return {
        "players": [{"id": p.id, "name": p.name} for p in players],
        "win_conditions": list(WIN_CONDITIONS),
        "earliest": bounds[0].date().isoformat() if bounds[0] else None,
        "latest": bounds[1].date().isoformat() if bounds[1] else None,
    }


def compute_metrics(owner_user_id: int, *, date_from: str | None = None, date_to: str | None = None,
                    player_id: Any = None, win_condition: str | None = None,
                    min_games: Any = 1) -> dict[str, Any]:
    min_games = max(1, _opt_int(min_games) or 1)
    player_id = _opt_int(player_id)
    wc = (win_condition or "").strip().lower() or None

    q = GVGame.query.filter(GVGame.owner_user_id == owner_user_id)
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    if df:
        q = q.filter(GVGame.played_at >= df)
    if dt:
        q = q.filter(GVGame.played_at < dt + timedelta(days=1))
    if wc:
        q = q.filter(db.func.lower(GVGame.win_condition) == wc)
    games = q.order_by(GVGame.played_at).all()

    game_ids = [g.id for g in games]
    parts = (
        GVGameParticipant.query.filter(GVGameParticipant.game_id.in_(game_ids)).all()
        if game_ids else []
    )

    # Restrict to games a specific player was in.
    if player_id:
        keep = {p.game_id for p in parts if p.player_id == player_id}
        games = [g for g in games if g.id in keep]
        game_ids = [g.id for g in games]
        parts = [p for p in parts if p.game_id in keep]

    played_at = {g.id: g.played_at for g in games}

    # Deck bracket lookup (for linked decks).
    deck_ids = {p.deck_id for p in parts if p.deck_id}
    deck_bracket: dict[int, Any] = {}
    if deck_ids:
        for d in GVDeck.query.filter(GVDeck.id.in_(deck_ids)).all():
            deck_bracket[d.id] = d.bracket

    total_games = len(games)

    # ---- aggregations -------------------------------------------------- #
    players: dict[str, dict[str, Any]] = {}
    decks: dict[str, dict[str, Any]] = {}
    commanders: dict[str, dict[str, Any]] = {}
    turn_order: dict[int, dict[str, Any]] = {}
    brackets: dict[int, dict[str, int]] = {}
    turn_sum: dict[str, list[int]] = defaultdict(list)

    def bump(bucket, key, label, won, extra=None):
        if key is None:
            return
        e = bucket.setdefault(key, {"label": label, "games": 0, "wins": 0})
        e["games"] += 1
        e["wins"] += 1 if won else 0
        if extra:
            e.update(extra)

    for p in parts:
        won = bool(p.is_winner)
        pname = (p.player_name or "").strip() or None
        bump(players, pname and pname.lower(), p.player_name or "Unknown", won)
        if pname and p.turn_order:
            turn_sum[pname.lower()].append(int(p.turn_order))
        if p.deck_name:
            bump(decks, p.deck_name.lower(), p.deck_name, won,
                 extra={"commander": p.commander_name, "bracket": deck_bracket.get(p.deck_id)})
        if p.commander_name:
            bump(commanders, p.commander_name.lower(), p.commander_name, won)
        if p.turn_order:
            seat = int(p.turn_order)
            e = turn_order.setdefault(seat, {"seat": seat, "games": 0, "wins": 0})
            e["games"] += 1
            e["wins"] += 1 if won else 0
        br = deck_bracket.get(p.deck_id)
        if br:
            b = brackets.setdefault(int(br), {"games": 0, "wins": 0})
            b["games"] += 1
            b["wins"] += 1 if won else 0

    def finalize(bucket, *, threshold=1):
        out = []
        for e in bucket.values():
            if e["games"] < threshold:
                continue
            e = dict(e)
            e["win_rate"] = _rate(e["wins"], e["games"])
            out.append(e)
        out.sort(key=lambda x: (-x["win_rate"], -x["games"], x["label"].lower()))
        return out

    player_rows = finalize(players)
    for row in player_rows:
        seats = turn_sum.get(row["label"].lower(), [])
        row["avg_turn_order"] = round(sum(seats) / len(seats), 2) if seats else None

    # Win conditions
    wc_counts: dict[str, int] = defaultdict(int)
    infinite_wins = 0
    turns_vals: list[int] = []
    for g in games:
        if g.infinite_win:
            infinite_wins += 1
        if g.win_condition:
            wc_counts[g.win_condition] += 1
        if g.turns:
            turns_vals.append(int(g.turns))
    win_conditions = [
        {"label": k, "count": v, "pct": _rate(v, total_games)}
        for k, v in sorted(wc_counts.items(), key=lambda kv: -kv[1])
    ]

    # Turn order (first -> last)
    turn_rows = []
    for seat in sorted(turn_order):
        e = turn_order[seat]
        turn_rows.append({"seat": seat, "label": _ordinal(seat) + " to play",
                          "games": e["games"], "wins": e["wins"], "win_rate": _rate(e["wins"], e["games"])})

    # Bracket performance
    bracket_rows = []
    for b in sorted(brackets):
        e = brackets[b]
        bracket_rows.append({"bracket": b, "games": e["games"], "wins": e["wins"],
                             "win_rate": _rate(e["wins"], e["games"])})

    # Infinite wins per player (the winner of each infinite-flagged game).
    winner_by_game = {p.game_id: p for p in parts if p.is_winner}
    inf_by_player: dict[str, int] = defaultdict(int)
    for g in games:
        if g.infinite_win:
            w = winner_by_game.get(g.id)
            name = (w.player_name or "").strip() if w else ""
            if name:
                inf_by_player[name] += 1
    infinite_by_player = sorted(
        ({"label": n, "count": c} for n, c in inf_by_player.items()),
        key=lambda e: (-e["count"], e["label"].lower()),
    )

    # Head-to-head: the focus player's win rate in games each opponent was in.
    head_to_head = []
    if player_id:
        seats_by_game: dict[int, list] = defaultdict(list)
        for p in parts:
            seats_by_game[p.game_id].append(p)
        h2h: dict[str, list] = defaultdict(lambda: [0, 0])  # opponent -> [games, focus_wins]
        for seats in seats_by_game.values():
            focus = next((p for p in seats if p.player_id == player_id), None)
            if not focus:
                continue
            focus_won = bool(focus.is_winner)
            for p in seats:
                if p.player_id == player_id:
                    continue
                name = (p.player_name or "").strip() or "Unknown"
                h2h[name][0] += 1
                h2h[name][1] += 1 if focus_won else 0
        head_to_head = [
            {"label": opp, "games": g, "wins": w, "win_rate": _rate(w, g)}
            for opp, (g, w) in h2h.items()
        ]
        head_to_head.sort(key=lambda e: (-e["games"], e["label"].lower()))

    return {
        "summary": {
            "games": total_games,
            "players": len(players),
            "decks": len(decks),
            "avg_turns": round(sum(turns_vals) / len(turns_vals), 1) if turns_vals else None,
            "combo_pct": _rate(wc_counts.get("combo", 0), total_games),
            "infinite_pct": _rate(infinite_wins, total_games),
            "infinite_wins": infinite_wins,
        },
        "players": player_rows,
        "decks": finalize(decks, threshold=min_games),
        "commanders": finalize(commanders, threshold=min_games),
        "turn_order": turn_rows,
        "win_conditions": win_conditions,
        "brackets": bracket_rows,
        "infinite_by_player": infinite_by_player,
        "head_to_head": head_to_head,
        "applied": {
            "date_from": df.date().isoformat() if df else None,
            "date_to": dt.date().isoformat() if dt else None,
            "player_id": player_id,
            "win_condition": wc,
            "min_games": min_games,
        },
    }


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


__all__ = ["compute_metrics", "filter_options"]
