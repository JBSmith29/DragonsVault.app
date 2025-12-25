"""Add deck build session metadata table."""

from __future__ import annotations

import logging

from alembic import op

revision = "0012_add_build_sessions"
down_revision = "0011_add_deck_tag_tables"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)


def upgrade() -> None:
    try:
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS deck_build_sessions (
                folder_id INTEGER PRIMARY KEY,
                tags_json JSON,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                FOREIGN KEY(folder_id) REFERENCES folder(id) ON DELETE CASCADE
            )
            """
        )
        _LOG.info("Ensured deck_build_sessions table exists.")
    except Exception:
        _LOG.error("Failed to create deck_build_sessions table.", exc_info=True)
        raise


def downgrade() -> None:
    _LOG.info("Downgrade skipped for deck_build_sessions.")
