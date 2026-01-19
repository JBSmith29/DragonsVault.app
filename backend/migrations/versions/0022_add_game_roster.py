"""Add game roster player/deck tables."""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0022_add_game_roster"
down_revision = "0021_add_game_tracking"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)


def upgrade() -> None:
    try:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        tables = set(inspector.get_table_names())

        if "game_roster_players" not in tables:
            op.create_table(
                "game_roster_players",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "owner_user_id",
                    sa.Integer(),
                    sa.ForeignKey("users.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column(
                    "user_id",
                    sa.Integer(),
                    sa.ForeignKey("users.id", ondelete="SET NULL"),
                    nullable=True,
                ),
                sa.Column("display_name", sa.String(length=120), nullable=True),
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.UniqueConstraint("owner_user_id", "user_id", name="uq_game_roster_user"),
                sa.UniqueConstraint("owner_user_id", "display_name", name="uq_game_roster_display_name"),
                sa.CheckConstraint(
                    "user_id IS NOT NULL OR display_name IS NOT NULL",
                    name="ck_game_roster_identity",
                ),
            )
            op.create_index("ix_game_roster_players_owner_user_id", "game_roster_players", ["owner_user_id"])
            op.create_index("ix_game_roster_players_user_id", "game_roster_players", ["user_id"])
            op.create_index("ix_game_roster_players_display_name", "game_roster_players", ["display_name"])
            op.create_index("ix_game_roster_players_created_at", "game_roster_players", ["created_at"])
            op.create_index("ix_game_roster_players_updated_at", "game_roster_players", ["updated_at"])

        if "game_roster_decks" not in tables:
            op.create_table(
                "game_roster_decks",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "roster_player_id",
                    sa.Integer(),
                    sa.ForeignKey("game_roster_players.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column(
                    "owner_user_id",
                    sa.Integer(),
                    sa.ForeignKey("users.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column(
                    "folder_id",
                    sa.Integer(),
                    sa.ForeignKey("folder.id", ondelete="SET NULL"),
                    nullable=True,
                ),
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.UniqueConstraint("roster_player_id", "folder_id", name="uq_game_roster_deck"),
            )
            op.create_index("ix_game_roster_decks_roster_player_id", "game_roster_decks", ["roster_player_id"])
            op.create_index("ix_game_roster_decks_owner_user_id", "game_roster_decks", ["owner_user_id"])
            op.create_index("ix_game_roster_decks_folder_id", "game_roster_decks", ["folder_id"])
            op.create_index("ix_game_roster_decks_created_at", "game_roster_decks", ["created_at"])

        _LOG.info("Game roster tables created.")
    except Exception:
        _LOG.error("Failed to create game roster tables.", exc_info=True)
        raise


def downgrade() -> None:
    _LOG.info("Downgrade skipped for game roster tables.")
