"""Games landing, overview, and import/export renderers."""

from __future__ import annotations

import csv
import io
from datetime import date

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import case, func
from sqlalchemy.orm import selectinload

from extensions import db
from models import Folder, GameDeck, GamePod, GameSeat, GameSeatAssignment, GameSession, User
from core.domains.cards.services import scryfall_cache as sc
from shared.validation import ValidationError, log_validation_error, parse_positive_int

from . import game_export_support_service as export_support
from . import game_metrics_query_service as metrics_query
from . import game_metrics_support_service as metrics_support
from . import game_players_service
from . import game_public_dashboard_service
from . import game_session_shared_service as session_shared

__all__ = [
    "games_admin",
    "games_dashboard",
    "games_deck_bracket_update",
    "games_export",
    "games_import",
    "games_import_template",
    "games_landing",
    "games_manual_deck_update",
    "games_overview",
    "games_overview_public",
]


def _recent_sessions(limit: int, *, visibility_filter=None) -> list[GameSession]:
    query = GameSession.query.options(
        selectinload(GameSession.seats)
        .selectinload(GameSeat.assignment)
        .selectinload(GameSeatAssignment.player),
        selectinload(GameSession.seats)
        .selectinload(GameSeat.assignment)
        .selectinload(GameSeatAssignment.deck),
    )
    if visibility_filter is not None:
        query = query.filter(visibility_filter)
    return (
        query.order_by(GameSession.played_at.desc().nullslast(), GameSession.created_at.desc())
        .limit(limit)
        .all()
    )


def games_landing():
    recent_games = [
        session_shared._game_session_payload(session, current_user.id)
        for session in _recent_sessions(6, visibility_filter=metrics_support._session_visibility_filter(current_user.id))
    ]
    quick_all = metrics_query._metrics_payload(current_user.id)
    last30_range = metrics_support._resolve_date_range({"range": "last30"})
    quick_30 = metrics_query._metrics_payload(current_user.id, last30_range["start_at"], last30_range["end_at"])
    return render_template(
        "games/index.html",
        recent_games=recent_games,
        quick_all=quick_all,
        quick_30=quick_30,
    )


def games_dashboard():
    recent_games = [
        session_shared._game_session_payload(session, current_user.id)
        for session in _recent_sessions(8, visibility_filter=metrics_support._session_visibility_filter(current_user.id))
    ]

    from extensions import cache

    cache_key = f"user_metrics_{current_user.id}"
    quick_all = cache.get(cache_key)
    if quick_all is None:
        quick_all = metrics_query._metrics_payload(current_user.id)
        cache.set(cache_key, quick_all, timeout=300)

    last30_range = metrics_support._resolve_date_range({"range": "last30"})
    cache_key_30 = f"user_metrics_30_{current_user.id}"
    quick_30 = cache.get(cache_key_30)
    if quick_30 is None:
        quick_30 = metrics_query._metrics_payload(current_user.id, last30_range["start_at"], last30_range["end_at"])
        cache.set(cache_key_30, quick_30, timeout=300)

    return render_template(
        "games/dashboard.html",
        recent_games=recent_games,
        quick_all=quick_all,
        quick_30=quick_30,
    )


def games_admin():
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for("views.games_dashboard"))

    total_games = db.session.query(func.count(GameSession.id)).scalar() or 0
    total_users = db.session.query(func.count(User.id)).scalar() or 0
    today = date.today()
    games_today = (
        db.session.query(func.count(GameSession.id))
        .filter(func.date(GameSession.played_at) == today)
        .scalar()
        or 0
    )
    avg_games_per_user = round(total_games / total_users, 1) if total_users > 0 else 0
    total_pods = db.session.query(func.count(GamePod.id)).scalar() or 0
    combo_wins = (
        db.session.query(func.count(GameSession.id))
        .filter(GameSession.win_via_combo.is_(True))
        .scalar()
        or 0
    )
    combo_rate = round((combo_wins / total_games) * 100, 1) if total_games > 0 else 0
    system_stats = {
        "total_games": total_games,
        "total_users": total_users,
        "games_today": games_today,
        "avg_games_per_user": avg_games_per_user,
        "total_pods": total_pods,
        "combo_rate": combo_rate,
    }
    recent_games = [session_shared._game_session_payload(session, current_user.id) for session in _recent_sessions(20)]
    return render_template(
        "games/admin.html",
        system_stats=system_stats,
        recent_games=recent_games,
    )


