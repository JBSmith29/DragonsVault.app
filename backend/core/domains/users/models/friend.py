"""Friend request and friendship models."""

from __future__ import annotations

from extensions import db
from core.shared.utils.time import utcnow


class UserFriendRequest(db.Model):
    __tablename__ = "user_friend_requests"
    __table_args__ = (
        db.UniqueConstraint("requester_user_id", "recipient_user_id", name="uq_user_friend_request"),
        db.CheckConstraint("requester_user_id <> recipient_user_id", name="ck_user_friend_request_distinct"),
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
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    requester = db.relationship("User", foreign_keys=[requester_user_id])
    recipient = db.relationship("User", foreign_keys=[recipient_user_id])


class UserFriend(db.Model):
    __tablename__ = "user_friends"
    __table_args__ = (
        db.UniqueConstraint("user_id", "friend_user_id", name="uq_user_friend_pair"),
        db.CheckConstraint("user_id <> friend_user_id", name="ck_user_friend_distinct"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    friend_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    user = db.relationship("User", foreign_keys=[user_id])
    friend = db.relationship("User", foreign_keys=[friend_user_id])
