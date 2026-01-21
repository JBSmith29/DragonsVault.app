"""Increase commander_oracle_id field length for partner commanders."""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0025_oracle_id_128"
down_revision = "0024_add_game_pods"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)


def upgrade() -> None:
    """Increase commander_oracle_id field length from 64 to 128 characters."""
    try:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        tables = set(inspector.get_table_names())

        if "game_decks" in tables:
            # Alter the column to increase length
            op.alter_column(
                "game_decks",
                "commander_oracle_id",
                type_=sa.String(128),
                existing_type=sa.String(64),
                nullable=True
            )
            _LOG.info("Increased commander_oracle_id field length to 128 characters.")
        else:
            _LOG.warning("game_decks table not found, skipping migration.")
    except Exception:
        _LOG.error("Failed to increase commander_oracle_id field length.", exc_info=True)
        raise


def downgrade() -> None:
    """Decrease commander_oracle_id field length back to 64 characters."""
    try:
        op.alter_column(
            "game_decks",
            "commander_oracle_id",
            type_=sa.String(64),
            existing_type=sa.String(128),
            nullable=True
        )
        _LOG.info("Decreased commander_oracle_id field length back to 64 characters.")
    except Exception:
        _LOG.error("Failed to decrease commander_oracle_id field length.", exc_info=True)
        raise