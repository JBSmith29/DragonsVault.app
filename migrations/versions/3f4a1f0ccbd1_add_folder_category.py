"""Add Folder.category to classify decks vs collection buckets."""

from alembic import op
import sqlalchemy as sa


revision = "3f4a1f0ccbd1"
down_revision = "9dded09cdca8"
branch_labels = None
depends_on = None


DEFAULT_COLLECTION_FOLDERS = ("lands", "common", "uncommon", "rare", "mythic", "to add")


def upgrade():
    op.add_column(
        "folder",
        sa.Column("category", sa.String(length=20), nullable=False, server_default="deck"),
    )
    if DEFAULT_COLLECTION_FOLDERS:
        in_clause = ", ".join(f"'{name}'" for name in DEFAULT_COLLECTION_FOLDERS)
        op.execute(
            f"UPDATE folder SET category = 'collection' WHERE lower(name) IN ({in_clause})"
        )
    op.create_index("ix_folder_category", "folder", ["category"])


def downgrade():
    op.drop_index("ix_folder_category", table_name="folder")
    op.drop_column("folder", "category")
