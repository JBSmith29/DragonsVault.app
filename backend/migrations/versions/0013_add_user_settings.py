"""Add user_settings table for dashboard preferences."""

from __future__ import annotations

import logging

from alembic import op

revision = "0013_add_user_settings"
down_revision = "0012_add_build_sessions"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)


def upgrade() -> None:
    try:
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        _LOG.info("Ensured user_settings table exists.")
    except Exception:
        _LOG.error("Failed to create user_settings table.", exc_info=True)
        raise


def downgrade() -> None:
    _LOG.info("Downgrade skipped for user_settings.")
