"""Add performance indexes for common card lookups."""

from __future__ import annotations

from alembic import op

revision = "0018_perf_indexes"
down_revision = "0017_deck_tag_db"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_cards_folder_oracle", "cards", ["folder_id", "oracle_id"], unique=False)
    op.create_index(
        "ix_cards_folder_print",
        "cards",
        ["folder_id", "set_code", "collector_number", "lang", "is_foil"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_cards_folder_print", table_name="cards")
    op.drop_index("ix_cards_folder_oracle", table_name="cards")
