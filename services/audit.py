"""Audit logging helpers for privileged actions."""
from __future__ import annotations

from typing import Any, Dict, Optional

from flask import current_app, request
from flask_login import current_user

from extensions import db
from models import AuditLog


def record_audit_event(action: str, details: Optional[Dict[str, Any]] = None) -> None:
    """Persist an audit log entry for the current request/user."""
    try:
        user_id: Optional[int] = None
        if current_user and getattr(current_user, "is_authenticated", False):
            try:
                user_id = int(current_user.get_id())
            except (TypeError, ValueError):
                user_id = None

        entry = AuditLog(
            user_id=user_id,
            action=action,
            details=details or {},
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
            user_agent=(request.headers.get("User-Agent") or "")[:255],
        )
        db.session.add(entry)
        # Flushing keeps the entry tied to the surrounding transaction without forcing a commit.
        db.session.flush()
    except Exception:
        current_app.logger.exception("Failed to record audit event: action=%s", action)
