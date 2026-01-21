"""Database helpers for the core app (legacy implementations)."""

from shared.database import (
    DatabaseHealthCheck,
    configure_database_events,
    get_database_config,
    get_or_404,
    optimize_database_queries,
)

__all__ = [
    "DatabaseHealthCheck",
    "configure_database_events",
    "get_database_config",
    "get_or_404",
    "optimize_database_queries",
]
