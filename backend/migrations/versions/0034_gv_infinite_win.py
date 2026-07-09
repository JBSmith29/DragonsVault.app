"""Game Vault — add gv_games.infinite_win flag.

Revision ID: 0034_gv_infinite_win
Revises: 0033_game_vault
"""

from alembic import op
import sqlalchemy as sa


revision = "0034_gv_infinite_win"
down_revision = "0033_game_vault"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gv_games",
        sa.Column("infinite_win", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("gv_games", "infinite_win")
