from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import DateTime, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import MetaData

SCHEMA_NAME = os.getenv("DATABASE_SCHEMA", "edhrec_service")

metadata = MetaData(schema=SCHEMA_NAME)


class Base(DeclarativeBase):
    metadata = metadata


class EdhrecCommanderCache(Base):
    __tablename__ = "edhrec_commanders"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    theme_slug: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("slug", "theme_slug", name="uq_edhrec_commander_slug"),
    )


class EdhrecThemeCache(Base):
    __tablename__ = "edhrec_themes"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
