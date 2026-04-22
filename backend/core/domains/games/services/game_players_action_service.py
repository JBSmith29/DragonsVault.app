"""POST action handlers for roster and pod management."""

from __future__ import annotations

from flask import flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from extensions import db

from . import game_compat_service as legacy
from . import game_players_payload_service as payload_service


def _players_redirect():
    return redirect(url_for("views.games_players"))


def _commit_with_flash(*, success_message: str, failure_message: str, success_level: str = "success"):
    try:
        db.session.commit()
        flash(success_message, success_level)
    except Exception:
        db.session.rollback()
        flash(failure_message, "danger")
    return _players_redirect()


def _handle_create_pod():
    pod_name = (request.form.get("pod_name") or "").strip()
    if not pod_name:
        flash("Enter a pod name.", "warning")
        return _players_redirect()
    existing = legacy.GamePod.query.filter_by(owner_user_id=current_user.id, name=pod_name).first()
    if existing:
        flash("Pod name already exists.", "warning")
        return _players_redirect()
    db.session.add(legacy.GamePod(owner_user_id=current_user.id, name=pod_name))
    return _commit_with_flash(success_message="Pod created.", failure_message="Unable to create pod.")


def _handle_remove_pod():
    pod_id_raw = request.form.get("pod_id")
    try:
        pod_id = legacy.parse_positive_int(pod_id_raw, field="pod")
    except legacy.ValidationError as exc:
        legacy.log_validation_error(exc, context="game_pod_remove")
        flash("Invalid pod selection.", "warning")
        return _players_redirect()
    pod = legacy.GamePod.query.filter_by(id=pod_id, owner_user_id=current_user.id).first()
    if pod:
        db.session.delete(pod)
        db.session.commit()
        flash("Pod removed.", "info")
    return _players_redirect()


def _load_pod_for_management(pod_id: int):
    return (
        legacy.GamePod.query.options(
            selectinload(legacy.GamePod.members).selectinload(legacy.GamePodMember.roster_player)
        )
        .filter(legacy.GamePod.id == pod_id)
        .first()
    )


def _handle_add_pod_member():
    pod_id_raw = request.form.get("pod_id")
    roster_id_raw = request.form.get("roster_player_id")
    try:
        pod_id = legacy.parse_positive_int(pod_id_raw, field="pod")
        roster_id = legacy.parse_positive_int(roster_id_raw, field="player")
    except legacy.ValidationError as exc:
        legacy.log_validation_error(exc, context="game_pod_member")
        flash("Select a pod and player.", "warning")
        return _players_redirect()

    pod = _load_pod_for_management(pod_id)
    if not pod:
        flash("Pod not found.", "warning")
        return _players_redirect()
    is_owner, _ = payload_service._pod_access_flags(pod, current_user.id)
    if not is_owner:
        flash("Pod not found.", "warning")
        return _players_redirect()
    roster_player = legacy.GameRosterPlayer.query.filter_by(
        id=roster_id,
        owner_user_id=pod.owner_user_id,
    ).first()
    if not roster_player:
        flash("Player not found.", "warning")
        return _players_redirect()
    existing = legacy.GamePodMember.query.filter_by(pod_id=pod_id, roster_player_id=roster_id).first()
    if existing:
        flash("Player already in this pod.", "info")
        return _players_redirect()
    db.session.add(legacy.GamePodMember(pod_id=pod_id, roster_player_id=roster_id))
    return _commit_with_flash(success_message="Player added to pod.", failure_message="Unable to add player to pod.")


