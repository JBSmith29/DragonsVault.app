"""
Canonical metadata for Build-A-Deck sessions.
Stores selected tags and timestamps for build decks.
"""

from extensions import db
from utils.time import utcnow


class DeckBuildSession(db.Model):
    __tablename__ = "deck_build_sessions"

    folder_id = db.Column(
        db.Integer,
        db.ForeignKey("folder.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tags_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

    folder = db.relationship("Folder", backref=db.backref("build_session", uselist=False))
