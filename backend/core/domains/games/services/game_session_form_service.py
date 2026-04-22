"""Game session detail and form CRUD renderers."""

from __future__ import annotations

from flask import flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy.orm import selectinload

from extensions import db

from . import game_compat_service as legacy
from . import game_session_form_context_service as form_context
from . import game_session_form_mutation_service as form_mutation
from . import game_session_form_parsing_service as form_parsing

__all__ = [
    "game_detail",
    "games_bulk_delete",
    "games_delete",
    "games_edit",
    "games_new",
]


def game_detail(game_id: int):
    return form_context.game_detail(game_id)


def games_new():
    context = form_context.game_form_context()
    form_data = form_context.default_game_form_data(context)

    if request.method == "GET":
        return form_context.render_game_form(context, form_data, is_edit=False, game_id=None)

    parsed = form_parsing.parse_game_form(context)
    errors = parsed["errors"]
    form_data = parsed["form_data"]
    seat_payloads = parsed["seat_payloads"]
    folders_by_id = form_parsing.load_selected_decks(parsed["deck_ids_to_load"])
    manual_decks_by_id = form_parsing.load_manual_decks(parsed["manual_deck_ids_to_load"])

    form_parsing.validate_loaded_decks(seat_payloads, folders_by_id, manual_decks_by_id, errors)
    if errors:
        for message in errors:
            flash(message, "warning")
        return form_context.render_game_form(context, form_data, is_edit=False, game_id=None)

    session = legacy.GameSession(
        owner_user_id=current_user.id,
        played_at=parsed["played_at"],
        notes=parsed["notes"] or None,
        win_via_combo=bool(parsed["win_via_combo"]),
    )
    db.session.add(session)
    db.session.flush()
    form_mutation.persist_game_session(
        session,
        seat_payloads,
        folders_by_id,
        manual_decks_by_id,
        parsed["winner_seat"],
    )
    form_mutation.apply_auto_assignments(parsed["auto_assignments"])

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to save the game right now.", "danger")
        return form_context.render_game_form(context, form_data, is_edit=False, game_id=None)

    flash("Game logged.", "success")
    return redirect(url_for("views.games_detail", game_id=session.id))


def games_edit(game_id: int):
    session = (
        legacy.GameSession.query.options(
            selectinload(legacy.GameSession.seats)
            .selectinload(legacy.GameSeat.assignment)
            .selectinload(legacy.GameSeatAssignment.player),
            selectinload(legacy.GameSession.seats)
            .selectinload(legacy.GameSeat.assignment)
            .selectinload(legacy.GameSeatAssignment.deck),
        )
        .filter(legacy.GameSession.id == game_id, legacy.GameSession.owner_user_id == current_user.id)
        .first()
    )
    if not session:
        flash("Game session not found.", "warning")
        return redirect(url_for("views.games_overview"))

    context = form_context.game_form_context()

    if request.method == "GET":
        folder_ids = {deck.folder_id for deck in (session.decks or []) if deck.folder_id}
        folder_map = (
            {folder.id: folder for folder in legacy.Folder.query.filter(legacy.Folder.id.in_(folder_ids)).all()}
            if folder_ids
            else {}
        )
        form_data = form_context.game_form_data_from_session(session, context, folder_map)
        return form_context.render_game_form(context, form_data, is_edit=True, game_id=session.id)

    parsed = form_parsing.parse_game_form(context)
    errors = parsed["errors"]
    form_data = parsed["form_data"]
    seat_payloads = parsed["seat_payloads"]
    folders_by_id = form_parsing.load_selected_decks(parsed["deck_ids_to_load"])
    manual_decks_by_id = form_parsing.load_manual_decks(parsed["manual_deck_ids_to_load"])

    form_parsing.validate_loaded_decks(seat_payloads, folders_by_id, manual_decks_by_id, errors)
    if errors:
        for message in errors:
            flash(message, "warning")
        return form_context.render_game_form(context, form_data, is_edit=True, game_id=session.id)

    session.played_at = parsed["played_at"]
    session.notes = parsed["notes"] or None
    session.win_via_combo = bool(parsed["win_via_combo"])
    session.winner_seat = None
    session.winner_seat_id = None

    existing_assignments = legacy.GameSeatAssignment.query.filter_by(session_id=session.id).all()
    player_ids = {assignment.player_id for assignment in existing_assignments if assignment.player_id}
    for assignment in existing_assignments:
        db.session.delete(assignment)
    for seat in session.seats:
        db.session.delete(seat)
    for deck in session.decks:
        db.session.delete(deck)
    if player_ids:
        legacy.GamePlayer.query.filter(legacy.GamePlayer.id.in_(player_ids)).delete(synchronize_session=False)
    db.session.flush()

    form_mutation.persist_game_session(
        session,
        seat_payloads,
        folders_by_id,
        manual_decks_by_id,
        parsed["winner_seat"],
    )
    form_mutation.apply_auto_assignments(parsed["auto_assignments"])

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to update the game right now.", "danger")
        return form_context.render_game_form(context, form_data, is_edit=True, game_id=session.id)

    flash("Game updated.", "success")
    return redirect(url_for("views.games_detail", game_id=session.id))


def games_delete(game_id: int):
    session = legacy.GameSession.query.filter_by(id=game_id, owner_user_id=current_user.id).first()
    if not session:
        flash("Game session not found.", "warning")
        return redirect(url_for("views.games_overview"))
    db.session.delete(session)
    try:
        db.session.commit()
        flash("Game log deleted.", "info")
    except Exception:
        db.session.rollback()
        flash("Unable to delete the game right now.", "danger")
        return redirect(url_for("views.games_detail", game_id=game_id))
    return redirect(url_for("views.games_overview"))


def games_bulk_delete():
    raw_ids = (request.form.get("game_ids") or "").strip()
    if not raw_ids:
        flash("Select at least one game log.", "warning")
        return redirect(url_for("views.games_overview"))

    game_ids: list[int] = []
    for token in raw_ids.replace(" ", "").split(","):
        if not token:
            continue
        try:
            game_ids.append(legacy.parse_positive_int(token, field="game"))
        except legacy.ValidationError as exc:
            legacy.log_validation_error(exc, context="game_bulk_delete")
            continue

    if not game_ids:
        flash("Select at least one game log.", "warning")
        return redirect(url_for("views.games_overview"))

    sessions = (
        legacy.GameSession.query.filter(
            legacy.GameSession.id.in_(game_ids),
            legacy.GameSession.owner_user_id == current_user.id,
        )
        .all()
    )
    if not sessions:
        flash("No owned game logs were selected.", "warning")
        return redirect(url_for("views.games_overview"))

    deleted = 0
    for session in sessions:
        db.session.delete(session)
        deleted += 1
    try:
        db.session.commit()
        flash(f"Deleted {deleted} game log{'s' if deleted != 1 else ''}.", "info")
    except Exception:
        db.session.rollback()
        flash("Unable to delete the selected game logs.", "danger")
    return redirect(url_for("views.games_overview"))