def games_overview():
    q = (request.args.get("q") or "").strip()
    query = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .filter(metrics_support._session_visibility_filter(current_user.id))
    )
    query = metrics_support._apply_notes_search(query, q)
    sessions = query.order_by(GameSession.played_at.desc().nullslast(), GameSession.created_at.desc()).all()
    games = [session_shared._game_session_payload(session, current_user.id) for session in sessions]
    has_owned_games = any(session.owner_user_id == current_user.id for session in sessions)
    summary = session_shared._games_summary(current_user.id)
    manual_decks = session_shared._manual_deck_summary(current_user.id)
    registered_deck_options = game_players_service._accessible_deck_options(current_user.id) if manual_decks else []
    return render_template(
        "games/logs.html",
        games=games,
        summary=summary,
        search_query=q,
        has_owned_games=has_owned_games,
        manual_decks=manual_decks,
        registered_deck_options=registered_deck_options,
    )


def games_overview_public():
    owner_user_id = game_public_dashboard_service.resolve_public_dashboard_owner_user_id()
    q = (request.args.get("q") or "").strip()
    sessions: list[GameSession] = []
    summary = {"total_games": 0, "combo_wins": 0}

    if owner_user_id is not None:
        query = (
            GameSession.query.options(
                selectinload(GameSession.seats)
                .selectinload(GameSeat.assignment)
                .selectinload(GameSeatAssignment.player),
                selectinload(GameSession.seats)
                .selectinload(GameSeat.assignment)
                .selectinload(GameSeatAssignment.deck),
            )
            .filter(GameSession.owner_user_id == owner_user_id)
        )
        query = metrics_support._apply_notes_search(query, q)
        sessions = query.order_by(GameSession.played_at.desc().nullslast(), GameSession.created_at.desc()).all()
        total, combo_wins = (
            db.session.query(
                func.count(GameSession.id),
                func.coalesce(func.sum(case((GameSession.win_via_combo.is_(True), 1), else_=0)), 0),
            )
            .filter(GameSession.owner_user_id == owner_user_id)
            .one()
        )
        summary = {
            "total_games": int(total or 0),
            "combo_wins": int(combo_wins or 0),
        }

    games = [session_shared._game_session_payload(session, None) for session in sessions]
    return render_template(
        "games/logs.html",
        games=games,
        summary=summary,
        search_query=q,
        has_owned_games=False,
        manual_decks=[],
        registered_deck_options=[],
        is_public_dashboard=True,
        logs_action_endpoint="views.gamedashboard",
        logs_metric_value="logs",
    )


