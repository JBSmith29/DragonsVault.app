from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import MetaData

SCHEMA_NAME = os.getenv("DATABASE_SCHEMA", "price_service")

metadata = MetaData(schema=SCHEMA_NAME)


class Base(DeclarativeBase):
    metadata = metadata


class PrintPrice(Base):
    __tablename__ = "print_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scryfall_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mtgjson_uuid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    set_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    collector_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    normalized_prices: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_prices: Mapped[list | None] = mapped_column(JSON, nullable=True)
    price_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, default="mtgjson")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("scryfall_id", name="uq_print_prices_scryfall"),
    )
