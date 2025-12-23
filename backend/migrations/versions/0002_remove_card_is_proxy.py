"""Remove per-card proxy flag."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0002_remove_card_is_proxy"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cards") as batch:
        batch.drop_index("ix_cards_is_proxy")
        batch.drop_column("is_proxy")


def downgrade() -> None:
    with op.batch_alter_table("cards") as batch:
        batch.add_column(
            sa.Column(
                "is_proxy",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_index("ix_cards_is_proxy", ["is_proxy"], unique=False)
