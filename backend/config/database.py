from __future__ import annotations

import os
from pathlib import Path


def default_sqlite_uri(instance_dir: Path) -> str:
    """Return a sqlite URI for the instance database path."""
    return f"sqlite:///{(instance_dir / 'database.db').as_posix()}"


def sqlalchemy_engine_options(database_uri: str) -> dict:
    """Match legacy SQLAlchemy engine options with sqlite-specific flags."""
    connect_args: dict = {}
    is_sqlite = "sqlite" in (database_uri or "")
    
    if is_sqlite:
        connect_args = {"check_same_thread": False}
    
    # Base options for all databases
    options = {
        "pool_pre_ping": True,
        "pool_recycle": 3600,
        "connect_args": connect_args,
    }
    
    # Connection pooling configuration (PostgreSQL/MySQL only)
    if not is_sqlite:
        # Pool size: number of connections to maintain
        pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
        # Max overflow: additional connections beyond pool_size
        max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))
        # Pool timeout: seconds to wait for connection from pool
        pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        
        options.update({
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": pool_timeout,
        })
    
    return options
