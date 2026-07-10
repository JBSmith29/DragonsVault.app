"""Orchestration for Game Vault: player/deck/game CRUD, import, sync, stats.

All queries are scoped by ``owner_user_id`` and never touch tables outside the
``gv_*`` set.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func

from extensions import db
from core.shared.utils.time import utcnow

from ..models import (
    GVDeck,
    GVGame,
    GVGameParticipant,
    GVPlayer,
    KNOWN_SOURCES,
    WIN_CONDITIONS,
)
from . import scryfall_lookup
from .importers import (
    DeckImportError,
    detect_source,
    fetch_deck,
    import_from_url,
    list_user_decks,
    supports_username_listing,
)


class VaultError(ValueError):
    """User-facing validation / operation error (maps to HTTP 400)."""


# --------------------------------------------------------------------------- #
# Players
# --------------------------------------------------------------------------- #
def list_players(owner_user_id: int, *, include_decks: bool = True) -> list[dict[str, Any]]:
    players = (
        GVPlayer.query.filter(
            GVPlayer.owner_user_id == owner_user_id,
            GVPlayer.archived_at.is_(None),
        )
        .order_by(func.lower(GVPlayer.name))
        .all()
    )
    return [p.to_dict(include_decks=include_decks) for p in players]


def _get_player(owner_user_id: int, player_id: int) -> GVPlayer:
    player = GVPlayer.query.filter_by(id=player_id, owner_user_id=owner_user_id).first()
    if not player or player.archived_at is not None:
        raise VaultError("Player not found.")
    return player


def create_player(owner_user_id: int, name: str, *, note: str | None = None,
                  color: str | None = None) -> GVPlayer:
    clean = (name or "").strip()
    if not clean:
        raise VaultError("Enter a player name.")
    if len(clean) > 120:
        raise VaultError("Player name is too long.")
    exists = GVPlayer.query.filter(
        GVPlayer.owner_user_id == owner_user_id,
        func.lower(GVPlayer.name) == clean.lower(),
        GVPlayer.archived_at.is_(None),
    ).first()
    if exists:
        raise VaultError(f"You already have a player named “{clean}”.")
    player = GVPlayer(
        owner_user_id=owner_user_id,
        name=clean,
        note=(note or "").strip() or None,
        color=(color or "").strip() or None,
    )
    db.session.add(player)
    db.session.commit()
    return player


def update_player(owner_user_id: int, player_id: int, **fields: Any) -> GVPlayer:
    player = _get_player(owner_user_id, player_id)
    if "name" in fields:
        clean = (fields["name"] or "").strip()
        if not clean:
            raise VaultError("Enter a player name.")
        dup = GVPlayer.query.filter(
            GVPlayer.owner_user_id == owner_user_id,
            func.lower(GVPlayer.name) == clean.lower(),
            GVPlayer.id != player.id,
            GVPlayer.archived_at.is_(None),
        ).first()
        if dup:
            raise VaultError(f"You already have a player named “{clean}”.")
        player.name = clean
    if "note" in fields:
        player.note = (fields["note"] or "").strip() or None
    if "color" in fields:
        player.color = (fields["color"] or "").strip() or None
    db.session.commit()
    return player


def delete_player(owner_user_id: int, player_id: int) -> None:
    player = _get_player(owner_user_id, player_id)
    db.session.delete(player)
    db.session.commit()


# --------------------------------------------------------------------------- #
# Decks
# --------------------------------------------------------------------------- #
def list_source_decks(source: str, username: str, *, limit: int = 60) -> list[dict[str, Any]]:
    source = (source or "").strip().lower()
    if source not in KNOWN_SOURCES:
        raise VaultError("Unknown deck source.")
    if not supports_username_listing(source):
        raise VaultError(f"{source.title()} can't list decks by username — paste a deck link instead.")
    try:
        return list_user_decks(source, username, limit=limit)
    except DeckImportError as exc:
        raise VaultError(str(exc)) from exc


def _run_import(*, url: str | None, source: str | None, deck_ref: str | None):
    try:
        if url:
            return import_from_url(url)
        if source and deck_ref:
            source = source.strip().lower()
            if source not in KNOWN_SOURCES:
                raise VaultError("Unknown deck source.")
            return fetch_deck(source, deck_ref)
    except DeckImportError as exc:
        raise VaultError(str(exc)) from exc
    raise VaultError("Provide a deck link to import.")


def _apply_imported(deck: GVDeck, imported) -> None:
    deck.source = imported.source
    deck.source_id = imported.source_id
    deck.url = imported.url
    deck.name = imported.name
    deck.commander_name = imported.commander_name
    deck.color_identity = imported.color_identity
    deck.format = imported.format
    # A hand-set bracket is authoritative — never clobber it on re-sync.
    if not deck.bracket_manual:
        deck.bracket = imported.bracket
        deck.bracket_is_estimated = bool(getattr(imported, "bracket_estimated", False))
    deck.card_count = imported.card_count
    deck.cards = imported.cards

    # Best-effort enrichment: commander art + color identity from Scryfall.
    if imported.commander_name:
        image, identity = scryfall_lookup.lookup_commander(imported.commander_name)
        if image:
            deck.commander_image = image
        if identity and not deck.color_identity:
            deck.color_identity = identity

    deck.last_synced_at = utcnow()
    deck.sync_status = "ok"
    deck.sync_error = None


def import_deck(owner_user_id: int, player_id: int, *, url: str | None = None,
                source: str | None = None, deck_ref: str | None = None) -> GVDeck:
    player = _get_player(owner_user_id, player_id)
    imported = _run_import(url=url, source=source, deck_ref=deck_ref)

    # Re-importing the same source deck updates the existing row.
    existing = None
    if imported.source_id:
        existing = GVDeck.query.filter_by(
            owner_user_id=owner_user_id,
            player_id=player.id,
            source=imported.source,
            source_id=imported.source_id,
        ).first()

    deck = existing or GVDeck(owner_user_id=owner_user_id, player_id=player.id)
    deck.archived_at = None
    _apply_imported(deck, imported)
    if existing is None:
        db.session.add(deck)
    db.session.commit()
    return deck


def sync_deck(owner_user_id: int, deck_id: int) -> GVDeck:
    deck = GVDeck.query.filter_by(id=deck_id, owner_user_id=owner_user_id).first()
    if not deck:
        raise VaultError("Deck not found.")
    if not deck.url and not (deck.source and deck.source_id):
        raise VaultError("This deck has no source link to sync from.")
    try:
        imported = _run_import(url=deck.url, source=deck.source, deck_ref=deck.source_id)
    except VaultError as exc:
        deck.sync_status = "error"
        deck.sync_error = str(exc)[:255]
        deck.last_synced_at = utcnow()
        db.session.commit()
        raise
    _apply_imported(deck, imported)
    db.session.commit()
    return deck


def sync_all_decks(owner_user_id: int) -> dict[str, int]:
    decks = GVDeck.query.filter(
        GVDeck.owner_user_id == owner_user_id,
        GVDeck.archived_at.is_(None),
    ).all()
    ok = errors = 0
    for deck in decks:
        try:
            sync_deck(owner_user_id, deck.id)
            ok += 1
        except VaultError:
            errors += 1
    return {"synced": ok, "errors": errors, "total": len(decks)}


def delete_deck(owner_user_id: int, deck_id: int) -> None:
    deck = GVDeck.query.filter_by(id=deck_id, owner_user_id=owner_user_id).first()
    if not deck:
        raise VaultError("Deck not found.")
    db.session.delete(deck)
    db.session.commit()


def get_deck_detail(owner_user_id: int, deck_id: int) -> dict[str, Any]:
    """Full deck payload including the card list (fetched on demand)."""
    deck = GVDeck.query.filter_by(id=deck_id, owner_user_id=owner_user_id).first()
    if not deck:
        raise VaultError("Deck not found.")
    data = deck.to_dict()
    data["cards"] = deck.cards or []
    return data


def games_csv(owner_user_id: int) -> str:
    """Flatten the owner's games into a CSV (one row per game, seats as cols)."""
    import csv
    import io

    games = (
        GVGame.query.filter_by(owner_user_id=owner_user_id)
        .order_by(GVGame.played_at.desc(), GVGame.id.desc())
        .all()
    )
    max_seats = max([len(g.participants or []) for g in games] + [1])

    out = io.StringIO()
    writer = csv.writer(out)
    header = ["game_id", "played_at", "format", "turns", "win_condition",
              "infinite_win", "winner", "player_count", "notes"]
    for i in range(1, max_seats + 1):
        header += [f"seat_{i}_player", f"seat_{i}_deck", f"seat_{i}_commander",
                   f"seat_{i}_turn_order", f"seat_{i}_winner"]
    writer.writerow(header)

    for g in games:
        seats = sorted(g.participants or [], key=lambda p: (p.turn_order if p.turn_order is not None else 99))
        winner = next((p.player_name for p in seats if p.is_winner), "")
        row = [
            g.id,
            g.played_at.strftime("%Y-%m-%d") if g.played_at else "",
            g.format or "", g.turns if g.turns is not None else "",
            g.win_condition or "", "yes" if g.infinite_win else "",
            winner, len(seats), g.notes or "",
        ]
        for i in range(max_seats):
            if i < len(seats):
                p = seats[i]
                row += [p.player_name or "", p.deck_name or "", p.commander_name or "",
                        p.turn_order if p.turn_order is not None else "", "yes" if p.is_winner else ""]
            else:
                row += ["", "", "", "", ""]
        writer.writerow(row)
    return out.getvalue()


