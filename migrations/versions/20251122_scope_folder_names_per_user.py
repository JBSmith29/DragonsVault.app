"""Allow duplicate folder names per user."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20251122_scope_folder_names_per_user"
down_revision = "20251120_add_site_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {idx["name"]: idx for idx in inspector.get_indexes("folder")}
    if "ix_folder_name" in indexes:
        op.drop_index("ix_folder_name", table_name="folder")

    with op.batch_alter_table("folder") as batch:
        batch.alter_column(
            "name",
            existing_type=sa.String(length=120),
            nullable=False,
            existing_nullable=False,
            unique=False,
            existing_unique=True,
        )
        batch.create_unique_constraint("uq_folder_owner_name", ["owner_user_id", "name"])
        batch.create_index("ix_folder_name", ["name"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {idx["name"]: idx for idx in inspector.get_indexes("folder")}
    if "ix_folder_name" in indexes:
        op.drop_index("ix_folder_name", table_name="folder")

    with op.batch_alter_table("folder") as batch:
        batch.drop_constraint("uq_folder_owner_name", type_="unique")
        batch.alter_column(
            "name",
            existing_type=sa.String(length=120),
            nullable=False,
            existing_nullable=False,
            unique=True,
            existing_unique=False,
        )
        batch.create_index("ix_folder_name", ["name"], unique=True)
