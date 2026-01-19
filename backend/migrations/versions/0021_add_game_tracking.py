"""Add commander game tracking tables."""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0021_add_game_tracking"
down_revision = "0020_add_user_follow"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)


def _table_exists(inspector, name: str) -> bool:
    return name in set(inspector.get_table_names())


def _column_names(inspector, name: str) -> set[str]:
    return {col["name"] for col in inspector.get_columns(name)}


def _index_names(inspector, name: str) -> set[str]:
    return {idx["name"] for idx in inspector.get_indexes(name)}


def _fk_names(inspector, name: str) -> set[str]:
    return {fk["name"] for fk in inspector.get_foreign_keys(name) if fk.get("name")}


def upgrade() -> None:
    try:
        bind = op.get_bind()
        inspector = sa.inspect(bind)

        if not _table_exists(inspector, "game_sessions"):
            op.create_table(
                "game_sessions",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "owner_user_id",
                    sa.Integer(),
                    sa.ForeignKey("users.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column("played_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.Column("notes", sa.Text(), nullable=True),
                sa.Column("win_via_combo", sa.Boolean(), nullable=False, server_default=sa.false()),
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            )
            op.create_index("ix_game_sessions_owner_user_id", "game_sessions", ["owner_user_id"])
            op.create_index("ix_game_sessions_played_at", "game_sessions", ["played_at"])
            op.create_index("ix_game_sessions_created_at", "game_sessions", ["created_at"])
            op.create_index("ix_game_sessions_updated_at", "game_sessions", ["updated_at"])

        if not _table_exists(inspector, "game_players"):
            op.create_table(
                "game_players",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "user_id",
                    sa.Integer(),
                    sa.ForeignKey("users.id", ondelete="SET NULL"),
                    nullable=True,
                ),
                sa.Column("display_name", sa.String(length=120), nullable=True),
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.CheckConstraint(
                    "user_id IS NOT NULL OR display_name IS NOT NULL",
                    name="ck_game_player_identity",
                ),
            )
            op.create_index("ix_game_players_user_id", "game_players", ["user_id"])
            op.create_index("ix_game_players_display_name", "game_players", ["display_name"])
            op.create_index("ix_game_players_created_at", "game_players", ["created_at"])

        if not _table_exists(inspector, "game_decks"):
            op.create_table(
                "game_decks",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "session_id",
                    sa.Integer(),
                    sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column(
                    "folder_id",
                    sa.Integer(),
                    sa.ForeignKey("folder.id", ondelete="SET NULL"),
                    nullable=True,
                ),
                sa.Column("deck_name", sa.String(length=200), nullable=False),
                sa.Column("commander_name", sa.String(length=200), nullable=True),
                sa.Column("commander_oracle_id", sa.String(length=64), nullable=True),
                sa.Column("bracket_level", sa.String(length=16), nullable=True),
                sa.Column("bracket_label", sa.String(length=120), nullable=True),
                sa.Column("bracket_score", sa.Float(), nullable=True),
                sa.Column("power_score", sa.Float(), nullable=True),
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            )
            op.create_index("ix_game_decks_session_id", "game_decks", ["session_id"])
            op.create_index("ix_game_decks_folder_id", "game_decks", ["folder_id"])
            op.create_index("ix_game_decks_created_at", "game_decks", ["created_at"])

        if not _table_exists(inspector, "game_seats"):
            op.create_table(
                "game_seats",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "session_id",
                    sa.Integer(),
                    sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column("seat_number", sa.Integer(), nullable=False),
                sa.Column("turn_order", sa.Integer(), nullable=False),
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.UniqueConstraint("session_id", "seat_number", name="uq_game_seat_number"),
                sa.UniqueConstraint("session_id", "turn_order", name="uq_game_seat_turn_order"),
                sa.CheckConstraint("seat_number >= 1 AND seat_number <= 4", name="ck_game_seat_number"),
                sa.CheckConstraint("turn_order >= 1 AND turn_order <= 4", name="ck_game_seat_turn_order"),
            )
            op.create_index("ix_game_seats_session_id", "game_seats", ["session_id"])
            op.create_index("ix_game_seats_seat_number", "game_seats", ["seat_number"])
            op.create_index("ix_game_seats_turn_order", "game_seats", ["turn_order"])
            op.create_index("ix_game_seats_created_at", "game_seats", ["created_at"])

        if not _table_exists(inspector, "game_seat_assignments"):
            op.create_table(
                "game_seat_assignments",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "session_id",
                    sa.Integer(),
                    sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column(
                    "seat_id",
                    sa.Integer(),
                    sa.ForeignKey("game_seats.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column(
                    "player_id",
                    sa.Integer(),
                    sa.ForeignKey("game_players.id", ondelete="SET NULL"),
                    nullable=True,
                ),
                sa.Column(
                    "deck_id",
                    sa.Integer(),
                    sa.ForeignKey("game_decks.id", ondelete="SET NULL"),
                    nullable=True,
                ),
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.UniqueConstraint("seat_id", name="uq_game_seat_assignment_seat"),
            )
            op.create_index("ix_game_seat_assignments_session_id", "game_seat_assignments", ["session_id"])
            op.create_index("ix_game_seat_assignments_seat_id", "game_seat_assignments", ["seat_id"])
            op.create_index("ix_game_seat_assignments_player_id", "game_seat_assignments", ["player_id"])
            op.create_index("ix_game_seat_assignments_deck_id", "game_seat_assignments", ["deck_id"])
            op.create_index("ix_game_seat_assignments_created_at", "game_seat_assignments", ["created_at"])

        inspector = sa.inspect(bind)
        if _table_exists(inspector, "game_sessions") and _table_exists(inspector, "game_seats"):
            columns = _column_names(inspector, "game_sessions")
            if "winner_seat_id" not in columns:
                op.add_column("game_sessions", sa.Column("winner_seat_id", sa.Integer(), nullable=True))
                op.create_index("ix_game_sessions_winner_seat_id", "game_sessions", ["winner_seat_id"])

            fk_names = _fk_names(inspector, "game_sessions")
            if "fk_game_sessions_winner_seat" not in fk_names and "winner_seat_id" in _column_names(inspector, "game_sessions"):
                op.create_foreign_key(
                    "fk_game_sessions_winner_seat",
                    "game_sessions",
                    "game_seats",
                    ["winner_seat_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

        if bind.dialect.name == "sqlite" and _table_exists(inspector, "game_sessions"):
            bind.exec_driver_sql(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS game_sessions_fts
                USING fts5(
                  notes,
                  content='game_sessions', content_rowid='id'
                );
                """
            )
            bind.exec_driver_sql(
                """
                INSERT INTO game_sessions_fts(rowid, notes)
                  SELECT id, lower(coalesce(notes, ''))
                  FROM game_sessions
                  WHERE notes IS NOT NULL
                    AND notes != ''
                    AND NOT EXISTS (SELECT 1 FROM game_sessions_fts WHERE rowid = game_sessions.id);
                """
            )
            bind.exec_driver_sql(
                """
                CREATE TRIGGER IF NOT EXISTS game_sessions_ai AFTER INSERT ON game_sessions BEGIN
                  INSERT INTO game_sessions_fts(rowid, notes)
                  VALUES (new.id, lower(coalesce(new.notes, '')));
                END;
                """
            )
            bind.exec_driver_sql(
                """
                CREATE TRIGGER IF NOT EXISTS game_sessions_au AFTER UPDATE ON game_sessions BEGIN
                  UPDATE game_sessions_fts
                     SET notes = lower(coalesce(new.notes, ''))
                   WHERE rowid = new.id;
                END;
                """
            )
            bind.exec_driver_sql(
                """
                CREATE TRIGGER IF NOT EXISTS game_sessions_ad AFTER DELETE ON game_sessions BEGIN
                  DELETE FROM game_sessions_fts WHERE rowid = old.id;
                END;
                """
            )

        _LOG.info("Game tracking tables created.")
    except Exception:
        _LOG.error("Failed to create game tracking tables.", exc_info=True)
        raise


def downgrade() -> None:
    _LOG.info("Downgrade skipped for game tracking tables.")