def set_deck_bracket(owner_user_id: int, deck_id: int, bracket: Any) -> GVDeck:
    """Hand-set a deck's bracket (1-5), or clear the override (bracket=None).

    A manual bracket is authoritative and survives re-syncs. Clearing it reverts
    to the source's bracket/estimate (re-pulled immediately for source decks)."""
    deck = GVDeck.query.filter_by(id=deck_id, owner_user_id=owner_user_id).first()
    if not deck:
        raise VaultError("Deck not found.")

    value = _opt_int(bracket)
    if value is None:
        deck.bracket_manual = False
        deck.bracket = None
        deck.bracket_is_estimated = False
        db.session.commit()
        if deck.url or (deck.source and deck.source_id):
            try:
                return sync_deck(owner_user_id, deck_id)
            except VaultError:
                pass
        return deck

    if value < 1 or value > 5:
        raise VaultError("Bracket must be between 1 and 5.")
    deck.bracket = value
    deck.bracket_manual = True
    deck.bracket_is_estimated = False
    db.session.commit()
    return deck


# --------------------------------------------------------------------------- #
# Games
# --------------------------------------------------------------------------- #
def _parse_played_at(raw: str | None) -> datetime:
    if not raw:
        return utcnow()
    value = str(raw).strip()
    try:
        if len(value) <= 10:
            return datetime.combine(date.fromisoformat(value), datetime.min.time())
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise VaultError("Played-at must be a valid date.")


