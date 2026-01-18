"""Add wishlist order reference field."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0019_add_wishlist_order_ref"
down_revision = "0018_perf_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("wishlist_items", sa.Column("order_ref", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("wishlist_items", "order_ref")
