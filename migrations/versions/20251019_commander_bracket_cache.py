"""Add commander bracket cache table.

Revision ID: f4a1a2c8c7d3
Revises: b8673e0b8290
Create Date: 2025-10-19 20:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f4a1a2c8c7d3"
down_revision = "b8673e0b8290"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commander_bracket_cache",
        sa.Column("folder_id", sa.Integer(), nullable=False),
        sa.Column("cache_epoch", sa.Integer(), nullable=False),
        sa.Column("card_signature", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["folder_id"], ["folder.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("folder_id"),
    )
    op.create_index(
        "ix_commander_bracket_cache_signature",
        "commander_bracket_cache",
        ["card_signature"],
    )
    op.create_index(
        "ix_commander_bracket_cache_epoch",
        "commander_bracket_cache",
        ["cache_epoch"],
    )


def downgrade() -> None:
    op.drop_index("ix_commander_bracket_cache_epoch", table_name="commander_bracket_cache")
    op.drop_index("ix_commander_bracket_cache_signature", table_name="commander_bracket_cache")
    op.drop_table("commander_bracket_cache")
