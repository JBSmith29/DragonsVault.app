"""Enforce unique print per folder

Revision ID: 20250922_b_enforce_unique_print_in_folder
Revises: 20250922_a_add_card_fields_and_indexes
Create Date: 2025-09-22 22:11:16.375517

"""
from alembic import op

revision = "20250922_b_enforce_unique_print_in_folder"
down_revision = "20250922_a_add_card_fields_and_indexes"
branch_labels = None
depends_on = None

def upgrade():
    # Unique per printed card in a folder (lang + foil split variants)
    op.create_index(
        "uq_card_print_in_folder",
        "cards",
        ["name", "folder_id", "set_code", "collector_number", "lang", "is_foil"],
        unique=True,
    )

def downgrade():
    op.drop_index("uq_card_print_in_folder", table_name="cards")
