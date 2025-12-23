"""Add oracle tag tables for keywords, roles, and typal."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0003_add_oracle_tag_tables"
down_revision = "0002_remove_card_is_proxy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oracle_keyword_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("oracle_id", sa.String(length=64), nullable=False),
        sa.Column("keyword", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="derived"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("oracle_id", "keyword", "source", name="uq_oracle_keyword_tag"),
    )
    op.create_index(
        "ix_oracle_keyword_tags_oracle_id",
        "oracle_keyword_tags",
        ["oracle_id"],
        unique=False,
    )
    op.create_index(
        "ix_oracle_keyword_tags_keyword",
        "oracle_keyword_tags",
        ["keyword"],
        unique=False,
    )

    op.create_table(
        "oracle_role_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("oracle_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="derived"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("oracle_id", "role", name="uq_oracle_role_tag"),
    )
    op.create_index(
        "ix_oracle_role_tags_oracle_id",
        "oracle_role_tags",
        ["oracle_id"],
        unique=False,
    )
    op.create_index(
        "ix_oracle_role_tags_role",
        "oracle_role_tags",
        ["role"],
        unique=False,
    )
    op.create_index(
        "ix_oracle_role_tags_is_primary",
        "oracle_role_tags",
        ["is_primary"],
        unique=False,
    )

    op.create_table(
        "oracle_typal_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("oracle_id", sa.String(length=64), nullable=False),
        sa.Column("typal", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="derived"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("oracle_id", "typal", "source", name="uq_oracle_typal_tag"),
    )
    op.create_index(
        "ix_oracle_typal_tags_oracle_id",
        "oracle_typal_tags",
        ["oracle_id"],
        unique=False,
    )
    op.create_index(
        "ix_oracle_typal_tags_typal",
        "oracle_typal_tags",
        ["typal"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_oracle_typal_tags_typal", table_name="oracle_typal_tags")
    op.drop_index("ix_oracle_typal_tags_oracle_id", table_name="oracle_typal_tags")
    op.drop_table("oracle_typal_tags")

    op.drop_index("ix_oracle_role_tags_is_primary", table_name="oracle_role_tags")
    op.drop_index("ix_oracle_role_tags_role", table_name="oracle_role_tags")
    op.drop_index("ix_oracle_role_tags_oracle_id", table_name="oracle_role_tags")
    op.drop_table("oracle_role_tags")

    op.drop_index("ix_oracle_keyword_tags_keyword", table_name="oracle_keyword_tags")
    op.drop_index("ix_oracle_keyword_tags_oracle_id", table_name="oracle_keyword_tags")
    op.drop_table("oracle_keyword_tags")
