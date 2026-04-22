"""Self-service password reset: token generation, email dispatch, and redemption."""
from __future__ import annotations

from flask import current_app, render_template, url_for

from extensions import db
from models import User
from shared.email import send_email
from core.domains.users.services.audit import record_audit_event

__all__ = ["request_password_reset", "redeem_password_reset"]

# Always respond with the same message regardless of whether the email exists,
# to prevent user enumeration.
_ALWAYS_OK = "If that email is registered you'll receive a reset link shortly."


def request_password_reset(email: str) -> str:
    """Issue a reset token and send the email. Returns a safe user-facing message."""
    email = (email or "").strip().lower()
    if not email:
        return _ALWAYS_OK

    user = User.query.filter_by(email=email).first()
    if not user or user.archived_at is not None:
        # Don't reveal whether the address exists.
        return _ALWAYS_OK

    token = user.issue_pw_reset_token()
    db.session.commit()

    reset_url = url_for("views.reset_password", token=token, _external=True)
    app_name = current_app.config.get("APP_NAME", "DragonsVault")

    text_body = render_template(
        "emails/password_reset.txt",
        reset_url=reset_url,
        app_name=app_name,
        ttl_minutes=User.PW_RESET_TTL_SECONDS // 60,
    )
    html_body = render_template(
        "emails/password_reset.html",
        reset_url=reset_url,
        app_name=app_name,
        ttl_minutes=User.PW_RESET_TTL_SECONDS // 60,
    )

    sent = send_email(
        to=user.email,
        subject=f"Reset your {app_name} password",
        text_body=text_body,
        html_body=html_body,
    )

    record_audit_event(
        "password_reset_requested",
        {"email": user.email, "email_sent": sent},
    )

    if not sent:
        current_app.logger.warning("Password reset email failed to send for user %s", user.id)

    return _ALWAYS_OK


def redeem_password_reset(token: str, new_password: str, confirm_password: str, min_length: int = 8) -> tuple[bool, str]:
    """
    Validate the token and set the new password.
    Returns (success: bool, message: str).
    """
    if not token:
        return False, "Invalid or expired reset link."

    if len(new_password) < min_length:
        return False, f"Password must be at least {min_length} characters."

    if new_password != confirm_password:
        return False, "Passwords do not match."

    user = User.verify_pw_reset_token(token)
    if not user:
        return False, "This reset link is invalid or has expired. Please request a new one."

    user.set_password(new_password)
    user.clear_pw_reset_token()
    db.session.commit()

    record_audit_event(
        "password_reset_completed",
        {"user_id": user.id, "email": user.email},
    )

    return True, "Password updated. You can now sign in."
