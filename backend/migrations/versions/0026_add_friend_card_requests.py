"""Add friend card requests and wishlist requested statuses."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0026_add_friend_card_requests"
down_revision = "0025_oracle_id_128"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "friend_card_requests",
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
            "wishlist_item_id",
            sa.Integer(),
            sa.ForeignKey("wishlist_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "requester_user_id",
            "recipient_user_id",
            "wishlist_item_id",
            name="uq_friend_card_request",
        ),
        sa.CheckConstraint(
            "status in ('pending','accepted','rejected')",
            name="ck_friend_card_request_status",
        ),
    )
    op.create_index(
        "ix_friend_card_requests_requester",
        "friend_card_requests",
        ["requester_user_id"],
    )
    op.create_index(
        "ix_friend_card_requests_recipient",
        "friend_card_requests",
        ["recipient_user_id"],
    )
    op.create_index(
        "ix_friend_card_requests_status",
        "friend_card_requests",
        ["status"],
    )
    op.create_index(
        "ix_friend_card_requests_created_at",
        "friend_card_requests",
        ["created_at"],
    )

    with op.batch_alter_table("wishlist_items") as batch:
        batch.drop_constraint("ck_wishlist_items_status", type_="check")
        batch.create_check_constraint(
            "ck_wishlist_items_status",
            "status in ('open','to_fetch','ordered','acquired','removed','requested','rejected')",
        )


def downgrade():
    with op.batch_alter_table("wishlist_items") as batch:
        batch.drop_constraint("ck_wishlist_items_status", type_="check")
        batch.create_check_constraint(
            "ck_wishlist_items_status",
            "status in ('open','to_fetch','ordered','acquired','removed')",
        )

    op.drop_index("ix_friend_card_requests_created_at", table_name="friend_card_requests")
    op.drop_index("ix_friend_card_requests_status", table_name="friend_card_requests")
    op.drop_index("ix_friend_card_requests_recipient", table_name="friend_card_requests")
    op.drop_index("ix_friend_card_requests_requester", table_name="friend_card_requests")
    op.drop_table("friend_card_requests")
