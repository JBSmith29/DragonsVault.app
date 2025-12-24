"""Add deck tag synergy tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_deck_tag_synergy_tables"
down_revision = "0005_oracle_core_role_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deck_tag_core_role_synergies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deck_tag", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=sa.text("'derived'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("deck_tag", "role", "source", name="uq_deck_tag_core_role_synergy"),
    )
    op.create_index("ix_deck_tag_core_role_synergies_deck_tag", "deck_tag_core_role_synergies", ["deck_tag"])
    op.create_index("ix_deck_tag_core_role_synergies_role", "deck_tag_core_role_synergies", ["role"])

    op.create_table(
        "deck_tag_evergreen_synergies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deck_tag", sa.String(length=128), nullable=False),
        sa.Column("keyword", sa.String(length=128), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=sa.text("'derived'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("deck_tag", "keyword", "source", name="uq_deck_tag_evergreen_synergy"),
    )
    op.create_index("ix_deck_tag_evergreen_synergies_deck_tag", "deck_tag_evergreen_synergies", ["deck_tag"])
    op.create_index("ix_deck_tag_evergreen_synergies_keyword", "deck_tag_evergreen_synergies", ["keyword"])

    op.create_table(
        "deck_tag_card_synergies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deck_tag", sa.String(length=128), nullable=False),
        sa.Column("oracle_id", sa.String(length=64), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=sa.text("'derived'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("deck_tag", "oracle_id", "source", name="uq_deck_tag_card_synergy"),
    )
    op.create_index("ix_deck_tag_card_synergies_deck_tag", "deck_tag_card_synergies", ["deck_tag"])
    op.create_index("ix_deck_tag_card_synergies_oracle_id", "deck_tag_card_synergies", ["oracle_id"])


def downgrade() -> None:
    op.drop_index("ix_deck_tag_card_synergies_oracle_id", table_name="deck_tag_card_synergies")
    op.drop_index("ix_deck_tag_card_synergies_deck_tag", table_name="deck_tag_card_synergies")
    op.drop_table("deck_tag_card_synergies")

    op.drop_index("ix_deck_tag_evergreen_synergies_keyword", table_name="deck_tag_evergreen_synergies")
    op.drop_index("ix_deck_tag_evergreen_synergies_deck_tag", table_name="deck_tag_evergreen_synergies")
    op.drop_table("deck_tag_evergreen_synergies")

    op.drop_index("ix_deck_tag_core_role_synergies_role", table_name="deck_tag_core_role_synergies")
    op.drop_index("ix_deck_tag_core_role_synergies_deck_tag", table_name="deck_tag_core_role_synergies")
    op.drop_table("deck_tag_core_role_synergies")
