"""Shared database helpers (legacy implementations)."""

from .database_config import (
    DatabaseHealthCheck,
    configure_database_events,
    get_database_config,
    optimize_database_queries,
)
from .db import get_or_404

__all__ = [
    "DatabaseHealthCheck",
    "configure_database_events",
    "get_database_config",
    "get_or_404",
    "optimize_database_queries",
]
