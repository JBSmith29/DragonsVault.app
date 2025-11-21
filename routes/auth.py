"""Authentication and account management routes."""

from __future__ import annotations

from datetime import datetime

from flask import flash, jsonify, redirect, render_template, request, url_for, session
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from extensions import db
from models import User
from services.audit import record_audit_event

from .base import views

MIN_PASSWORD_LENGTH = 8
MAX_USERNAME_LENGTH = 80


@views.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(request.args.get("next") or url_for("views.dashboard"))

    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""
        user = None
        if identifier:
            lowered = identifier.lower()
            user = User.query.filter(func.lower(User.email) == lowered).first()
            if not user:
                user = User.query.filter(func.lower(User.username) == lowered).first()
        if not user or not user.check_password(password):
            flash("Invalid email/username or password.", "danger")
            return render_template("auth/login.html", identifier=identifier, disable_hx=True)

        login_user(user, remember=False, fresh=True)
        session["user_is_admin"] = bool(user.is_admin)
        user.last_login_at = datetime.utcnow()
        db.session.commit()
        record_audit_event("login", {"email": user.email})
        session["force_full_refresh"] = True
        dest = request.args.get("next") or url_for("views.dashboard")
        resp = redirect(dest)
        if request.headers.get("HX-Request"):
            resp.headers["HX-Refresh"] = "true"
        return resp

    return render_template("auth/login.html", disable_hx=True)


@views.route("/logout")
@login_required
def logout():
    record_audit_event("logout", {"email": current_user.email})
    logout_user()
    session.clear()
    flash("Signed out successfully.", "info")
    return redirect(url_for("views.login"))


@views.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("views.dashboard"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        username = (request.form.get("username") or "").strip().lower()
        password = (request.form.get("password") or "").strip()
        confirm = (request.form.get("confirm_password") or "").strip()
        context = {
            "email": email,
            "username": username,
            "min_password_length": MIN_PASSWORD_LENGTH,
        }

        if not email or not username or not password:
            flash("Email, username, and password are required.", "warning")
            return render_template("auth/register.html", disable_hx=True, **context)
        if len(password) < MIN_PASSWORD_LENGTH:
            flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.", "warning")
            return render_template("auth/register.html", disable_hx=True, **context)
        if password != confirm:
            flash("Passwords do not match.", "warning")
            return render_template("auth/register.html", disable_hx=True, **context)
        existing_email = User.query.filter(func.lower(User.email) == email).first()
        if existing_email:
            flash("That email is already registered.", "warning")
            return render_template("auth/register.html", disable_hx=True, **context)
        existing_username = User.query.filter(func.lower(User.username) == username).first()
        if existing_username:
            flash("That username is already taken.", "warning")
            return render_template("auth/register.html", disable_hx=True, **context)

        new_user = User(
            email=email,
            username=username,
            display_name=None,
            is_admin=False,
        )
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        record_audit_event("user_registered", {"email": email, "username": username})
        flash("Account created. Please sign in.", "success")
        return redirect(url_for("views.login"))

    return render_template("auth/register.html", min_password_length=MIN_PASSWORD_LENGTH, disable_hx=True)


@views.route("/account/api-token", methods=["GET", "POST"])
@login_required
def manage_api_token():
    issued_token = None
    action = (request.form.get("action") or "").lower() if request.method == "POST" else ""

    if action == "create":
        issued_token = current_user.issue_api_token()
        db.session.commit()
        record_audit_event("api_token_issued", {"hint": issued_token[-8:]})
        flash("New API token generated. Copy it now; it is shown only once.", "success")
    elif action == "revoke":
        current_user.clear_api_token()
        db.session.commit()
        record_audit_event("api_token_revoked", {})
        flash("API token revoked.", "info")
    elif action == "update_password":
        current_password = (request.form.get("current_password") or "").strip()
        new_password = (request.form.get("new_password") or "").strip()
        confirm_password = (request.form.get("confirm_password") or "").strip()

        if not current_user.check_password(current_password):
            flash("Current password is incorrect.", "danger")
        elif len(new_password) < MIN_PASSWORD_LENGTH:
            flash(f"New password must be at least {MIN_PASSWORD_LENGTH} characters long.", "warning")
        elif new_password != confirm_password:
            flash("New password confirmation does not match.", "warning")
        elif current_password and current_password == new_password:
            flash("New password must be different from the current password.", "warning")
        else:
            current_user.set_password(new_password)
            db.session.commit()
            record_audit_event("password_updated", {"method": "self_service"})
            flash("Password updated successfully.", "success")

    return render_template(
        "auth/api_token.html",
        issued_token=issued_token,
        token_hint=current_user.api_token_hint,
        token_created_at=current_user.api_token_created_at,
        min_password_length=MIN_PASSWORD_LENGTH,
    )


@views.route("/account/center", methods=["GET", "POST"])
@login_required
def account_center():
    """Surface shortcuts to account and admin tools based on permissions."""
    username_form_value = None
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "update_username":
            desired = (request.form.get("username") or "").strip().lower()
            username_form_value = desired
            if not desired:
                flash("Username is required.", "warning")
            elif len(desired) > MAX_USERNAME_LENGTH:
                flash(f"Username must be {MAX_USERNAME_LENGTH} characters or fewer.", "warning")
            else:
                existing = (
                    User.query.filter(func.lower(User.username) == desired)
                    .filter(User.id != current_user.id)
                    .first()
                )
                if existing:
                    flash("That username is already taken.", "warning")
                elif desired == (current_user.username or "").lower():
                    flash("Username is unchanged.", "info")
                else:
                    current_user.username = desired
                    db.session.commit()
                    record_audit_event(
                        "username_updated",
                        {"username": desired, "user_id": current_user.id},
                    )
                    flash("Username updated.", "success")
                return redirect(url_for("views.account_center"))

    is_admin = bool(getattr(current_user, "is_admin", False))
    general_options = [
        {
            "title": "Account & security",
            "description": "Rotate API tokens and update your password.",
            "icon": "person-gear",
            "url": url_for("views.manage_api_token"),
            "button_label": "Manage account",
        },
        {
            "title": "My folders",
            "description": "Mark decks vs. collection buckets and manage proxy settings.",
            "icon": "folder2-open",
            "url": url_for("views.manage_folder_preferences"),
            "button_label": "Manage folders",
        },
        {
            "title": "Import tools",
            "description": "Upload CSV or deck files to update your collection in bulk.",
            "icon": "cloud-arrow-up",
            "url": url_for("views.import_csv"),
            "button_label": "Open import tools",
        },
        {
            "title": "Sign out",
            "description": "End your session on this device.",
            "icon": "box-arrow-right",
            "url": url_for("views.logout"),
            "button_label": "Sign out",
            "disable_hx": True,
        },
    ]

    admin_options = []
    if is_admin:
        admin_options = [
            {
                "title": "Admin dashboard",
                "description": "Create users, refresh caches, and run maintenance jobs.",
                "icon": "speedometer2",
                "url": url_for("views.admin_console"),
                "button_label": "Open admin dashboard",
            },
        ]

    page_title = "Admin Center" if is_admin else "Settings"
    return render_template(
        "auth/account_center.html",
        page_title=page_title,
        general_options=general_options,
        admin_options=admin_options,
        is_admin=is_admin,
        username_value=(
            username_form_value if username_form_value is not None else (current_user.username or "")
        ),
        username_max=MAX_USERNAME_LENGTH,
    )
