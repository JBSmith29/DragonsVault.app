"""Add folder roles table for deck semantics."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0008_add_folder_roles"
down_revision = "0007_add_card_normalized_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "folder_roles",
        sa.Column("folder_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["folder_id"], ["folder.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("folder_id", "role"),
    )
    op.create_index("ix_folder_roles_role", "folder_roles", ["role"], unique=False)
    op.create_index("ix_folder_roles_folder_id", "folder_roles", ["folder_id"], unique=False)

    op.execute(
        """
        INSERT INTO folder_roles (folder_id, role)
        SELECT id, COALESCE(category, 'deck') FROM folder
        """
    )


def downgrade() -> None:
    op.drop_index("ix_folder_roles_folder_id", table_name="folder_roles")
    op.drop_index("ix_folder_roles_role", table_name="folder_roles")
    op.drop_table("folder_roles")
