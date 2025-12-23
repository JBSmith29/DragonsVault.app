from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import MetaData

SCHEMA_NAME = os.getenv("DATABASE_SCHEMA", "card_data")

metadata = MetaData(schema=SCHEMA_NAME)


class Base(DeclarativeBase):
    metadata = metadata


class ScryfallBulkMeta(Base):
    __tablename__ = "scryfall_bulk_meta"

    dataset_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    download_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ScryfallOracle(Base):
    __tablename__ = "scryfall_oracles"

    oracle_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    type_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    oracle_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    mana_cost: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cmc: Mapped[float | None] = mapped_column(Float, nullable=True)
    colors: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    color_identity: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    legalities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    layout: Mapped[str | None] = mapped_column(String(32), nullable=True)
    card_faces: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    edhrec_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    power: Mapped[str | None] = mapped_column(String(16), nullable=True)
    toughness: Mapped[str | None] = mapped_column(String(16), nullable=True)
    loyalty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    defense: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scryfall_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
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


class OracleKeyword(Base):
    __tablename__ = "oracle_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    oracle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{SCHEMA_NAME}.scryfall_oracles.oracle_id", ondelete="CASCADE"),
        index=True,
    )
    keyword: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="derived")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("oracle_id", "keyword", "source", name="uq_oracle_keywords"),
    )


class OracleRole(Base):
    __tablename__ = "oracle_roles"

    oracle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{SCHEMA_NAME}.scryfall_oracles.oracle_id", ondelete="CASCADE"),
        primary_key=True,
    )
    primary_role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    roles: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    subroles: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class OracleSynergy(Base):
    __tablename__ = "oracle_synergies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    oracle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{SCHEMA_NAME}.scryfall_oracles.oracle_id", ondelete="CASCADE"),
        index=True,
    )
    related_oracle_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "oracle_id",
            "related_oracle_id",
            "source",
            name="uq_oracle_synergy_pair",
        ),
    )


class EnrichmentRun(Base):
    __tablename__ = "enrichment_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
