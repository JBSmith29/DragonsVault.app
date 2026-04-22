"""Persistence helpers for the game session form flow."""

from __future__ import annotations

from flask_login import current_user

from extensions import db

from . import game_compat_service as legacy
from . import game_session_shared_service as session_shared


def persist_game_session(
    session: legacy.GameSession,
    seat_payloads: list[dict[str, object]],
    folders_by_id: dict[int, legacy.Folder],
    manual_decks_by_id: dict[int, legacy.GameRosterDeck],
    winner_seat: int | None,
) -> None:
    seats_by_number: dict[int, legacy.GameSeat] = {}
    for payload in seat_payloads:
        seat = legacy.GameSeat(
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

        player = legacy.GamePlayer(user_id=getattr(player_user, "id", None), display_name=player_label)
        db.session.add(player)

        deck_kind = payload.get("deck_kind")
        deck_id = payload.get("deck_lookup_id")
        if deck_kind == "folder":
            folder = folders_by_id.get(deck_id)
            deck_snapshot = session_shared._snapshot_deck(folder)
        elif deck_kind == "manual_entry":
            manual_name = payload.get("manual_deck_name") or "Deck"
            manual_commander = payload.get("manual_commander_name") or None
            manual_link_id = payload.get("manual_link_id")
            manual_link_folder = folders_by_id.get(manual_link_id) if manual_link_id else None
            resolved_folder = session_shared._find_deck_by_name(player_user.id, manual_name) if player_user and manual_name else None
            if manual_link_folder:
                deck_snapshot = session_shared._snapshot_deck(manual_link_folder)
                deck_snapshot["deck_name"] = manual_name
            elif resolved_folder:
                deck_snapshot = session_shared._snapshot_deck(resolved_folder)
                deck_snapshot["deck_name"] = manual_name
            else:
                commander_oracle_id = None
                if manual_commander:
                    try:
                        legacy.sc.ensure_cache_loaded()
                        commander_oracle_id = legacy.sc.unique_oracle_by_name(manual_commander)
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
                session_shared._find_deck_by_name(player_user.id, manual.deck_name)
                if manual and player_user and manual.deck_name
                else None
            )
            if manual_link_folder:
                deck_snapshot = session_shared._snapshot_deck(manual_link_folder)
                if manual and manual.deck_name:
                    deck_snapshot["deck_name"] = manual.deck_name
                if manual and not manual.folder_id:
                    manual.folder_id = manual_link_folder.id
            elif resolved_folder:
                deck_snapshot = session_shared._snapshot_deck(resolved_folder)
                if manual and manual.deck_name:
                    deck_snapshot["deck_name"] = manual.deck_name
                if manual and not manual.folder_id:
                    manual.folder_id = resolved_folder.id
            else:
                commander_oracle_id = None
                if manual_commander:
                    try:
                        legacy.sc.ensure_cache_loaded()
                        commander_oracle_id = legacy.sc.unique_oracle_by_name(manual_commander)
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
        deck = legacy.GameDeck(
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

        assignment = legacy.GameSeatAssignment(
            session_id=session.id,
            seat=seats_by_number[payload["seat_number"]],
            player=player,
            deck=deck,
        )
        db.session.add(assignment)


def apply_auto_assignments(auto_assignments: list[tuple[int, int]]) -> None:
    for roster_id, deck_id in auto_assignments:
        existing = legacy.GameRosterDeck.query.filter_by(roster_player_id=roster_id, folder_id=deck_id).first()
        if not existing:
            db.session.add(
                legacy.GameRosterDeck(
                    roster_player_id=roster_id,
                    owner_user_id=current_user.id,
                    folder_id=deck_id,
                )
            )
