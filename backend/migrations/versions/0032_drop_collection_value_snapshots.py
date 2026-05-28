"""Drop collection_value_snapshots — feature removed.

Revision ID: 0032_drop_collection_value_snapshots
Revises: 0031_collection_value
Create Date: 2026-05-28

The collection-value dashboard was deleted at the user's request because it
encouraged tracking market trends, which is not a goal of this app. This
migration drops the persisted snapshot table that backed the dashboard.

The forward direction is destructive (any historical snapshots are lost).
The downgrade re-creates the schema in the same shape as 0031 but does not
restore data; that's intentional — Alembic downgrades always lose the data
written after the upgrade.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

_LOG = logging.getLogger(__name__)

revision = "0032_drop_collection_value"
down_revision = "0031_collection_value"
branch_labels = None
depends_on = None


_TABLE = "collection_value_snapshots"

_INDEX_NAMES = (
    "ix_collection_value_user_folder_captured",
    "ix_collection_value_user_captured",
    "ix_collection_value_snapshots_captured_at",
    "ix_collection_value_snapshots_folder_id",
    "ix_collection_value_snapshots_user_id",
)


def _table_exists(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not _table_exists(inspector, _TABLE):
        _LOG.info("%s already absent, skipping", _TABLE)
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes(_TABLE)}
    for name in _INDEX_NAMES:
        if name in existing_indexes:
            op.drop_index(name, table_name=_TABLE)

    op.drop_table(_TABLE)


def downgrade() -> None:
    """Recreate the empty table for parity with 0031's upgrade.

    Data written before this migration was applied cannot be recovered.
    """
    bind = op.get_bind()
    inspector = inspect(bind)
    if _table_exists(inspector, _TABLE):
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "folder_id",
            sa.Integer(),
            sa.ForeignKey("folder.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("currency", sa.String(length=4), nullable=False, server_default="usd"),
        sa.Column("total_value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("unique_cards", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cards", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priced_cards", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_prices", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_cards", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "currency IN ('usd','eur','tix')",
            name="ck_collection_value_currency",
        ),
    )
    for name, cols in (
        ("ix_collection_value_snapshots_user_id", ["user_id"]),
        ("ix_collection_value_snapshots_folder_id", ["folder_id"]),
        ("ix_collection_value_snapshots_captured_at", ["captured_at"]),
        ("ix_collection_value_user_captured", ["user_id", "captured_at"]),
        (
            "ix_collection_value_user_folder_captured",
            ["user_id", "folder_id", "captured_at"],
        ),
    ):
        op.create_index(name, _TABLE, cols, unique=False)
