"""Allow storing multiple commander oracle IDs per deck."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7e0f548bfe21"
down_revision = "f4a1a2c8c7d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("folder") as batch:
        batch.alter_column(
            "commander_oracle_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("folder") as batch:
        batch.alter_column(
            "commander_oracle_id",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=True,
        )
