from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import MetaData

SCHEMA_NAME = os.getenv("DATABASE_SCHEMA", "game_engine")

metadata = MetaData(schema=SCHEMA_NAME)


class Base(DeclarativeBase):
    metadata = metadata


class Game(Base):
    __tablename__ = "games"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    format: Mapped[str] = mapped_column(String(64), nullable=False, default="commander")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="waiting")
    rules_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class GamePlayer(Base):
    __tablename__ = "game_players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{SCHEMA_NAME}.games.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    seat_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    deck_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GameEvent(Base):
    __tablename__ = "game_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{SCHEMA_NAME}.games.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GameAction(Base):
    __tablename__ = "game_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(
        String(64), ForeignKey(f"{SCHEMA_NAME}.games.id", ondelete="CASCADE"), index=True
    )
    player_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EngineDeck(Base):
    __tablename__ = "engine_decks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folder_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_proxy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("folder_id", name="uq_engine_decks_folder"),
    )


class EngineDeckCard(Base):
    __tablename__ = "engine_deck_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deck_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA_NAME}.engine_decks.id", ondelete="CASCADE"), index=True
    )
    oracle_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    type_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    oracle_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    mana_cost: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cmc: Mapped[float | None] = mapped_column(Float, nullable=True)
    colors: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    color_identity: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    layout: Mapped[str | None] = mapped_column(String(32), nullable=True)
    card_faces: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    power: Mapped[str | None] = mapped_column(String(16), nullable=True)
    toughness: Mapped[str | None] = mapped_column(String(16), nullable=True)
    loyalty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    defense: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
