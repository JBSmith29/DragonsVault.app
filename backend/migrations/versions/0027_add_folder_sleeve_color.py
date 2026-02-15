"""Add sleeve color field to folders."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0027_add_folder_sleeve_color"
down_revision = "5fdb8d649e43"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("folder", sa.Column("sleeve_color", sa.String(length=64), nullable=True))


def downgrade():
    op.drop_column("folder", "sleeve_color")
