"""Friend card request model for wishlist items."""

from __future__ import annotations

from extensions import db
from core.shared.utils.time import utcnow


class FriendCardRequest(db.Model):
    __tablename__ = "friend_card_requests"
    __table_args__ = (
        db.UniqueConstraint(
            "requester_user_id",
            "recipient_user_id",
            "wishlist_item_id",
            name="uq_friend_card_request",
        ),
        db.CheckConstraint(
            "status in ('pending','accepted','rejected')",
            name="ck_friend_card_request_status",
        ),
        db.Index("ix_friend_card_requests_requester", "requester_user_id"),
        db.Index("ix_friend_card_requests_recipient", "recipient_user_id"),
        db.Index("ix_friend_card_requests_status", "status"),
        db.Index("ix_friend_card_requests_created_at", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    requester_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipient_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wishlist_item_id = db.Column(
        db.Integer,
        db.ForeignKey("wishlist_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    requested_qty = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

    requester = db.relationship("User", foreign_keys=[requester_user_id])
    recipient = db.relationship("User", foreign_keys=[recipient_user_id])
    wishlist_item = db.relationship("WishlistItem", foreign_keys=[wishlist_item_id])
