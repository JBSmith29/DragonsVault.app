"""Context and render helpers for the game session form flow."""

from __future__ import annotations

from typing import Any

from flask import flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy.orm import selectinload

from . import game_compat_service as legacy
from . import game_session_shared_service as session_shared


def game_detail(game_id: int):
    session = (
        legacy.GameSession.query.options(
            selectinload(legacy.GameSession.seats)
            .selectinload(legacy.GameSeat.assignment)
            .selectinload(legacy.GameSeatAssignment.player),
            selectinload(legacy.GameSession.seats)
            .selectinload(legacy.GameSeat.assignment)
            .selectinload(legacy.GameSeatAssignment.deck),
        )
        .filter(legacy.GameSession.id == game_id)
        .first()
    )
    if not session:
        flash("Game session not found.", "warning")
        return redirect(url_for("views.games_overview"))
    is_participant = any(
        seat.assignment and seat.assignment.player and seat.assignment.player.user_id == current_user.id
        for seat in (session.seats or [])
    )
    if not (session.owner_user_id == current_user.id or is_participant):
        flash("Game session not found.", "warning")
        return redirect(url_for("views.games_overview"))
    game = session_shared._game_session_payload(session, current_user.id)
    can_edit = session.owner_user_id == current_user.id
    return render_template("games/detail.html", game=game, can_edit=can_edit)


def game_form_context() -> dict[str, Any]:
    roster_players = legacy._roster_players(current_user.id)
    roster_map = {player["id"]: player for player in roster_players}
    roster_deck_refs = {
        player["id"]: {deck["ref"] for deck in player.get("deck_options", [])}
        for player in roster_players
    }
    guest_deck_options = legacy._accessible_deck_options(current_user.id)
    manual_link_options = legacy._accessible_deck_options()
    guest_deck_refs = {deck["ref"] for deck in guest_deck_options}
    pods = legacy._pod_payloads_for_owner(current_user.id, roster_players)
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


def default_game_form_data(context: dict[str, Any]) -> dict[str, Any]:
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


def game_form_data_from_session(
    session: legacy.GameSession,
    context: dict[str, Any],
    folder_map: dict[int, legacy.Folder] | None = None,
) -> dict[str, Any]:
    roster_players = context["roster_players"]
    pods = context["pods"]
    roster_by_user_id = {player["user_id"]: player["id"] for player in roster_players if player.get("user_id")}
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
                    manual_commander_name = session_shared._oracle_name_from_id(deck.commander_oracle_id) or ""
                if not manual_commander_name and linked_folder:
                    manual_commander_name = linked_folder.commander_name or ""
                if not manual_commander_name and linked_folder and linked_folder.commander_oracle_id:
                    manual_commander_name = session_shared._oracle_name_from_id(linked_folder.commander_oracle_id) or ""

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


def render_game_form(context: dict[str, Any], form_data: dict[str, Any], *, is_edit: bool, game_id: int | None):
    return render_template(
        "games/new.html",
        roster_players=context["roster_players"],
        roster_deck_map=context["roster_map"],
        guest_deck_options=context["guest_deck_options"],
        manual_link_options=context["manual_link_options"],
        pods=context["pods"],
        pod_member_map=context["pod_member_map"],
        form_data=form_data,
        is_edit=is_edit,
        game_id=game_id,
    )
