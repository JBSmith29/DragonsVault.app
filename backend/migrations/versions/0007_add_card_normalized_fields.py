"""Add normalized card metadata fields."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007_add_card_normalized_fields"
down_revision = "0006_deck_tag_synergy_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cards") as batch_op:
        batch_op.add_column(sa.Column("oracle_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("mana_value", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("colors", sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column("color_identity", sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column("layout", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("faces_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cards") as batch_op:
        batch_op.drop_column("faces_json")
        batch_op.drop_column("layout")
        batch_op.drop_column("color_identity")
        batch_op.drop_column("colors")
        batch_op.drop_column("mana_value")
        batch_op.drop_column("oracle_text")
