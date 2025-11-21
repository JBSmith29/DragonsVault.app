"""Add card quantity/lang/is_foil/oracle_id (columns only)

Revision ID: 20250922_a_add_card_fields_and_indexes
Revises: None
Create Date: 2025-09-22

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250922_a_add_card_fields_and_indexes"
down_revision = None
branch_labels = None
depends_on = None

def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns(table)}
    return column in cols

def upgrade():
    # Add columns one by one, no positional hints, safe for SQLite
    if not _has_column("cards", "quantity"):
        op.add_column("cards", sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"))
        # drop the default after backfill so future inserts rely on app logic
        op.alter_column("cards", "quantity", server_default=None)

    if not _has_column("cards", "lang"):
        op.add_column("cards", sa.Column("lang", sa.String(length=8), nullable=True))

    if not _has_column("cards", "is_foil"):
        # SQLite boolean is int 0/1
        op.add_column("cards", sa.Column("is_foil", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        op.alter_column("cards", "is_foil", server_default=None)

    if not _has_column("cards", "oracle_id"):
        op.add_column("cards", sa.Column("oracle_id", sa.String(length=64), nullable=True))

    # NOTE: Indexes are handled in a separate migration ("ensure card indexes").

def downgrade():
    # Drop columns if they exist (Alembic will batch-recreate as needed)
    if _has_column("cards", "oracle_id"):
        op.drop_column("cards", "oracle_id")
    if _has_column("cards", "is_foil"):
        op.drop_column("cards", "is_foil")
    if _has_column("cards", "lang"):
        op.drop_column("cards", "lang")
    if _has_column("cards", "quantity"):
        op.drop_column("cards", "quantity")
