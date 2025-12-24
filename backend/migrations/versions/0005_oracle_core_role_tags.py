"""Add oracle core role tag table."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_oracle_core_role_tags"
down_revision = "0004_oracle_deck_evergreen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oracle_core_role_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("oracle_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=sa.text("'core-role'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("oracle_id", "role", "source", name="uq_oracle_core_role_tag"),
    )
    op.create_index("ix_oracle_core_role_tags_oracle_id", "oracle_core_role_tags", ["oracle_id"], unique=False)
    op.create_index("ix_oracle_core_role_tags_role", "oracle_core_role_tags", ["role"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_oracle_core_role_tags_role", table_name="oracle_core_role_tags")
    op.drop_index("ix_oracle_core_role_tags_oracle_id", table_name="oracle_core_role_tags")
    op.drop_table("oracle_core_role_tags")
