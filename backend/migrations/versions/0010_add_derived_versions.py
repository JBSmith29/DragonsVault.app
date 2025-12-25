"""Add version fields to derived tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010_add_derived_versions"
down_revision = "0009_add_deck_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deck_stats",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column(
        "deck_stats",
        sa.Column("source_version", sa.Text(), nullable=True),
    )
    op.add_column(
        "oracle_deck_tags",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column(
        "oracle_deck_tags",
        sa.Column("source_version", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oracle_deck_tags", "source_version")
    op.drop_column("oracle_deck_tags", "version")
    op.drop_column("deck_stats", "source_version")
    op.drop_column("deck_stats", "version")
