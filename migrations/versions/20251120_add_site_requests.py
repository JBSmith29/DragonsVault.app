"""Add site_requests for bug/feature submissions from Contact."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251120_add_site_requests"
down_revision = "9f5d92a01961"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("request_type", sa.String(length=20), nullable=False, server_default=sa.text("'bug'")),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'not_started'"),
        ),
        sa.Column("requester_name", sa.String(length=120), nullable=True),
        sa.Column("requester_email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_site_requests_request_type", "site_requests", ["request_type"], unique=False)
    op.create_index("ix_site_requests_status", "site_requests", ["status"], unique=False)
    op.create_index("ix_site_requests_created_at", "site_requests", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_site_requests_created_at", table_name="site_requests")
    op.drop_index("ix_site_requests_status", table_name="site_requests")
    op.drop_index("ix_site_requests_request_type", table_name="site_requests")
    op.drop_table("site_requests")
