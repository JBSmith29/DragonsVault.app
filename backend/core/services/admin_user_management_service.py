"""Admin user-management helpers."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user
from sqlalchemy import func

from extensions import db
from models import AuditLog, CommanderBracketCache, DeckStats, Folder, FolderRole, FolderShare, User
from models.card import Card
from core.domains.users.routes.auth import MIN_PASSWORD_LENGTH
from core.domains.users.services.audit import record_audit_event
from shared.validation import ValidationError, log_validation_error, parse_positive_int

__all__ = [
    "handle_delete_user",
    "handle_impersonate_user",
    "handle_reset_user_password",
    "purge_folder",
    "render_admin_manage_users",
    "stop_admin_impersonation",
    "user_management_context",
]


def user_management_context(include_users: bool = False) -> dict:
    user_stats = {"total": 0, "admins": 0}
    users: list[User] = []
    folder_owner_counts: dict[int, int] = {}
    try:
        user_stats["total"] = db.session.query(func.count(User.id)).scalar() or 0
        user_stats["admins"] = (
            db.session.query(func.count(User.id))
            .filter(User.is_admin.is_(True))
            .scalar()
            or 0
        )
    except Exception:
        db.session.rollback()
    if include_users:
        users = User.query.order_by(func.lower(User.email)).all()
        folder_owner_counts = {
            owner_id: count
            for owner_id, count in (
                db.session.query(Folder.owner_user_id, func.count(Folder.id))
                .filter(Folder.owner_user_id.isnot(None))
                .group_by(Folder.owner_user_id)
                .all()
            )
            if owner_id is not None
        }
    return {
        "users": users,
        "folder_owner_counts": folder_owner_counts,
        "user_stats": user_stats,
    }


def purge_folder(folder: Folder) -> dict[str, int]:
    folder_id = folder.id
    deleted_cards = (
        db.session.query(Card)
        .filter(Card.folder_id == folder_id)
        .delete(synchronize_session=False)
    )
    deleted_roles = (
        db.session.query(FolderRole)
        .filter(FolderRole.folder_id == folder_id)
        .delete(synchronize_session=False)
    )
    deleted_shares = (
        db.session.query(FolderShare)
        .filter(FolderShare.folder_id == folder_id)
        .delete(synchronize_session=False)
    )
    deleted_stats = (
        db.session.query(DeckStats)
        .filter(DeckStats.folder_id == folder_id)
        .delete(synchronize_session=False)
    )
    deleted_bracket_cache = (
        db.session.query(CommanderBracketCache)
        .filter(CommanderBracketCache.folder_id == folder_id)
        .delete(synchronize_session=False)
    )
    db.session.delete(folder)
    return {
        "cards": deleted_cards,
        "roles": deleted_roles,
        "shares": deleted_shares,
        "stats": deleted_stats,
        "bracket_cache": deleted_bracket_cache,
    }


def handle_reset_user_password(target_endpoint: str):
    redirect_target = url_for(target_endpoint)
    target_user_raw = request.form.get("target_user_id")
    new_password = (request.form.get("new_password") or "").strip()
    if not target_user_raw:
        flash("Select a user to reset.", "warning")
        return redirect(redirect_target)
    try:
        target_user_id = parse_positive_int(target_user_raw, field="user id")
    except ValidationError as exc:
        log_validation_error(exc, context="admin_reset_password")
        flash("Invalid user id.", "danger")
        return redirect(redirect_target)
    if len(new_password) < MIN_PASSWORD_LENGTH:
        flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.", "warning")
        return redirect(redirect_target)
    target_user = db.session.get(User, target_user_id)
    if not target_user:
        flash("User not found.", "warning")
        return redirect(redirect_target)
    target_user.set_password(new_password)
    db.session.commit()
    record_audit_event(
        "admin_reset_user_password",
        {"target_id": target_user.id, "email": target_user.email},
    )
    flash(f"Password updated for {target_user.email}.", "success")
    return redirect(redirect_target)


def handle_delete_user(target_endpoint: str):
    redirect_target = url_for(target_endpoint)
    target_user_raw = request.form.get("target_user_id")
    if not target_user_raw:
        flash("Select a user to delete.", "warning")
        return redirect(redirect_target)
    try:
        target_user_id = parse_positive_int(target_user_raw, field="user id")
    except ValidationError as exc:
        log_validation_error(exc, context="admin_delete_user")
        flash("Invalid user id.", "danger")
        return redirect(redirect_target)
    if current_user.is_authenticated and target_user_id == current_user.id:
        flash("You cannot delete the account that is currently signed in.", "warning")
        return redirect(redirect_target)
    target_user = db.session.get(User, target_user_id)
    if not target_user:
        flash("User not found.", "warning")
        return redirect(redirect_target)
    if target_user.is_admin:
        remaining_admins = User.query.filter(User.is_admin.is_(True), User.id != target_user.id).count()
        if remaining_admins == 0:
            flash("Cannot delete the last administrator.", "warning")
            return redirect(redirect_target)
    owned_folders = Folder.query.filter(Folder.owner_user_id == target_user.id).all()
    deleted_folders = 0
    deleted_cards = 0
    deleted_folder_names: list[str] = []
    for folder in owned_folders:
        deleted_folders += 1
        deleted_folder_names.append(folder.name or "Folder")
        counts = purge_folder(folder)
        deleted_cards += counts.get("cards", 0)
    removed_shares = (
        db.session.query(FolderShare)
        .filter(FolderShare.shared_user_id == target_user.id)
        .delete(synchronize_session=False)
    )
    audit_entries_detached = (
        AuditLog.query.filter(AuditLog.user_id == target_user.id)
        .update({AuditLog.user_id: None}, synchronize_session=False)
    )
    removed_email = target_user.email
    removed_username = target_user.username
    db.session.delete(target_user)
    db.session.commit()
    record_audit_event(
        "admin_delete_user",
        {
            "target_id": target_user_id,
            "email": removed_email,
            "username": removed_username,
            "folders_deleted": deleted_folders,
            "folder_names": deleted_folder_names,
            "cards_deleted": deleted_cards,
            "shares_removed": removed_shares,
            "audit_entries_detached": audit_entries_detached,
        },
    )
    if deleted_folders:
        flash(
            f"Deleted user {removed_email} along with {deleted_folders} folder(s) and {deleted_cards} card(s).",
            "success",
        )
    else:
        flash(f"Deleted user {removed_email}.", "success")
    return redirect(redirect_target)


def handle_impersonate_user(target_endpoint: str):
    redirect_target = url_for(target_endpoint)
    target_user_raw = request.form.get("target_user_id")
    if not target_user_raw:
        flash("Select a user to impersonate.", "warning")
        return redirect(redirect_target)
    try:
        target_user_id = parse_positive_int(target_user_raw, field="user id")
    except ValidationError as exc:
        log_validation_error(exc, context="admin_impersonate")
        flash("Invalid user id.", "danger")
        return redirect(redirect_target)
    if current_user.is_authenticated and target_user_id == current_user.id:
        flash("You are already signed in as this user.", "info")
        return redirect(redirect_target)
    if session.get("impersonator_id"):
        flash("Stop the current impersonation before starting another.", "warning")
        return redirect(redirect_target)
    target_user = db.session.get(User, target_user_id)
    if not target_user:
        flash("User not found.", "warning")
        return redirect(redirect_target)

    session["impersonator_id"] = current_user.id if current_user.is_authenticated else None
    session["impersonated_user_id"] = target_user.id
    record_audit_event(
        "admin_impersonate_start",
        {
            "admin_id": current_user.id if current_user.is_authenticated else None,
            "target_id": target_user.id,
            "target_email": target_user.email,
            "target_username": target_user.username,
        },
    )
    login_user(target_user, remember=False, fresh=True)
    session.permanent = True
    session["user_is_admin"] = bool(target_user.is_admin)
    flash(f"Now impersonating {target_user.username or target_user.email}.", "info")
    return redirect(url_for("views.dashboard"))


def render_admin_manage_users():
    if request.method == "POST":
        action = (request.form.get("action") or "").lower()
        if action == "reset_user_password":
            return handle_reset_user_password("views.admin_manage_users")
        if action == "delete_user":
            return handle_delete_user("views.admin_manage_users")
        if action == "impersonate_user":
            return handle_impersonate_user("views.admin_manage_users")
    context = user_management_context(include_users=True)
    return render_template(
        "admin/user_management.html",
        users=context["users"],
        folder_owner_counts=context["folder_owner_counts"],
        user_stats=context["user_stats"],
        min_password_length=MIN_PASSWORD_LENGTH,
        current_user_id=current_user.id if current_user.is_authenticated else None,
    )


def stop_admin_impersonation():
    impersonator_id = session.get("impersonator_id")
    impersonated_id = session.get("impersonated_user_id")
    if not impersonator_id:
        flash("No impersonation session is active.", "info")
        return redirect(url_for("views.dashboard"))
    admin_user = db.session.get(User, impersonator_id)
    if not admin_user or not admin_user.is_admin:
        session.pop("impersonator_id", None)
        session.pop("impersonated_user_id", None)
        flash("Impersonation ended. Admin account unavailable.", "warning")
        return redirect(url_for("views.dashboard"))

    login_user(admin_user, remember=False, fresh=True)
    session.permanent = True
    session["user_is_admin"] = bool(admin_user.is_admin)
    session.pop("impersonator_id", None)
    session.pop("impersonated_user_id", None)
    record_audit_event(
        "admin_impersonate_stop",
        {
            "admin_id": admin_user.id,
            "impersonated_id": impersonated_id,
        },
    )
    flash("Returned to your admin session.", "success")
    return redirect(url_for("views.admin_manage_users"))