def _handle_remove_pod_member():
    member_id_raw = request.form.get("member_id")
    try:
        member_id = legacy.parse_positive_int(member_id_raw, field="pod member")
    except legacy.ValidationError as exc:
        legacy.log_validation_error(exc, context="game_pod_member_remove")
        flash("Invalid pod member.", "warning")
        return _players_redirect()
    member = legacy.GamePodMember.query.filter_by(id=member_id).first()
    if not member:
        flash("Pod member not found.", "warning")
        return _players_redirect()
    pod = _load_pod_for_management(member.pod_id)
    if not pod:
        flash("Pod not found.", "warning")
        return _players_redirect()
    is_owner, _ = payload_service._pod_access_flags(pod, current_user.id)
    is_self_member = bool(member.roster_player and member.roster_player.user_id == current_user.id)
    if not is_owner and not is_self_member:
        flash("Pod member not found.", "warning")
        return _players_redirect()
    db.session.delete(member)
    db.session.commit()
    flash("Pod member removed.", "info")
    return _players_redirect()


def _handle_add_player():
    roster_owner_id = current_user.id
    roster_owner_raw = request.form.get("roster_owner_id")
    if roster_owner_raw:
        try:
            roster_owner_id = legacy.parse_positive_int(
                roster_owner_raw,
                field="roster owner",
                min_value=1,
            )
        except legacy.ValidationError as exc:
            legacy.log_validation_error(exc, context="game_roster_owner")
            roster_owner_id = current_user.id
    if roster_owner_id != current_user.id:
        flash("Select a valid roster owner.", "warning")
        return _players_redirect()

    kind = (request.form.get("player_kind") or "guest").strip().lower()
    identifier = (request.form.get("player_identifier") or "").strip()
    display_name = (request.form.get("display_name") or "").strip()
    if kind == "user":
        if not identifier:
            flash("Enter a username or email.", "warning")
            return _players_redirect()
        user = (
            legacy.User.query.filter(func.lower(legacy.User.username) == identifier.lower()).first()
            or legacy.User.query.filter(func.lower(legacy.User.email) == identifier.lower()).first()
        )
        if not user:
            flash("User not found.", "warning")
            return _players_redirect()

        label = display_name or user.display_name or user.username or user.email
        player = legacy.GameRosterPlayer(
            owner_user_id=roster_owner_id,
            user_id=user.id,
            display_name=label,
        )
        db.session.add(player)
        try:
            db.session.flush()
        except Exception:
            db.session.rollback()
            flash("Unable to add player.", "danger")
            return _players_redirect()

        auto_added = 0
        deck_ids = (
            db.session.query(legacy.Folder.id)
            .join(legacy.FolderRole, legacy.FolderRole.folder_id == legacy.Folder.id)
            .filter(
                legacy.FolderRole.role.in_(legacy.FolderRole.DECK_ROLES),
                legacy.Folder.owner_user_id == user.id,
            )
            .all()
        )
        for (deck_id,) in deck_ids:
            db.session.add(
                legacy.GameRosterDeck(
                    roster_player_id=player.id,
                    owner_user_id=roster_owner_id,
                    folder_id=deck_id,
                )
            )
            auto_added += 1
        try:
            db.session.commit()
            flash(
                f"Player added with {auto_added} deck(s)." if auto_added else "Player added.",
                "success",
            )
        except Exception:
            db.session.rollback()
            flash("Unable to add player.", "danger")
        return _players_redirect()

    if not display_name:
        flash("Enter a display name.", "warning")
        return _players_redirect()

    db.session.add(
        legacy.GameRosterPlayer(
            owner_user_id=roster_owner_id,
            display_name=display_name,
        )
    )
    return _commit_with_flash(success_message="Guest player added.", failure_message="Unable to add player.")


