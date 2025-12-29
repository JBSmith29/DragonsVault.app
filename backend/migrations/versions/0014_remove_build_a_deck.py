"""Remove build-a-deck schema and data."""

from __future__ import annotations

import logging

from alembic import op

revision = "0014_remove_build_a_deck"
down_revision = "0013_add_user_settings"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)

_BUILD_FOLDER_IDS_SQL = """
SELECT id FROM folder WHERE category = 'build'
UNION
SELECT folder_id FROM folder_roles WHERE role = 'build'
"""


def upgrade() -> None:
    try:
        op.execute(f"CREATE TEMP TABLE build_folder_ids AS {_BUILD_FOLDER_IDS_SQL}")
        op.execute("DELETE FROM cards WHERE folder_id IN (SELECT id FROM build_folder_ids)")
        op.execute("DELETE FROM deck_stats WHERE folder_id IN (SELECT id FROM build_folder_ids)")
        op.execute("DELETE FROM folder_share WHERE folder_id IN (SELECT id FROM build_folder_ids)")
        op.execute("DELETE FROM commander_bracket_cache WHERE folder_id IN (SELECT id FROM build_folder_ids)")
        op.execute("DELETE FROM folder_roles WHERE folder_id IN (SELECT id FROM build_folder_ids)")
        op.execute("DELETE FROM folder_roles WHERE role = 'build'")
        op.execute("DELETE FROM folder WHERE id IN (SELECT id FROM build_folder_ids)")
        op.execute("DROP TABLE IF EXISTS build_folder_ids")
        op.execute("DROP TABLE IF EXISTS deck_build_sessions")
        _LOG.info("Removed build-a-deck data and build sessions.")
    except Exception:
        _LOG.error("Failed to remove build-a-deck data.", exc_info=True)
        raise

    try:
        with op.batch_alter_table("folder") as batch_op:
            try:
                batch_op.drop_constraint("ck_folder_category", type_="check")
            except Exception:
                _LOG.warning("Folder category constraint not found; recreating.")
            batch_op.create_check_constraint("ck_folder_category", "category in ('deck','collection')")
        _LOG.info("Updated folder category constraint.")
    except Exception:
        _LOG.error("Failed to update folder category constraint.", exc_info=True)
        raise


def downgrade() -> None:
    _LOG.info("Downgrade skipped for build-a-deck removal.")
