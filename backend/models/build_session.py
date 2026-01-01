"""Build session models for proxy-only deck construction."""

from __future__ import annotations

from extensions import db
from utils.time import utcnow


class BuildSession(db.Model):
    __tablename__ = "build_sessions"

    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    commander_oracle_id = db.Column(db.String(64), nullable=True, index=True)
    commander_name = db.Column(db.String(200), nullable=True)
    build_name = db.Column(db.String(200), nullable=True)
    tags_json = db.Column(db.JSON, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="active", server_default="active")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

    cards = db.relationship(
        "BuildSessionCard",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class BuildSessionCard(db.Model):
    __tablename__ = "build_session_cards"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("build_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    card_oracle_id = db.Column(db.String(64), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    added_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    session = db.relationship("BuildSession", back_populates="cards")

    __table_args__ = (
        db.UniqueConstraint("session_id", "card_oracle_id", name="uq_build_session_card"),
    )
