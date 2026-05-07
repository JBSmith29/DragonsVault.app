"""Add critical performance indexes for queries identified in quality review.

This migration adds indexes for:
- cards.oracle_id for card lookups
- game_sessions.created_at for date range queries
- cards.folder_id (if not already indexed by FK)

Revision ID: 0029_add_critical_performance_indexes
Revises: 0028_add_pw_reset_token_to_users
Create Date: 2026-05-05
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import inspect

_LOG = logging.getLogger(__name__)

revision = "0029_perf_indexes"
down_revision = "0028_add_pw_reset_token_to_users"
branch_labels = None
depends_on = None


def _index_exists(connection, table_name: str, index_name: str) -> bool:
    """Check if an index already exists."""
    inspector = inspect(connection)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
    return index_name in existing_indexes


def upgrade():
    """Add performance indexes for critical queries."""
    connection = op.get_bind()
    
    # Index for cards.oracle_id lookups (card detail, print queries)
    if not _index_exists(connection, "cards", "ix_cards_oracle_id"):
        _LOG.info("Creating index ix_cards_oracle_id on cards.oracle_id")
        op.create_index("ix_cards_oracle_id", "cards", ["oracle_id"], unique=False)
    else:
        _LOG.info("Index ix_cards_oracle_id already exists, skipping")
    
    # Index for game_sessions.created_at (date range queries, recent games)
    if not _index_exists(connection, "game_sessions", "ix_game_sessions_created_at"):
        _LOG.info("Creating index ix_game_sessions_created_at on game_sessions.created_at")
        op.create_index("ix_game_sessions_created_at", "game_sessions", ["created_at"], unique=False)
    else:
        _LOG.info("Index ix_game_sessions_created_at already exists, skipping")
    
    # Index for cards.folder_id (if not already covered by FK index)
    # Note: Some databases auto-create FK indexes, others don't
    if not _index_exists(connection, "cards", "ix_cards_folder_id"):
        _LOG.info("Creating index ix_cards_folder_id on cards.folder_id")
        op.create_index("ix_cards_folder_id", "cards", ["folder_id"], unique=False)
    else:
        _LOG.info("Index ix_cards_folder_id already exists, skipping")
    
    _LOG.info("Performance indexes created successfully")


def downgrade():
    """Remove performance indexes."""
    connection = op.get_bind()
    
    indexes_to_drop = [
        ("cards", "ix_cards_oracle_id"),
        ("game_sessions", "ix_game_sessions_created_at"),
        ("cards", "ix_cards_folder_id"),
    ]
    
    for table_name, index_name in indexes_to_drop:
        if _index_exists(connection, table_name, index_name):
            _LOG.info(f"Dropping index {index_name} from {table_name}")
            op.drop_index(index_name, table_name=table_name)
        else:
            _LOG.info(f"Index {index_name} does not exist, skipping")
    
    _LOG.info("Performance indexes removed successfully")
