from extensions import db
from utils.time import utcnow


class DeckStats(db.Model):
    __tablename__ = "deck_stats"

    folder_id = db.Column(
        db.Integer,
        db.ForeignKey("folder.id", ondelete="CASCADE"),
        primary_key=True,
    )
    avg_mana = db.Column(db.Float, nullable=True)
    curve_json = db.Column(db.Text, nullable=True)
    color_pips_json = db.Column(db.Text, nullable=True)
    last_updated = db.Column(db.DateTime, nullable=True, default=utcnow, onupdate=utcnow)

    folder = db.relationship("Folder", back_populates="deck_stats")
