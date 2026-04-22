"""Shared database helpers (legacy implementations)."""

from .database_config import (
    DatabaseHealthCheck,
    configure_database_events,
    get_database_config,
    optimize_database_queries,
)
from .db import get_or_404, safe_commit
from .schema_bootstrap import ensure_runtime_schema_fallbacks, validate_sqlite_database

__all__ = [
    "DatabaseHealthCheck",
    "configure_database_events",
    "ensure_runtime_schema_fallbacks",
    "get_database_config",
    "get_or_404",
    "optimize_database_queries",
    "safe_commit",
    "validate_sqlite_database",
]