def list_games(owner_user_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
    games = (
        GVGame.query.filter(GVGame.owner_user_id == owner_user_id)
        .order_by(GVGame.played_at.desc(), GVGame.id.desc())
        .limit(limit)
        .all()
    )
    return [g.to_dict() for g in games]


def _normalize_win_condition(win_condition: str | None) -> str | None:
    wc = (win_condition or "").strip().lower() or None
    if wc and wc not in WIN_CONDITIONS:
        wc = "other"
    return wc


def _build_participants(owner_user_id: int, participants) -> list[GVGameParticipant]:
    """Validate a participants payload and return unattached GVGameParticipant
    rows. Shared by create_game and update_game."""
    parts = list(participants or [])
    if len(parts) < 2:
        raise VaultError("Log at least two players.")
    if len(parts) > 8:
        raise VaultError("A game can have at most eight players.")

    winners = 0
    built: list[GVGameParticipant] = []
    for idx, raw in enumerate(parts, start=1):
        player = None
        deck = None
        player_id = _opt_int(raw.get("player_id"))
        deck_id = _opt_int(raw.get("deck_id"))
        if player_id:
            player = GVPlayer.query.filter_by(id=player_id, owner_user_id=owner_user_id).first()
            if not player:
                raise VaultError("One of the selected players no longer exists.")
        if deck_id:
            deck = GVDeck.query.filter_by(id=deck_id, owner_user_id=owner_user_id).first()
            if not deck:
                raise VaultError("One of the selected decks no longer exists.")
        player_name = (raw.get("player_name") or (player.name if player else "")).strip()
        if not player_name:
            raise VaultError(f"Seat {idx}: choose or name a player.")
        is_winner = bool(raw.get("is_winner"))
        winners += 1 if is_winner else 0
        # When no deck is chosen, preserve any snapshot passed by the client
        # (this keeps imported games' deck/commander names when editing).
        snap_deck = (raw.get("deck_name") or "").strip() or None
        snap_cmd = (raw.get("commander_name") or "").strip() or None
        built.append(
            GVGameParticipant(
                player_id=player.id if player else None,
                deck_id=deck.id if deck else None,
                player_name=player_name[:120],
                deck_name=(deck.name if deck else snap_deck),
                commander_name=(deck.commander_name if deck else snap_cmd),
                turn_order=_opt_int(raw.get("turn_order")) or idx,
                is_winner=is_winner,
            )
        )
    if winners > 1:
        raise VaultError("Only one player can be marked the winner.")
    return built


def create_game(owner_user_id: int, *, played_at: str | None, format: str | None = None,
                turns: Any = None, duration_minutes: Any = None,
                win_condition: str | None = None, infinite_win: Any = False,
                notes: str | None = None,
                participants: Iterable[dict[str, Any]] | None = None) -> GVGame:
    built = _build_participants(owner_user_id, participants)
    game = GVGame(
        owner_user_id=owner_user_id,
        played_at=_parse_played_at(played_at),
        # This is a Commander-only logger; format is fixed.
        format=(format or "commander").strip().lower() or "commander",
        turns=_opt_int(turns),
        duration_minutes=_opt_int(duration_minutes),
        win_condition=_normalize_win_condition(win_condition),
        infinite_win=bool(infinite_win),
        notes=(notes or "").strip() or None,
    )
    for p in built:
        game.participants.append(p)
    db.session.add(game)
    db.session.commit()
    return game


def update_game(owner_user_id: int, game_id: int, *, played_at: str | None = None,
                turns: Any = None, duration_minutes: Any = None,
                win_condition: str | None = None, infinite_win: Any = False,
                notes: str | None = None,
                participants: Iterable[dict[str, Any]] | None = None) -> GVGame:
    game = GVGame.query.filter_by(id=game_id, owner_user_id=owner_user_id).first()
    if not game:
        raise VaultError("Game not found.")
    built = _build_participants(owner_user_id, participants)

    if played_at:
        game.played_at = _parse_played_at(played_at)
    game.turns = _opt_int(turns)
    game.duration_minutes = _opt_int(duration_minutes)
    game.win_condition = _normalize_win_condition(win_condition)
    game.infinite_win = bool(infinite_win)
    game.notes = (notes or "").strip() or None

    # Replace the seats wholesale (delete-orphan cleans up the old rows).
    game.participants.clear()
    db.session.flush()
    for p in built:
        game.participants.append(p)
    db.session.commit()
    return game


def delete_game(owner_user_id: int, game_id: int) -> None:
    game = GVGame.query.filter_by(id=game_id, owner_user_id=owner_user_id).first()
    if not game:
        raise VaultError("Game not found.")
    db.session.delete(game)
    db.session.commit()


# --------------------------------------------------------------------------- #
# Deck mapping — link game-log deck snapshots to a player's real decks
# --------------------------------------------------------------------------- #
def deck_mapping_overview(owner_user_id: int) -> dict[str, Any]:
    """For each player, list the distinct COMMANDERS that appear in their game
    history alongside the player's current decks to map them onto. Where a
    current deck already runs that commander, it's offered as a confident,
    pre-selected suggestion so you only confirm the sure matches."""
    rows = (
        db.session.query(GVGameParticipant)
        .join(GVGame, GVGame.id == GVGameParticipant.game_id)
        .filter(
            GVGame.owner_user_id == owner_user_id,
            GVGameParticipant.player_id.isnot(None),
            GVGameParticipant.commander_name.isnot(None),
        )
        .all()
    )
    by_player: dict[int, dict[str, dict[str, Any]]] = {}
    for p in rows:
        cmd = (p.commander_name or "").strip()
        if not cmd:
            continue
        bucket = by_player.setdefault(p.player_id, {})
        entry = bucket.setdefault(
            cmd.lower(),
            {"commander_name": cmd, "count": 0, "deck_ids": set()},
        )
        entry["count"] += 1
        if p.deck_id:
            entry["deck_ids"].add(p.deck_id)

    players = (
        GVPlayer.query.filter(
            GVPlayer.owner_user_id == owner_user_id,
            GVPlayer.archived_at.is_(None),
        )
        .order_by(func.lower(GVPlayer.name))
        .all()
    )
    out: list[dict[str, Any]] = []
    for player in players:
        game_cmds = by_player.get(player.id)
        if not game_cmds:
            continue
        decks = [d for d in (player.decks or []) if d.archived_at is None]
        by_commander = {}
        for d in decks:
            key = (d.commander_name or "").strip().lower()
            if key:
                by_commander.setdefault(key, d.id)

        out.append(
            {
                "id": player.id,
                "name": player.name,
                "decks": [
                    {"id": d.id, "name": d.name, "commander_name": d.commander_name}
                    for d in decks
                ],
                "game_commanders": [
                    {
                        "commander_name": e["commander_name"],
                        "count": e["count"],
                        "mapped_deck_id": (next(iter(e["deck_ids"])) if len(e["deck_ids"]) == 1 else None),
                        "suggested_deck_id": by_commander.get(e["commander_name"].lower()),
                    }
                    for e in sorted(game_cmds.values(), key=lambda x: (-x["count"], x["commander_name"].lower()))
                ],
            }
        )
    return {"players": out}


def apply_deck_mapping(owner_user_id: int, mappings: Iterable[dict[str, Any]] | None) -> dict[str, int]:
    """Point every matching game seat at a player's chosen deck.

    Each mapping is {player_id, commander_name, deck_id}. deck_id null unlinks
    the seats (keeping the current commander). Matching is by player + the
    exact (case-insensitive) commander currently stored on the seat.
    """
    updated = 0
    mapped = 0
    for m in list(mappings or []):
        player_id = _opt_int(m.get("player_id"))
        commander_name = (m.get("commander_name") or "").strip()
        deck_id = _opt_int(m.get("deck_id"))
        if not player_id or not commander_name:
            continue
        deck = None
        if deck_id:
            deck = GVDeck.query.filter_by(
                id=deck_id, owner_user_id=owner_user_id, player_id=player_id
            ).first()
            if not deck:
                raise VaultError("A selected deck doesn't belong to that player.")
        seats = (
            db.session.query(GVGameParticipant)
            .join(GVGame, GVGame.id == GVGameParticipant.game_id)
            .filter(
                GVGame.owner_user_id == owner_user_id,
                GVGameParticipant.player_id == player_id,
                func.lower(GVGameParticipant.commander_name) == commander_name.lower(),
            )
            .all()
        )
        for seat in seats:
            if deck:
                seat.deck_id = deck.id
                seat.deck_name = deck.name
                if deck.commander_name:
                    seat.commander_name = deck.commander_name
            else:
                seat.deck_id = None
            updated += 1
        if seats:
            mapped += 1
    db.session.commit()
    return {"seats_updated": updated, "decks_mapped": mapped}


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #
def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def compute_stats(owner_user_id: int) -> dict[str, Any]:
    participants = (
        db.session.query(GVGameParticipant)
        .join(GVGame, GVGame.id == GVGameParticipant.game_id)
        .filter(GVGame.owner_user_id == owner_user_id)
        .all()
    )
    games = GVGame.query.filter_by(owner_user_id=owner_user_id).all()
    total_games = len(games)

    players: dict[str, dict[str, Any]] = {}
    decks: dict[str, dict[str, Any]] = {}
    commanders: dict[str, dict[str, Any]] = {}
    turn_order: dict[int, dict[str, Any]] = {}

    def bump(bucket: dict[Any, dict[str, Any]], key: Any, label: str, winner: bool):
        if key is None:
            return
        entry = bucket.setdefault(key, {"label": label, "games": 0, "wins": 0})
        entry["games"] += 1
        entry["wins"] += 1 if winner else 0

    max_seat = 0
    for participant in participants:
        won = bool(participant.is_winner)
        bump(players, (participant.player_name or "").lower() or None,
             participant.player_name or "Unknown", won)
        if participant.deck_name:
            bump(decks, participant.deck_name.lower(), participant.deck_name, won)
        if participant.commander_name:
            bump(commanders, participant.commander_name.lower(), participant.commander_name, won)
        seat = participant.turn_order
        if seat:
            max_seat = max(max_seat, int(seat))
            bump(turn_order, int(seat), f"{_ordinal(int(seat))} to play", won)

    def finalize(bucket: dict[Any, dict[str, Any]], *, sort_by_key: bool = False) -> list[dict[str, Any]]:
        out = []
        for entry in bucket.values():
            g = entry["games"]
            entry["win_rate"] = round(entry["wins"] / g * 100, 1) if g else 0.0
            out.append(entry)
        if sort_by_key:
            out.sort(key=lambda e: e["seat"])
        else:
            out.sort(key=lambda e: (-e["win_rate"], -e["games"], e["label"].lower()))
        return out

    # Tag turn-order entries with their seat number for ordered display.
    for seat, entry in turn_order.items():
        entry["seat"] = seat

    # Win-condition breakdown + infinite wins (game-level).
    win_conditions: dict[str, int] = {}
    infinite_wins = 0
    for game in games:
        if game.infinite_win:
            infinite_wins += 1
        wc = (game.win_condition or "").strip().lower()
        if wc:
            win_conditions[wc] = win_conditions.get(wc, 0) + 1

    return {
        "total_games": int(total_games),
        "total_players": GVPlayer.query.filter_by(owner_user_id=owner_user_id)
        .filter(GVPlayer.archived_at.is_(None)).count(),
        "total_decks": GVDeck.query.filter_by(owner_user_id=owner_user_id)
        .filter(GVDeck.archived_at.is_(None)).count(),
        "players": finalize(players),
        "decks": finalize(decks),
        "commanders": finalize(commanders),
        "turn_order": finalize(turn_order, sort_by_key=True),
        "win_conditions": [
            {"label": wc, "count": n}
            for wc, n in sorted(win_conditions.items(), key=lambda kv: -kv[1])
        ],
        "infinite_wins": infinite_wins,
        "pod_size": max_seat,
    }


def _opt_int(value: Any) -> Optional[int]:
    if value in (None, "", False):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "VaultError",
    "list_players", "create_player", "update_player", "delete_player",
    "list_source_decks", "import_deck", "sync_deck", "sync_all_decks", "delete_deck",
    "set_deck_bracket", "get_deck_detail", "games_csv",
    "list_games", "create_game", "update_game", "delete_game", "compute_stats",
    "deck_mapping_overview", "apply_deck_mapping",
    "detect_source",
]
