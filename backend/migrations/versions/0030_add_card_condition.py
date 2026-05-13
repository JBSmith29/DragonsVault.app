"""Add condition grade column to cards.

Tracks the physical condition of each owned copy using TCG-standard
abbreviations: NM, LP, MP, HP, DMG. Nullable because most existing rows have
no recorded grade, and importing spreadsheets frequently lack the column.

Revision ID: 0030_add_card_condition
Revises: 0029_perf_indexes
Create Date: 2026-05-12
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

_LOG = logging.getLogger(__name__)

revision = "0030_add_card_condition"
down_revision = "0029_perf_indexes"
branch_labels = None
depends_on = None


_TABLE = "cards"
_COLUMN = "condition"
_INDEX = "ix_cards_condition"
_CHECK = "ck_cards_condition_grade"
_ALLOWED = ("NM", "LP", "MP", "HP", "DMG")


def _has_column(inspector, table: str, column: str) -> bool:
    return any(col["name"] == column for col in inspector.get_columns(table))


def _has_index(inspector, table: str, name: str) -> bool:
    return any(idx["name"] == name for idx in inspector.get_indexes(table))


def _has_check(inspector, table: str, name: str) -> bool:
    try:
        constraints = inspector.get_check_constraints(table)
    except NotImplementedError:
        return False
    return any(c.get("name") == name for c in constraints)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _has_column(inspector, _TABLE, _COLUMN):
        _LOG.info("Adding %s.%s column", _TABLE, _COLUMN)
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=4), nullable=True))
    else:
        _LOG.info("Column %s.%s already present, skipping add", _TABLE, _COLUMN)

    # Re-inspect so index/check checks reflect the just-added column
    inspector = inspect(bind)

    if not _has_index(inspector, _TABLE, _INDEX):
        _LOG.info("Creating index %s on %s(%s)", _INDEX, _TABLE, _COLUMN)
        op.create_index(_INDEX, _TABLE, [_COLUMN], unique=False)
    else:
        _LOG.info("Index %s already exists, skipping", _INDEX)

    if not _has_check(inspector, _TABLE, _CHECK):
        values = ", ".join(f"'{v}'" for v in _ALLOWED)
        condition = f"{_COLUMN} IS NULL OR {_COLUMN} IN ({values})"
        # Use batch mode so SQLite (which doesn't support ADD CONSTRAINT) rebuilds the table.
        with op.batch_alter_table(_TABLE) as batch:
            batch.create_check_constraint(_CHECK, condition)
    else:
        _LOG.info("Check constraint %s already exists, skipping", _CHECK)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _has_check(inspector, _TABLE, _CHECK):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_constraint(_CHECK, type_="check")

    if _has_index(inspector, _TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)

    if _has_column(inspector, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
