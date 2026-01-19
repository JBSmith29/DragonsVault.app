"""Add user follow table."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0020_add_user_follow"
down_revision = "0019_add_wishlist_order_ref"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_follow",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "follower_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "followed_user_id",
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
        sa.UniqueConstraint("follower_user_id", "followed_user_id", name="uq_user_follow_pair"),
    )
    op.create_index("ix_user_follow_follower", "user_follow", ["follower_user_id"], unique=False)
    op.create_index("ix_user_follow_followed", "user_follow", ["followed_user_id"], unique=False)
    op.create_index("ix_user_follow_created_at", "user_follow", ["created_at"], unique=False)


def downgrade():
    op.drop_index("ix_user_follow_created_at", table_name="user_follow")
    op.drop_index("ix_user_follow_followed", table_name="user_follow")
    op.drop_index("ix_user_follow_follower", table_name="user_follow")
    op.drop_table("user_follow")
