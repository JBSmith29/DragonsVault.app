"""Add deck stats table."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009_add_deck_stats"
down_revision = "0008_add_folder_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deck_stats",
        sa.Column("folder_id", sa.Integer(), nullable=False),
        sa.Column("avg_mana", sa.Float(), nullable=True),
        sa.Column("curve_json", sa.Text(), nullable=True),
        sa.Column("color_pips_json", sa.Text(), nullable=True),
        sa.Column("last_updated", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["folder_id"], ["folder.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("folder_id"),
    )


def downgrade() -> None:
    op.drop_table("deck_stats")
