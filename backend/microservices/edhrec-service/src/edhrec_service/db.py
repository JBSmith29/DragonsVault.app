from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .config import ServiceConfig
from .models import Base

_ENGINE: Optional[Engine] = None
_SESSION_FACTORY: Optional[sessionmaker] = None


def get_engine(config: ServiceConfig) -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(
            config.database_url,
            pool_pre_ping=True,
        )
    return _ENGINE


def get_session_factory(config: ServiceConfig) -> sessionmaker:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(bind=get_engine(config), expire_on_commit=False)
    return _SESSION_FACTORY


def ensure_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)


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
