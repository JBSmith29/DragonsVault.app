"""Add user friend requests and friendships."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0023_add_user_friends"
down_revision = "0022_add_game_roster"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_friend_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requester_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("requester_user_id", "recipient_user_id", name="uq_user_friend_request"),
        sa.CheckConstraint("requester_user_id <> recipient_user_id", name="ck_user_friend_request_distinct"),
    )
    op.create_index("ix_user_friend_requests_requester", "user_friend_requests", ["requester_user_id"])
    op.create_index("ix_user_friend_requests_recipient", "user_friend_requests", ["recipient_user_id"])
    op.create_index("ix_user_friend_requests_created_at", "user_friend_requests", ["created_at"])

    op.create_table(
        "user_friends",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "friend_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("user_id", "friend_user_id", name="uq_user_friend_pair"),
        sa.CheckConstraint("user_id <> friend_user_id", name="ck_user_friend_distinct"),
    )
    op.create_index("ix_user_friends_user", "user_friends", ["user_id"])
    op.create_index("ix_user_friends_friend", "user_friends", ["friend_user_id"])
    op.create_index("ix_user_friends_created_at", "user_friends", ["created_at"])


def downgrade():
    op.drop_index("ix_user_friends_created_at", table_name="user_friends")
    op.drop_index("ix_user_friends_friend", table_name="user_friends")
    op.drop_index("ix_user_friends_user", table_name="user_friends")
    op.drop_table("user_friends")

    op.drop_index("ix_user_friend_requests_created_at", table_name="user_friend_requests")
    op.drop_index("ix_user_friend_requests_recipient", table_name="user_friend_requests")
    op.drop_index("ix_user_friend_requests_requester", table_name="user_friend_requests")
    op.drop_table("user_friend_requests")
