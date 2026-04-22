"""Add password reset token fields to users table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_add_pw_reset_token_to_users"
down_revision = "0027_add_folder_sleeve_color"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("pw_reset_token_hash", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pw_reset_token_expires_at", sa.DateTime(), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_users_pw_reset_token_hash", ["pw_reset_token_hash"]
        )
        batch_op.create_index(
            "ix_users_pw_reset_token_hash", ["pw_reset_token_hash"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index("ix_users_pw_reset_token_hash")
        batch_op.drop_constraint("uq_users_pw_reset_token_hash", type_="unique")
        batch_op.drop_column("pw_reset_token_expires_at")
        batch_op.drop_column("pw_reset_token_hash")