def games_manual_deck_update():
    source_deck_name = (request.form.get("source_deck_name") or "").strip()
    source_commander_name = (request.form.get("source_commander_name") or "").strip()
    action = (request.form.get("action") or "").strip().lower()
    if not source_deck_name:
        flash("Select a manual deck to update.", "warning")
        return redirect(url_for("views.games_overview"))

    deck_query = (
        GameDeck.query.join(GameSession, GameSession.id == GameDeck.session_id)
        .filter(
            GameSession.owner_user_id == current_user.id,
            GameDeck.folder_id.is_(None),
            func.lower(func.trim(GameDeck.deck_name)) == source_deck_name.lower(),
        )
    )
    if source_commander_name:
        deck_query = deck_query.filter(
            func.lower(func.trim(func.coalesce(GameDeck.commander_name, ""))) == source_commander_name.lower()
        )
    else:
        deck_query = deck_query.filter(
            (GameDeck.commander_name.is_(None)) | (func.trim(GameDeck.commander_name) == "")
        )

    decks = deck_query.all()
    if not decks:
        flash("Manual deck not found or already linked.", "warning")
        return redirect(url_for("views.games_overview"))

    if action == "link":
        deck_id_raw = (request.form.get("link_folder_id") or "").strip()
        if not deck_id_raw:
            flash("Select a registered deck to link.", "warning")
            return redirect(url_for("views.games_overview"))
        try:
            deck_id = parse_positive_int(deck_id_raw, field="deck", min_value=1)
        except ValidationError as exc:
            log_validation_error(exc, context="game_manual_link")
            flash("Select a valid registered deck.", "danger")
            return redirect(url_for("views.games_overview"))

        folder = Folder.query.filter_by(id=deck_id, owner_user_id=current_user.id).first()
        if not folder:
            flash("Registered deck not found.", "warning")
            return redirect(url_for("views.games_overview"))

        snapshot = session_shared._snapshot_deck(folder)
        folder_label = snapshot.get("deck_name") or folder.name or f"Deck {folder.id}"
        commander_name = snapshot.get("commander_name") or session_shared._oracle_name_from_id(
            snapshot.get("commander_oracle_id")
        )
        commander_oracle_id = snapshot.get("commander_oracle_id")

        for deck in decks:
            deck.folder_id = folder.id
            deck.deck_name = folder_label
            if commander_name:
                deck.commander_name = commander_name
            if commander_oracle_id:
                deck.commander_oracle_id = commander_oracle_id
            if snapshot.get("bracket_level") is not None:
                deck.bracket_level = snapshot.get("bracket_level")
            if snapshot.get("bracket_label") is not None:
                deck.bracket_label = snapshot.get("bracket_label")
            if snapshot.get("bracket_score") is not None:
                deck.bracket_score = snapshot.get("bracket_score")
                deck.power_score = snapshot.get("power_score")
            if snapshot.get("power_score") is not None:
                deck.power_score = snapshot.get("power_score")

        db.session.commit()
        flash(f"Linked {len(decks)} log entries to {folder_label}.", "success")
        return redirect(url_for("views.games_overview"))

    commander_name_input = (request.form.get("commander_name") or "").strip()
    bracket_level = (request.form.get("bracket_level") or "").strip()
    bracket_label = (request.form.get("bracket_label") or "").strip()
    bracket_score_raw = (request.form.get("bracket_score") or "").strip()

    has_updates = False
    commander_oracle_id = None
    if commander_name_input:
        try:
            sc.ensure_cache_loaded()
            commander_oracle_id = sc.unique_oracle_by_name(commander_name_input)
        except Exception:
            commander_oracle_id = None
        has_updates = True

    bracket_score = None
    if bracket_score_raw:
        try:
            bracket_score = float(bracket_score_raw)
            has_updates = True
        except ValueError:
            flash("Bracket score must be a number.", "warning")
            return redirect(url_for("views.games_overview"))

    if bracket_level or bracket_label:
        has_updates = True
    if not has_updates:
        flash("Add bracket or commander details before updating.", "warning")
        return redirect(url_for("views.games_overview"))

    for deck in decks:
        if commander_name_input:
            deck.commander_name = commander_name_input
            deck.commander_oracle_id = commander_oracle_id
        if bracket_level:
            deck.bracket_level = bracket_level
        if bracket_label:
            deck.bracket_label = bracket_label
        if bracket_score is not None:
            deck.bracket_score = bracket_score
            deck.power_score = bracket_score

    db.session.commit()
    flash(f"Updated {len(decks)} log entries.", "success")
    return redirect(url_for("views.games_overview"))


def games_deck_bracket_update():
    deck_name = (request.form.get("deck_name") or "").strip()
    match_commander_name = (request.form.get("match_commander_name") or "").strip()
    commander_name_input = (request.form.get("commander_name") or "").strip()
    bracket_level = (request.form.get("bracket_level") or "").strip()
    bracket_label = (request.form.get("bracket_label") or "").strip()
    bracket_score_raw = (request.form.get("bracket_score") or "").strip()

    if not deck_name:
        flash("Enter a deck name to update.", "warning")
        return redirect(url_for("views.games_overview"))

    deck_query = (
        GameDeck.query.join(GameSession, GameSession.id == GameDeck.session_id)
        .filter(
            GameSession.owner_user_id == current_user.id,
            func.lower(func.trim(GameDeck.deck_name)) == deck_name.lower(),
        )
    )
    if match_commander_name:
        deck_query = deck_query.filter(
            func.lower(func.trim(func.coalesce(GameDeck.commander_name, ""))) == match_commander_name.lower()
        )

    decks = deck_query.all()
    if not decks:
        flash("Deck not found in your logs.", "warning")
        return redirect(url_for("views.games_overview"))

    has_updates = False
    commander_oracle_id = None
    if commander_name_input:
        try:
            sc.ensure_cache_loaded()
            commander_oracle_id = sc.unique_oracle_by_name(commander_name_input)
        except Exception:
            commander_oracle_id = None
        has_updates = True

    bracket_score = None
    if bracket_score_raw:
        try:
            bracket_score = float(bracket_score_raw)
            has_updates = True
        except ValueError:
            flash("Bracket score must be a number.", "warning")
            return redirect(url_for("views.games_overview"))

    if bracket_level or bracket_label:
        has_updates = True
    if not has_updates:
        flash("Add bracket or commander details before updating.", "warning")
        return redirect(url_for("views.games_overview"))

    for deck in decks:
        if commander_name_input:
            deck.commander_name = commander_name_input
            deck.commander_oracle_id = commander_oracle_id
        if bracket_level:
            deck.bracket_level = bracket_level
        if bracket_label:
            deck.bracket_label = bracket_label
        if bracket_score is not None:
            deck.bracket_score = bracket_score
            deck.power_score = bracket_score

    db.session.commit()
    flash(f"Updated {len(decks)} log entries.", "success")
    return redirect(url_for("views.games_overview"))


