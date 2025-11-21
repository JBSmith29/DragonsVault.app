"""Add username column to users"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.sql import table, column


# revision identifiers, used by Alembic.
revision = "20251114_add_usernames"
down_revision = "f7f6d8ab1b0e"
branch_labels = None
depends_on = None


def _slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9._-]", "", value)
    return value or "user"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    column_names = {col["name"] for col in inspector.get_columns("users")}
    if "username" not in column_names:
        op.add_column("users", sa.Column("username", sa.String(length=80), nullable=True))

    index_names = {idx["name"] for idx in inspector.get_indexes("users")}
    if "ix_users_username" not in index_names:
        op.create_index("ix_users_username", "users", ["username"], unique=True)

    conn = bind
    rows = conn.execute(sa.text("SELECT id, email FROM users")).fetchall()
    used = set()
    for row in rows:
        base = _slugify((row.email or "").split("@")[0]) or f"user{row.id}"
        candidate = base
        counter = 2
        while candidate in used:
            candidate = f"{base}{counter}"
            counter += 1
        used.add(candidate)
        conn.execute(sa.text("UPDATE users SET username=:username WHERE id=:id"), {"username": candidate, "id": row.id})

    if op.get_context().dialect.name == "sqlite":
        # SQLite cannot run ALTER COLUMN directly; batch recreate instead
        with op.batch_alter_table("users") as batch:
            batch.alter_column("username", existing_type=sa.String(length=80), nullable=False)
    else:
        op.alter_column("users", "username", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
