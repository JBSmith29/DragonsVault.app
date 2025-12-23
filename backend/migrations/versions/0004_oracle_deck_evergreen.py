"""Add oracle deck and evergreen keyword tag tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_oracle_deck_evergreen"
down_revision = "0003_add_oracle_tag_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oracle_deck_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("oracle_id", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=sa.text("'derived'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("oracle_id", "tag", "source", name="uq_oracle_deck_tag"),
    )
    op.create_index("ix_oracle_deck_tags_oracle_id", "oracle_deck_tags", ["oracle_id"], unique=False)
    op.create_index("ix_oracle_deck_tags_tag", "oracle_deck_tags", ["tag"], unique=False)

    op.create_table(
        "oracle_evergreen_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("oracle_id", sa.String(length=64), nullable=False),
        sa.Column("keyword", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=sa.text("'derived'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("oracle_id", "keyword", "source", name="uq_oracle_evergreen_tag"),
    )
    op.create_index("ix_oracle_evergreen_tags_oracle_id", "oracle_evergreen_tags", ["oracle_id"], unique=False)
    op.create_index("ix_oracle_evergreen_tags_keyword", "oracle_evergreen_tags", ["keyword"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_oracle_evergreen_tags_keyword", table_name="oracle_evergreen_tags")
    op.drop_index("ix_oracle_evergreen_tags_oracle_id", table_name="oracle_evergreen_tags")
    op.drop_table("oracle_evergreen_tags")

    op.drop_index("ix_oracle_deck_tags_tag", table_name="oracle_deck_tags")
    op.drop_index("ix_oracle_deck_tags_oracle_id", table_name="oracle_deck_tags")
    op.drop_table("oracle_deck_tags")
