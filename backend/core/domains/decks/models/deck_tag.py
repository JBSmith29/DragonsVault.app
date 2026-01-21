from extensions import db
from core.shared.utils.time import utcnow


class DeckTag(db.Model):
    __tablename__ = "deck_tags"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True, index=True)
    slug = db.Column(db.String(160), nullable=False, unique=True, index=True)
    source = db.Column(db.String(32), nullable=False, index=True)
    edhrec_category = db.Column(db.String(120), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

    tag_maps = db.relationship(
        "DeckTagMap",
        back_populates="deck_tag",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DeckTagMap(db.Model):
    __tablename__ = "deck_tag_map"
    __table_args__ = (
        db.UniqueConstraint("folder_id", "deck_tag_id", name="uq_deck_tag_map_folder_tag"),
    )

    id = db.Column(db.Integer, primary_key=True)
    folder_id = db.Column(db.Integer, db.ForeignKey("folder.id", ondelete="CASCADE"), nullable=False, index=True)
    deck_tag_id = db.Column(db.Integer, db.ForeignKey("deck_tags.id", ondelete="CASCADE"), nullable=False, index=True)
    confidence = db.Column(db.Float, nullable=True)
    source = db.Column(db.String(32), nullable=False, index=True)
    locked = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

    deck_tag = db.relationship("DeckTag", back_populates="tag_maps")
    folder = db.relationship("Folder", back_populates="deck_tag_entries")
