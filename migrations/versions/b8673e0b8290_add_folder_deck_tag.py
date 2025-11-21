"""Add Folder.deck_tag for deck archetype tagging."""

import sqlalchemy as sa
from alembic import op


revision = "b8673e0b8290"
down_revision = "3f4a1f0ccbd1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("folder")}
    if "deck_tag" not in columns:
        op.add_column("folder", sa.Column("deck_tag", sa.String(length=120), nullable=True))
        op.create_index("ix_folder_deck_tag", "folder", ["deck_tag"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes("folder")}
    columns = {col["name"] for col in inspector.get_columns("folder")}
    if "ix_folder_deck_tag" in indexes:
        op.drop_index("ix_folder_deck_tag", table_name="folder")
    if "deck_tag" in columns:
        op.drop_column("folder", "deck_tag")
