"""Add build session name field."""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0016_add_build_session_name"
down_revision = "0015_add_build_sessions_v2"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)


def upgrade() -> None:
    try:
        op.add_column("build_sessions", sa.Column("build_name", sa.String(length=200), nullable=True))
        _LOG.info("Build session name column added.")
    except Exception:
        _LOG.error("Failed to add build session name column.", exc_info=True)
        raise


def downgrade() -> None:
    _LOG.info("Downgrade skipped for build session name column.")
