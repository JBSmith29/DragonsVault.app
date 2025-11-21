"""Add proxy deck support and folder owners.

Revision ID: 20251018_add_proxy_decks_and_owner
Revises: b8673e0b8290_add_folder_deck_tag
Create Date: 2025-10-18 04:32:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "20251018_add_proxy_decks_and_owner"
down_revision = "b8673e0b8290"
branch_labels = None
depends_on = None


def upgrade():
    # Folder metadata
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    folder_columns = {col["name"] for col in inspector.get_columns("folder")}
    if "owner" not in folder_columns:
        op.add_column("folder", sa.Column("owner", sa.String(length=120), nullable=True))
    if "is_proxy" not in folder_columns:
        op.add_column(
            "folder",
            sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )

    folder_indexes = {idx["name"] for idx in inspector.get_indexes("folder")}
    if "ix_folder_owner" not in folder_indexes:
        op.create_index("ix_folder_owner", "folder", ["owner"], unique=False)
    if "ix_folder_is_proxy" not in folder_indexes:
        op.create_index("ix_folder_is_proxy", "folder", ["is_proxy"], unique=False)

    # Card proxy flag
    card_columns = {col["name"] for col in inspector.get_columns("cards")}
    if "is_proxy" not in card_columns:
        op.add_column(
            "cards",
            sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )

    card_indexes = {idx["name"] for idx in inspector.get_indexes("cards")}
    if "ix_cards_is_proxy" not in card_indexes:
        op.create_index("ix_cards_is_proxy", "cards", ["is_proxy"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    card_indexes = {idx["name"] for idx in inspector.get_indexes("cards")}
    card_columns = {col["name"] for col in inspector.get_columns("cards")}
    if "ix_cards_is_proxy" in card_indexes:
        op.drop_index("ix_cards_is_proxy", table_name="cards")
    if "is_proxy" in card_columns:
        op.drop_column("cards", "is_proxy")

    folder_indexes = {idx["name"] for idx in inspector.get_indexes("folder")}
    folder_columns = {col["name"] for col in inspector.get_columns("folder")}
    if "ix_folder_is_proxy" in folder_indexes:
        op.drop_index("ix_folder_is_proxy", table_name="folder")
    if "ix_folder_owner" in folder_indexes:
        op.drop_index("ix_folder_owner", table_name="folder")
    if "is_proxy" in folder_columns:
        op.drop_column("folder", "is_proxy")
    if "owner" in folder_columns:
        op.drop_column("folder", "owner")
