"""Persisted collection-value snapshots for historical tracking.

A snapshot captures the sum of per-card market prices for a user (or a single
folder) at a point in time, plus the underlying denominators that produced the
number. Storing the totals rather than every card keeps the table small while
still supporting charts and trend analysis.
"""

from __future__ import annotations

from extensions import db
from core.shared.utils.time import utcnow


class CollectionValueSnapshot(db.Model):
    __tablename__ = "collection_value_snapshots"
    __table_args__ = (
        db.Index(
            "ix_collection_value_user_captured",
            "user_id",
            "captured_at",
        ),
        db.Index(
            "ix_collection_value_user_folder_captured",
            "user_id",
            "folder_id",
            "captured_at",
        ),
        db.CheckConstraint(
            "currency IN ('usd','eur','tix')",
            name="ck_collection_value_currency",
        ),
    )

    #: Supported currencies mirror Scryfall's price keys.
    CURRENCIES: tuple[str, ...] = ("usd", "eur", "tix")

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: ``None`` means the snapshot covers every accessible folder for the user.
    folder_id = db.Column(
        db.Integer,
        db.ForeignKey("folder.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    captured_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    currency = db.Column(db.String(4), nullable=False, default="usd")

    total_value = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    unique_cards = db.Column(db.Integer, nullable=False, default=0)
    total_cards = db.Column(db.Integer, nullable=False, default=0)
    priced_cards = db.Column(db.Integer, nullable=False, default=0)
    missing_prices = db.Column(db.Integer, nullable=False, default=0)
    #: JSON list of the top priced cards at snapshot time (name/qty/value/foil).
    top_cards = db.Column(db.JSON, nullable=True)
    #: Optional label (``daily``, ``manual``, ``pre-import``…) for audit.
    source = db.Column(db.String(32), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        scope = f"folder={self.folder_id}" if self.folder_id else "all"
        return (
            f"<CollectionValueSnapshot user={self.user_id} {scope} "
            f"{self.captured_at.isoformat() if self.captured_at else '?'} "
            f"{self.total_value} {self.currency}>"
        )
