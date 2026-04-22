"""Admin game-to-deck mapping helpers."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from extensions import db
from models import CommanderBracketCache, Folder, FolderRole, GameDeck, GameSeat, GameSeatAssignment, GameSession, User
from shared.validation import ValidationError, log_validation_error, parse_optional_positive_int, parse_positive_int

__all__ = ["render_admin_game_deck_mapping"]


def _admin_deck_options() -> list[dict[str, str]]:
    rows = (
        db.session.query(
            Folder.id,
            Folder.name,
            Folder.commander_name,
            Folder.owner,
            User.display_name,
            User.username,
            User.email,
        )
        .join(FolderRole, FolderRole.folder_id == Folder.id)
        .outerjoin(User, User.id == Folder.owner_user_id)
        .filter(FolderRole.role.in_(FolderRole.DECK_ROLES))
        .group_by(
            Folder.id,
            Folder.name,
            Folder.commander_name,
            Folder.owner,
            User.display_name,
            User.username,
            User.email,
        )
        .order_by(func.lower(Folder.name), Folder.id.asc())
        .all()
    )
    options: list[dict[str, str]] = []
    for row in rows:
        label = row.name or f"Deck {row.id}"
        if row.commander_name:
            label = f"{label} · {row.commander_name}"
        owner_label = row.owner or row.display_name or row.username or row.email
        if owner_label:
            label = f"{label} · {owner_label}"
        options.append({"id": str(row.id), "label": label})
    return options


def _admin_manual_game_ids() -> list[int]:
    rows = (
        db.session.query(GameSession.id)
        .join(GameDeck, GameDeck.session_id == GameSession.id)
        .filter(GameDeck.folder_id.is_(None))
        .distinct()
        .order_by(GameSession.played_at.desc().nullslast(), GameSession.created_at.desc())
        .all()
    )
    return [row[0] for row in rows if row and row[0]]


def _admin_snapshot_deck(folder: Folder) -> dict[str, object]:
    bracket_level = None
    bracket_label = None
    bracket_score = None
    power_score = None
    cache = CommanderBracketCache.query.filter_by(folder_id=folder.id).first()
    if cache and cache.payload:
        bracket_level = cache.payload.get("level")
        bracket_label = cache.payload.get("label")
        bracket_score = cache.payload.get("score")
        power_score = cache.payload.get("score")
    return {
        "folder_id": folder.id,
        "deck_name": folder.name or f"Deck {folder.id}",
        "commander_name": folder.commander_name,
        "commander_oracle_id": folder.commander_oracle_id,
        "bracket_level": bracket_level,
        "bracket_label": bracket_label,
        "bracket_score": bracket_score,
        "power_score": power_score,
    }


def render_admin_game_deck_mapping():
    manual_game_ids = _admin_manual_game_ids()
    if request.method == "POST":
        raw_game_id = request.form.get("game_id") or ""
        try:
            game_id = parse_positive_int(raw_game_id, field="game")
        except ValidationError as exc:
            log_validation_error(exc, context="admin_game_deck_mapping")
            flash("Select a valid game to update.", "warning")
            return redirect(url_for("views.admin_game_deck_mapping"))

        updated = 0
        for key, value in request.form.items():
            if not key.startswith("deck_map_"):
                continue
            try:
                deck_id = parse_positive_int(key.replace("deck_map_", ""), field="deck")
            except ValidationError:
                continue
            folder_id = parse_optional_positive_int(value, field="registered deck")
            if not folder_id:
                continue
            deck = GameDeck.query.filter_by(id=deck_id, session_id=game_id).first()
            if not deck:
                continue
            folder = (
                Folder.query.join(FolderRole, FolderRole.folder_id == Folder.id)
                .filter(Folder.id == folder_id, FolderRole.role.in_(FolderRole.DECK_ROLES))
                .first()
            )
            if not folder:
                continue
            snapshot = _admin_snapshot_deck(folder)
            deck.folder_id = snapshot["folder_id"]
            deck.deck_name = snapshot["deck_name"]
            deck.commander_name = snapshot["commander_name"]
            deck.commander_oracle_id = snapshot["commander_oracle_id"]
            deck.bracket_level = snapshot["bracket_level"]
            deck.bracket_label = snapshot["bracket_label"]
            deck.bracket_score = snapshot["bracket_score"]
            deck.power_score = snapshot["power_score"]
            updated += 1

        if updated:
            try:
                db.session.commit()
                flash(f"Updated {updated} deck mapping{'s' if updated != 1 else ''}.", "success")
            except Exception:
                db.session.rollback()
                flash("Unable to update deck mappings right now.", "danger")
        else:
            flash("No deck mappings were selected.", "info")

        manual_game_ids = _admin_manual_game_ids()
        action = request.form.get("action") or "save"
        if manual_game_ids:
            if game_id in manual_game_ids:
                index = manual_game_ids.index(game_id)
                next_id = manual_game_ids[index + 1] if index + 1 < len(manual_game_ids) else manual_game_ids[0]
            else:
                next_id = manual_game_ids[0]
            if action == "save_next":
                return redirect(url_for("views.admin_game_deck_mapping", game_id=next_id))
            return redirect(url_for("views.admin_game_deck_mapping", game_id=game_id))
        return redirect(url_for("views.admin_console"))

    if not manual_game_ids:
        return render_template("admin/game_deck_mapping.html", game=None, deck_options=[], total_games=0)

    raw_game_id = request.args.get("game_id")
    try:
        selected_id = parse_positive_int(raw_game_id, field="game") if raw_game_id else manual_game_ids[0]
    except ValidationError:
        selected_id = manual_game_ids[0]
    if selected_id not in manual_game_ids:
        selected_id = manual_game_ids[0]

    session = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .filter(GameSession.id == selected_id)
        .first()
    )

    if not session:
        flash("Game session not found.", "warning")
        return redirect(url_for("views.admin_game_deck_mapping"))

    seats_payload = []
    winner_label = None
    seats_sorted = sorted(session.seats or [], key=lambda s: s.seat_number or 0)
    for seat in seats_sorted:
        assignment = seat.assignment
        player = assignment.player if assignment else None
        deck = assignment.deck if assignment else None
        if session.winner_seat_id and seat.id == session.winner_seat_id:
            winner_label = (player.display_name if player else None) or winner_label
        seats_payload.append(
            {
                "seat_number": seat.seat_number,
                "turn_order": seat.turn_order,
                "player_label": (player.display_name if player else None) or "Unknown",
                "deck_name": (deck.deck_name if deck else None) or "Unknown deck",
                "commander_name": deck.commander_name if deck else None,
                "deck_id": deck.id if deck else None,
                "folder_id": deck.folder_id if deck else None,
                "is_manual": bool(deck and not deck.folder_id),
            }
        )

    played_at = session.played_at or session.created_at
    played_label = played_at.strftime("%Y-%m-%d") if played_at else "Unknown"
    total_games = len(manual_game_ids)
    current_index = manual_game_ids.index(selected_id) if selected_id in manual_game_ids else 0
    prev_id = manual_game_ids[current_index - 1] if current_index > 0 else None
    next_id = manual_game_ids[current_index + 1] if current_index + 1 < len(manual_game_ids) else None

    return render_template(
        "admin/game_deck_mapping.html",
        game={
            "id": session.id,
            "played_at": played_label,
            "notes": session.notes or "",
            "winner_label": winner_label,
            "seats": seats_payload,
        },
        deck_options=_admin_deck_options(),
        total_games=total_games,
        current_index=current_index + 1,
        prev_id=prev_id,
        next_id=next_id,
    )