def games_export():
    sessions = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .filter(metrics_support._session_visibility_filter(current_user.id))
        .order_by(GameSession.played_at.desc().nullslast(), GameSession.created_at.desc())
        .all()
    )
    output = io.StringIO()
    headers = export_support.game_csv_headers_wide()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for session in sessions:
        played_at = session.played_at or session.created_at
        played_label = played_at.date().isoformat() if played_at else ""
        winner_seat_number = None
        for seat in session.seats or []:
            if session.winner_seat_id and seat.id == session.winner_seat_id:
                winner_seat_number = seat.seat_number
                break
        row = {header: "" for header in headers}
        row["game_id"] = session.id
        row["played_at"] = played_label
        row["notes"] = session.notes or ""
        row["win_via_combo"] = "1" if session.win_via_combo else "0"
        row["winner_seat"] = winner_seat_number or ""
        row["seat_count"] = len(session.seats or [])
        seats = sorted(session.seats or [], key=lambda s: s.seat_number or 0)
        for seat in seats:
            if not seat.seat_number or seat.seat_number < 1 or seat.seat_number > 4:
                continue
            assignment = seat.assignment
            player = assignment.player if assignment else None
            deck = assignment.deck if assignment else None
            prefix = f"seat_{seat.seat_number}_"
            row[f"{prefix}player_name"] = session_shared._player_label(player)
            row[f"{prefix}player_user_id"] = player.user_id if player and player.user_id else ""
            row[f"{prefix}deck_name"] = deck.deck_name if deck else ""
            row[f"{prefix}deck_folder_id"] = deck.folder_id if deck and deck.folder_id else ""
            row[f"{prefix}commander_name"] = deck.commander_name if deck else ""
            row[f"{prefix}commander_oracle_id"] = deck.commander_oracle_id if deck else ""
            row[f"{prefix}bracket_level"] = deck.bracket_level if deck else ""
            row[f"{prefix}bracket_label"] = deck.bracket_label if deck else ""
            row[f"{prefix}bracket_score"] = deck.bracket_score if deck and deck.bracket_score is not None else ""
            row[f"{prefix}power_score"] = deck.power_score if deck and deck.power_score is not None else ""
            row[f"{prefix}turn_order"] = seat.turn_order or ""
        writer.writerow(row)
    filename = f"dragonsvault-games-{date.today().isoformat()}.csv"
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def games_import():
    from .game_import_service import GameImportError, import_games_csv

    upload = request.files.get("import_file")
    raw_text = (request.form.get("import_csv") or "").strip()
    if upload:
        raw = upload.read()
        try:
            raw_text = raw.decode("utf-8")
        except Exception:
            raw_text = raw.decode("utf-8", errors="ignore")
    if not raw_text:
        flash("Select a game log CSV file to import.", "warning")
        return redirect(url_for("views.games_overview"))

    try:
        imported, skipped, errors = import_games_csv(
            raw_text=raw_text,
            owner_user_id=current_user.id,
            parse_played_at=session_shared._parse_played_at,
            snapshot_deck=session_shared._snapshot_deck,
            find_deck_by_name=session_shared._find_deck_by_name,
        )
    except GameImportError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("views.games_overview"))

    if imported:
        flash(f"Imported {imported} game log{'s' if imported != 1 else ''}.", "success")
    if skipped:
        flash(f"Skipped {skipped} game log{'s' if skipped != 1 else ''}.", "warning")
    if errors:
        for message in errors[:5]:
            flash(message, "warning")
        if len(errors) > 5:
            flash(f"{len(errors) - 5} more import errors were skipped.", "warning")
    return redirect(url_for("views.games_overview"))


def games_import_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(export_support.game_csv_headers_wide(include_game_id=False))
    filename = "dragonsvault-game-import-template.csv"
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