def _handle_assign_deck():
    roster_id_raw = request.form.get("roster_player_id")
    deck_id_raw = request.form.get("deck_id")
    manual_name = (request.form.get("manual_deck_name") or "").strip()
    try:
        roster_id = legacy.parse_positive_int(roster_id_raw, field="player")
    except legacy.ValidationError as exc:
        legacy.log_validation_error(exc, context="game_roster_assign")
        flash("Select a player.", "warning")
        return _players_redirect()

    roster_player = legacy.GameRosterPlayer.query.filter_by(
        id=roster_id,
        owner_user_id=current_user.id,
    ).first()
    if not roster_player:
        flash("Player not found.", "warning")
        return _players_redirect()
    roster_owner_id = roster_player.owner_user_id

    if manual_name:
        db.session.add(
            legacy.GameRosterDeck(
                roster_player_id=roster_id,
                owner_user_id=roster_owner_id,
                deck_name=manual_name,
            )
        )
        return _commit_with_flash(success_message="Manual deck added.", failure_message="Unable to add manual deck.")

    try:
        deck_id = legacy.parse_positive_int(deck_id_raw, field="deck")
    except legacy.ValidationError as exc:
        legacy.log_validation_error(exc, context="game_roster_assign")
        flash("Select a deck.", "warning")
        return _players_redirect()

    folder = (
        legacy.Folder.query.outerjoin(legacy.FolderRole, legacy.FolderRole.folder_id == legacy.Folder.id)
        .filter(
            legacy.Folder.id == deck_id,
            legacy.Folder.owner_user_id == roster_owner_id,
            or_(
                legacy.FolderRole.role.in_(legacy.FolderRole.DECK_ROLES),
                legacy.Folder.category == legacy.Folder.CATEGORY_DECK,
            ),
        )
        .group_by(legacy.Folder.id)
        .first()
    )
    if not folder:
        flash("Deck not found.", "warning")
        return _players_redirect()
    existing = legacy.GameRosterDeck.query.filter_by(roster_player_id=roster_id, folder_id=deck_id).first()
    if existing:
        flash("Deck already assigned.", "info")
        return _players_redirect()

    db.session.add(
        legacy.GameRosterDeck(
            roster_player_id=roster_id,
            owner_user_id=roster_owner_id,
            folder_id=deck_id,
        )
    )
    return _commit_with_flash(success_message="Deck assigned.", failure_message="Unable to assign deck.")


def _handle_remove_deck():
    assignment_id_raw = request.form.get("assignment_id")
    try:
        assignment_id = legacy.parse_positive_int(assignment_id_raw, field="assignment")
    except legacy.ValidationError as exc:
        legacy.log_validation_error(exc, context="game_roster_remove")
        flash("Invalid assignment.", "warning")
        return _players_redirect()
    assignment = legacy.GameRosterDeck.query.filter_by(
        id=assignment_id,
        owner_user_id=current_user.id,
    ).first()
    if not assignment:
        return _players_redirect()
    db.session.delete(assignment)
    db.session.commit()
    flash("Deck removed.", "info")
    return _players_redirect()


def _handle_remove_player():
    roster_id_raw = request.form.get("roster_player_id")
    try:
        roster_id = legacy.parse_positive_int(roster_id_raw, field="player")
    except legacy.ValidationError as exc:
        legacy.log_validation_error(exc, context="game_roster_remove_player")
        flash("Invalid player.", "warning")
        return _players_redirect()
    roster_player = legacy.GameRosterPlayer.query.filter_by(
        id=roster_id,
        owner_user_id=current_user.id,
    ).first()
    if not roster_player:
        flash("Player not found.", "warning")
        return _players_redirect()
    db.session.delete(roster_player)
    db.session.commit()
    flash("Player removed.", "info")
    return _players_redirect()


def handle_games_players_post():
    action = (request.form.get("action") or "").strip().lower()
    if action == "create_pod":
        return _handle_create_pod()
    if action == "remove_pod":
        return _handle_remove_pod()
    if action == "add_pod_member":
        return _handle_add_pod_member()
    if action == "remove_pod_member":
        return _handle_remove_pod_member()
    if action == "add_player":
        return _handle_add_player()
    if action == "assign_deck":
        return _handle_assign_deck()
    if action == "remove_deck":
        return _handle_remove_deck()
    if action == "remove_player":
        return _handle_remove_player()
    flash("Unknown action.", "warning")
    return _players_redirect()


__all__ = ["handle_games_players_post"]
