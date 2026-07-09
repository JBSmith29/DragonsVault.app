"""Game Vault — self-contained game logger tables (gv_*).

Creates gv_players, gv_decks, gv_games, gv_game_participants. These tables are
deliberately isolated: no foreign keys reference any existing application table
(owner_user_id is a plain integer), so the feature can be dropped independently.

Revision ID: 0033_game_vault
Revises: 0032_drop_collection_value
"""

from alembic import op
import sqlalchemy as sa


revision = "0033_game_vault"
down_revision = "0032_drop_collection_value"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gv_players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_gv_players"),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_gv_player_owner_name"),
    )
    op.create_index("ix_gv_players_owner", "gv_players", ["owner_user_id"])

    op.create_table(
        "gv_decks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("commander_name", sa.String(length=200), nullable=True),
        sa.Column("commander_image", sa.String(length=500), nullable=True),
        sa.Column("color_identity", sa.String(length=10), nullable=True),
        sa.Column("format", sa.String(length=32), nullable=True),
        sa.Column("bracket", sa.Integer(), nullable=True),
        sa.Column("card_count", sa.Integer(), nullable=True),
        sa.Column("cards", sa.JSON(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("sync_status", sa.String(length=20), nullable=True),
        sa.Column("sync_error", sa.String(length=255), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_gv_decks"),
        sa.ForeignKeyConstraint(
            ["player_id"], ["gv_players.id"],
            name="fk_gv_decks_player_id_gv_players", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_gv_decks_owner", "gv_decks", ["owner_user_id"])
    op.create_index("ix_gv_decks_player", "gv_decks", ["player_id"])

    op.create_table(
        "gv_games",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("played_at", sa.DateTime(), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("win_condition", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_gv_games"),
    )
    op.create_index("ix_gv_games_owner", "gv_games", ["owner_user_id"])
    op.create_index("ix_gv_games_played_at", "gv_games", ["played_at"])
    op.create_index("ix_gv_games_owner_played", "gv_games", ["owner_user_id", "played_at"])

    op.create_table(
        "gv_game_participants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("deck_id", sa.Integer(), nullable=True),
        sa.Column("player_name", sa.String(length=120), nullable=True),
        sa.Column("deck_name", sa.String(length=200), nullable=True),
        sa.Column("commander_name", sa.String(length=200), nullable=True),
        sa.Column("turn_order", sa.Integer(), nullable=True),
        sa.Column("is_winner", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_gv_game_participants"),
        sa.ForeignKeyConstraint(
            ["game_id"], ["gv_games.id"],
            name="fk_gv_gp_game_id_gv_games", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["gv_players.id"],
            name="fk_gv_gp_player_id_gv_players", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["gv_decks.id"],
            name="fk_gv_gp_deck_id_gv_decks", ondelete="SET NULL",
        ),
    )
    op.create_index("ix_gv_gp_game", "gv_game_participants", ["game_id"])
    op.create_index("ix_gv_gp_player", "gv_game_participants", ["player_id"])
    op.create_index("ix_gv_gp_deck", "gv_game_participants", ["deck_id"])


def downgrade() -> None:
    op.drop_table("gv_game_participants")
    op.drop_index("ix_gv_games_owner_played", table_name="gv_games")
    op.drop_index("ix_gv_games_played_at", table_name="gv_games")
    op.drop_index("ix_gv_games_owner", table_name="gv_games")
    op.drop_table("gv_games")
    op.drop_index("ix_gv_decks_player", table_name="gv_decks")
    op.drop_index("ix_gv_decks_owner", table_name="gv_decks")
    op.drop_table("gv_decks")
    op.drop_index("ix_gv_players_owner", table_name="gv_players")
    op.drop_table("gv_players")
