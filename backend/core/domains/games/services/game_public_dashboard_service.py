"""Shared public dashboard helpers for games."""

from __future__ import annotations

from flask import current_app
from sqlalchemy import func

from extensions import db
from models import GameSession, User
from shared.validation import ValidationError, log_validation_error, parse_positive_int


def resolve_public_dashboard_owner_user_id() -> int | None:
    configured_raw = str(current_app.config.get("PUBLIC_GAME_DASHBOARD_OWNER_ID") or "").strip()
    if configured_raw:
        try:
            configured_id = parse_positive_int(configured_raw, field="public_game_dashboard_owner_id")
        except ValidationError as exc:
            log_validation_error(exc, context="public_game_dashboard_owner_id")
        else:
            exists = db.session.query(User.id).filter(User.id == configured_id).first()
            if exists:
                return int(configured_id)

    row = (
        db.session.query(GameSession.owner_user_id)
        .filter(GameSession.owner_user_id.isnot(None))
        .group_by(GameSession.owner_user_id)
        .order_by(func.count(GameSession.id).desc(), GameSession.owner_user_id.asc())
        .first()
    )
    if not row or row[0] is None:
        return None
    return int(row[0])


__all__ = ["resolve_public_dashboard_owner_user_id"]
