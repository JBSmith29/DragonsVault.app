"""Add game pod tables and manual roster decks."""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0024_add_game_pods"
down_revision = "0023_add_user_friends"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)


def upgrade() -> None:
    try:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        tables = set(inspector.get_table_names())

        if "game_pods" not in tables:
            op.create_table(
                "game_pods",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "owner_user_id",
                    sa.Integer(),
                    sa.ForeignKey("users.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column("name", sa.String(length=120), nullable=False),
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.UniqueConstraint("owner_user_id", "name", name="uq_game_pod_owner_name"),
            )
            op.create_index("ix_game_pods_owner_user_id", "game_pods", ["owner_user_id"])
            op.create_index("ix_game_pods_name", "game_pods", ["name"])
            op.create_index("ix_game_pods_created_at", "game_pods", ["created_at"])
            op.create_index("ix_game_pods_updated_at", "game_pods", ["updated_at"])

        if "game_pod_members" not in tables:
            op.create_table(
                "game_pod_members",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "pod_id",
                    sa.Integer(),
                    sa.ForeignKey("game_pods.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column(
                    "roster_player_id",
                    sa.Integer(),
                    sa.ForeignKey("game_roster_players.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.UniqueConstraint("pod_id", "roster_player_id", name="uq_game_pod_member"),
            )
            op.create_index("ix_game_pod_members_pod_id", "game_pod_members", ["pod_id"])
            op.create_index("ix_game_pod_members_roster_player_id", "game_pod_members", ["roster_player_id"])
            op.create_index("ix_game_pod_members_created_at", "game_pod_members", ["created_at"])

        if "game_roster_decks" in tables:
            columns = {col["name"] for col in inspector.get_columns("game_roster_decks")}
            if "deck_name" not in columns:
                op.add_column("game_roster_decks", sa.Column("deck_name", sa.String(length=200), nullable=True))

        _LOG.info("Game pod tables created.")
    except Exception:
        _LOG.error("Failed to create game pod tables.", exc_info=True)
        raise


def downgrade() -> None:
    _LOG.info("Downgrade skipped for game pod tables.")
