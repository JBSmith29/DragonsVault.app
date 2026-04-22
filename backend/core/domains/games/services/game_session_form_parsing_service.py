"""Parsing and lookup helpers for the game session form flow."""

from __future__ import annotations

from flask import request
from flask_login import current_user

from . import game_compat_service as legacy
from . import game_session_shared_service as session_shared


def parse_game_form(context: dict[str, object]) -> dict[str, object]:
    roster_map = context["roster_map"]
    roster_deck_refs = context["roster_deck_refs"]
    guest_deck_refs = context["guest_deck_refs"]

    errors: list[str] = []
    seat_count_raw = request.form.get("seat_count")
    try:
        seat_count = legacy.parse_positive_int(seat_count_raw, field="player count", min_value=2)
    except legacy.ValidationError as exc:
        legacy.log_validation_error(exc, context="game_create")
        errors.append("Player count must be between 2 and 4.")
        seat_count = 4
    if seat_count > 4:
        errors.append("Player count must be between 2 and 4.")
        seat_count = 4

    notes = (request.form.get("notes") or "").strip()
    played_at_raw = request.form.get("played_at")
    played_at = session_shared._parse_played_at(played_at_raw, errors)
    win_via_combo = request.form.get("win_via_combo") in {"1", "true", "on", "yes"}
    try:
        winner_seat = legacy.parse_optional_positive_int(
            request.form.get("winner_seat"),
            field="winner seat",
            min_value=1,
        )
    except legacy.ValidationError as exc:
        legacy.log_validation_error(exc, context="game_create")
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
            pod_id = legacy.parse_positive_int(pod_id_raw, field="pod")
        except legacy.ValidationError as exc:
            legacy.log_validation_error(exc, context="game_create")
            errors.append("Select a valid pod.")
            pod_id = None
        if pod_id:
            pod = legacy.GamePod.query.filter_by(id=pod_id, owner_user_id=current_user.id).first()
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

    seat_payloads: list[dict[str, object]] = []
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
            deck_kind, deck_lookup_id = session_shared._parse_deck_ref(
                deck_ref_raw,
                seat_number=seat_number,
                errors=errors,
            )

        manual_link_id = None
        if manual_link_ref:
            raw_link = manual_link_ref.strip()
            if raw_link.startswith("folder:") or raw_link.isdigit():
                try:
                    manual_link_id = legacy.parse_positive_int(
                        raw_link.split(":", 1)[-1],
                        field="commander deck",
                        min_value=1,
                    )
                except legacy.ValidationError as exc:
                    legacy.log_validation_error(exc, context="game_commander_link")
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
                roster_id = legacy.parse_positive_int(roster_id_raw, field="player", min_value=1)
            except legacy.ValidationError as exc:
                legacy.log_validation_error(exc, context="game_create")
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
                    legacy.User.query.filter(legacy.User.id == roster_player.get("user_id")).first()
                    if roster_player.get("user_id")
                    else None
                )
                if deck_kind in {"manual", "folder"} and deck_ref_raw not in roster_deck_refs.get(
                    roster_player["id"],
                    set(),
                ):
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


def load_selected_decks(deck_ids_to_load: set[int]) -> dict[int, legacy.Folder]:
    if not deck_ids_to_load:
        return {}
    return {folder.id: folder for folder in legacy.Folder.query.filter(legacy.Folder.id.in_(deck_ids_to_load)).all()}


def load_manual_decks(manual_deck_ids_to_load: set[int]) -> dict[int, legacy.GameRosterDeck]:
    if not manual_deck_ids_to_load:
        return {}
    return {
        deck.id: deck
        for deck in legacy.GameRosterDeck.query.filter(
            legacy.GameRosterDeck.id.in_(manual_deck_ids_to_load),
            legacy.GameRosterDeck.owner_user_id == current_user.id,
        ).all()
    }


def validate_loaded_decks(
    seat_payloads: list[dict[str, object]],
    folders_by_id: dict[int, legacy.Folder],
    manual_decks_by_id: dict[int, legacy.GameRosterDeck],
    errors: list[str],
) -> None:
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
