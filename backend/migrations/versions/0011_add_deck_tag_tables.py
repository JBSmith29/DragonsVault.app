"""Add canonical deck tag tables."""

from __future__ import annotations

import logging

from alembic import op

revision = "0011_add_deck_tag_tables"
down_revision = "0010_add_derived_versions"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)


def upgrade() -> None:
    try:
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS deck_tags (
                tag TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
            """
        )
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS deck_tag_aliases (
                alias TEXT PRIMARY KEY,
                canonical_tag TEXT NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        _LOG.info("Ensured deck_tags and deck_tag_aliases tables exist.")
    except Exception:
        _LOG.error("Failed to create deck tag tables.", exc_info=True)
        raise


def downgrade() -> None:
    _LOG.info("Downgrade skipped for deck tag tables.")
