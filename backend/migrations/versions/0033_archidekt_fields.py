"""Archidekt integration fields.

Revision ID: 0033_archidekt_fields
Revises: 0032_drop_collection_value
Create Date: 2026-06-17

Adds the columns needed to log Commander games against Archidekt decks:
  - users.archidekt_username           — remember a player's handle on profile
  - game_roster_players.archidekt_username — the handle set per pod player
  - folder.archidekt_deck_id           — link an imported deck back to Archidekt
                                         so re-imports refresh the same folder
  - folder.archidekt_bracket           — Archidekt's stated bracket (1-5)

All columns are nullable and additive; existing rows/games are unaffected. Each
add is guarded so the migration is idempotent and safe to re-run.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0033_archidekt_fields"
down_revision = "0032_drop_collection_value"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    try:
        return any(col["name"] == column for col in inspector.get_columns(table))
    except Exception:
        return False


def _has_index(inspector, table: str, index: str) -> bool:
    try:
        return any(ix["name"] == index for ix in inspector.get_indexes(table))
    except Exception:
        return False


def upgrade() -> None:
    inspector = inspect(op.get_bind())

    if not _has_column(inspector, "users", "archidekt_username"):
        op.add_column("users", sa.Column("archidekt_username", sa.String(length=64), nullable=True))

    if not _has_column(inspector, "game_roster_players", "archidekt_username"):
        op.add_column(
            "game_roster_players",
            sa.Column("archidekt_username", sa.String(length=64), nullable=True),
        )

    if not _has_column(inspector, "folder", "archidekt_deck_id"):
        op.add_column("folder", sa.Column("archidekt_deck_id", sa.String(length=32), nullable=True))
    if not _has_index(inspector, "folder", "ix_folder_archidekt_deck_id"):
        op.create_index("ix_folder_archidekt_deck_id", "folder", ["archidekt_deck_id"], unique=False)

    if not _has_column(inspector, "folder", "archidekt_bracket"):
        op.add_column("folder", sa.Column("archidekt_bracket", sa.Integer(), nullable=True))


def downgrade() -> None:
    inspector = inspect(op.get_bind())

    if _has_index(inspector, "folder", "ix_folder_archidekt_deck_id"):
        op.drop_index("ix_folder_archidekt_deck_id", table_name="folder")
    if _has_column(inspector, "folder", "archidekt_bracket"):
        op.drop_column("folder", "archidekt_bracket")
    if _has_column(inspector, "folder", "archidekt_deck_id"):
        op.drop_column("folder", "archidekt_deck_id")
    if _has_column(inspector, "game_roster_players", "archidekt_username"):
        op.drop_column("game_roster_players", "archidekt_username")
    if _has_column(inspector, "users", "archidekt_username"):
        op.drop_column("users", "archidekt_username")
