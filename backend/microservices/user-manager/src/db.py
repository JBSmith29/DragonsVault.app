from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import ServiceConfig

_ENGINE: Optional[Engine] = None


def get_engine(config: ServiceConfig) -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(
            config.database_url,
            pool_pre_ping=True,
        )
    return _ENGINE


def ping_db(engine: Engine, schema: str) -> None:
    with engine.connect() as connection:
        result = connection.execute(
            text(
                "SELECT 1 FROM information_schema.schemata "
                "WHERE schema_name = :schema"
            ),
            {"schema": schema},
        ).scalar()
        if result is None:
            raise RuntimeError("schema_not_found")
