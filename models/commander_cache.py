from __future__ import annotations

from datetime import datetime

from extensions import db


class CommanderBracketCache(db.Model):
    __tablename__ = "commander_bracket_cache"

    folder_id = db.Column(db.Integer, db.ForeignKey("folder.id", ondelete="CASCADE"), primary_key=True)
    cache_epoch = db.Column(db.Integer, nullable=False)
    card_signature = db.Column(db.String(64), nullable=False, index=True)
    payload = db.Column(db.JSON, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    folder = db.relationship("Folder", back_populates="bracket_cache")

    def __repr__(self) -> str:
        return f"<CommanderBracketCache folder={self.folder_id} epoch={self.cache_epoch}>"
