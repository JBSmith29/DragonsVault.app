"""Game Vault — add gv_decks.bracket_manual flag.

Marks a bracket the user set by hand; such a bracket is authoritative and is
not overwritten when the deck is re-synced from source.

Revision ID: 0036_gv_bracket_manual
Revises: 0035_gv_bracket_estimated
"""

from alembic import op
import sqlalchemy as sa


revision = "0036_gv_bracket_manual"
down_revision = "0035_gv_bracket_estimated"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gv_decks",
        sa.Column("bracket_manual", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("gv_decks", "bracket_manual")
