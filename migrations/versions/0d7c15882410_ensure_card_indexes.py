"""ensure card indexes

Revision ID: 0d7c15882410
Revises: perf_merge_20250924_165027
Create Date: 2025-09-25 13:56:42.299320

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0d7c15882410'
down_revision = 'perf_merge_20250924_165027'
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}

def upgrade():
    # Only create indexes for columns that actually exist on 'cards'
    idx_spec = [
        ("ix_cards_name",             ["name"]),
        ("ix_cards_folder_id",        ["folder_id"]),
        ("ix_cards_set_code",         ["set_code"]),
        ("ix_cards_lang",             ["lang"]),
        ("ix_cards_is_foil",          ["is_foil"]),
        ("ix_cards_color_identity",   ["color_identity"]),   # will be skipped if column absent
        ("ix_cards_rarity",           ["rarity"]),
        ("ix_cards_collector_number", ["collector_number"]),
    ]

    for idx_name, cols in idx_spec:
        if all(_has_column("cards", c) for c in cols):
            op.create_index(idx_name, "cards", cols, unique=False, if_not_exists=True)
        else:
            # Column missing -> skip this index quietly
            pass

# keep your existing downgrade() with op.drop_index(..., if_exists=True)
