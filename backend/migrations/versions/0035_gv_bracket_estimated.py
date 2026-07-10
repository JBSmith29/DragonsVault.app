"""Game Vault — add gv_decks.bracket_is_estimated flag.

Marks decks whose bracket came from Archidekt's auto-estimate rather than the
owner-declared value.

Revision ID: 0035_gv_bracket_estimated
Revises: 0034_gv_infinite_win
"""

from alembic import op
import sqlalchemy as sa


revision = "0035_gv_bracket_estimated"
down_revision = "0034_gv_infinite_win"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gv_decks",
        sa.Column("bracket_is_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("gv_decks", "bracket_is_estimated")
