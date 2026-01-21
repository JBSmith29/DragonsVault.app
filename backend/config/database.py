from __future__ import annotations

from pathlib import Path


def default_sqlite_uri(instance_dir: Path) -> str:
    """Return a sqlite URI for the instance database path."""
    return f"sqlite:///{(instance_dir / 'database.db').as_posix()}"


def sqlalchemy_engine_options(database_uri: str) -> dict:
    """Match legacy SQLAlchemy engine options with sqlite-specific flags."""
    connect_args: dict = {}
    if "sqlite" in (database_uri or ""):
        connect_args = {"check_same_thread": False}
    return {
        "pool_pre_ping": True,
        "pool_recycle": 3600,
        "connect_args": connect_args,
    }
