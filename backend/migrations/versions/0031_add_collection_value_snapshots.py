"""Add collection_value_snapshots for historical portfolio tracking.

Revision ID: 0031_add_collection_value_snapshots
Revises: 0030_add_card_condition
Create Date: 2026-05-12
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

_LOG = logging.getLogger(__name__)

revision = "0031_add_collection_value_snapshots"
down_revision = "0030_add_card_condition"
branch_labels = None
depends_on = None


_TABLE = "collection_value_snapshots"


def _table_exists(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _table_exists(inspector, _TABLE):
        _LOG.info("%s already exists, skipping", _TABLE)
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
    op.create_index(
        "ix_collection_value_snapshots_user_id",
        _TABLE,
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_collection_value_snapshots_folder_id",
        _TABLE,
        ["folder_id"],
        unique=False,
    )
    op.create_index(
        "ix_collection_value_snapshots_captured_at",
        _TABLE,
        ["captured_at"],
        unique=False,
    )
    op.create_index(
        "ix_collection_value_user_captured",
        _TABLE,
        ["user_id", "captured_at"],
        unique=False,
    )
    op.create_index(
        "ix_collection_value_user_folder_captured",
        _TABLE,
        ["user_id", "folder_id", "captured_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not _table_exists(inspector, _TABLE):
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes(_TABLE)}
    for name in (
        "ix_collection_value_user_folder_captured",
        "ix_collection_value_user_captured",
        "ix_collection_value_snapshots_captured_at",
        "ix_collection_value_snapshots_folder_id",
        "ix_collection_value_snapshots_user_id",
    ):
        if name in existing_indexes:
            op.drop_index(name, table_name=_TABLE)

    op.drop_table(_TABLE)
