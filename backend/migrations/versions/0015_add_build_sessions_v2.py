"""Add build session tables for proxy deck building."""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0015_add_build_sessions_v2"
down_revision = "0014_remove_build_a_deck"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)


def upgrade() -> None:
    try:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        tables = set(inspector.get_table_names())

        if "build_sessions" not in tables:
            op.create_table(
                "build_sessions",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
                sa.Column("commander_oracle_id", sa.String(length=64), nullable=True, index=True),
                sa.Column("commander_name", sa.String(length=200), nullable=True),
                sa.Column("tags_json", sa.JSON(), nullable=True),
                sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            )
            op.create_index("ix_build_sessions_owner_user_id", "build_sessions", ["owner_user_id"])
            op.create_index("ix_build_sessions_commander_oracle_id", "build_sessions", ["commander_oracle_id"])
            op.create_index("ix_build_sessions_created_at", "build_sessions", ["created_at"])
            op.create_index("ix_build_sessions_updated_at", "build_sessions", ["updated_at"])
        else:
            existing_indexes = {idx["name"] for idx in inspector.get_indexes("build_sessions")}
            if "ix_build_sessions_owner_user_id" not in existing_indexes:
                op.create_index("ix_build_sessions_owner_user_id", "build_sessions", ["owner_user_id"])
            if "ix_build_sessions_commander_oracle_id" not in existing_indexes:
                op.create_index("ix_build_sessions_commander_oracle_id", "build_sessions", ["commander_oracle_id"])
            if "ix_build_sessions_created_at" not in existing_indexes:
                op.create_index("ix_build_sessions_created_at", "build_sessions", ["created_at"])
            if "ix_build_sessions_updated_at" not in existing_indexes:
                op.create_index("ix_build_sessions_updated_at", "build_sessions", ["updated_at"])

        if "build_session_cards" not in tables:
            op.create_table(
                "build_session_cards",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("session_id", sa.Integer(), sa.ForeignKey("build_sessions.id", ondelete="CASCADE"), nullable=False, index=True),
                sa.Column("card_oracle_id", sa.String(length=64), nullable=False, index=True),
                sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
                sa.Column("added_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.UniqueConstraint("session_id", "card_oracle_id", name="uq_build_session_card"),
            )
            op.create_index("ix_build_session_cards_session_id", "build_session_cards", ["session_id"])
            op.create_index("ix_build_session_cards_card_oracle_id", "build_session_cards", ["card_oracle_id"])
            op.create_index("ix_build_session_cards_added_at", "build_session_cards", ["added_at"])
        else:
            existing_indexes = {idx["name"] for idx in inspector.get_indexes("build_session_cards")}
            existing_uniques = {uq["name"] for uq in inspector.get_unique_constraints("build_session_cards")}
            if "uq_build_session_card" not in existing_uniques:
                op.create_unique_constraint("uq_build_session_card", "build_session_cards", ["session_id", "card_oracle_id"])
            if "ix_build_session_cards_session_id" not in existing_indexes:
                op.create_index("ix_build_session_cards_session_id", "build_session_cards", ["session_id"])
            if "ix_build_session_cards_card_oracle_id" not in existing_indexes:
                op.create_index("ix_build_session_cards_card_oracle_id", "build_session_cards", ["card_oracle_id"])
            if "ix_build_session_cards_added_at" not in existing_indexes:
                op.create_index("ix_build_session_cards_added_at", "build_session_cards", ["added_at"])
        _LOG.info("Build session tables created.")
    except Exception:
        _LOG.error("Failed to create build session tables.", exc_info=True)
        raise


def downgrade() -> None:
    _LOG.info("Downgrade skipped for build session tables.")
