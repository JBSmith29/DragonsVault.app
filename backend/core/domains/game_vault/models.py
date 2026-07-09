"""Self-contained ORM models for Game Vault.

Design rules (see package docstring):
    * Every table is prefixed ``gv_``.
    * Foreign keys only ever reference other ``gv_`` tables.
    * ``owner_user_id`` is a plain indexed integer (NO ForeignKey) so the vault
      is scoped per signed-in account without coupling to the ``users`` table.
"""

from __future__ import annotations

from typing import Any

from extensions import db
from core.shared.utils.time import utcnow

# Sources we can import a decklist from.
SOURCE_ARCHIDEKT = "archidekt"
SOURCE_MOXFIELD = "moxfield"
SOURCE_GOLDFISH = "mtggoldfish"
SOURCE_MANUAL = "manual"
KNOWN_SOURCES = (SOURCE_ARCHIDEKT, SOURCE_MOXFIELD, SOURCE_GOLDFISH, SOURCE_MANUAL)

# How a game was won (free-form but constrained in the UI).
WIN_CONDITIONS = ("combat", "combo", "commander_damage", "mill", "alt_win", "other")


class GVPlayer(db.Model):
    """A person you play against (a pod regular). Not an app user account."""

    __tablename__ = "gv_players"
    __table_args__ = (
        db.UniqueConstraint("owner_user_id", "name", name="uq_gv_player_owner_name"),
        db.Index("ix_gv_players_owner", "owner_user_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    note = db.Column(db.String(255), nullable=True)
    color = db.Column(db.String(16), nullable=True)  # UI accent, e.g. "#8b5cf6"
    archived_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    decks = db.relationship(
        "GVDeck",
        back_populates="player",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="GVDeck.name",
    )

    def to_dict(self, *, include_decks: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "note": self.note or "",
            "color": self.color,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_decks:
            active = [d for d in (self.decks or []) if d.archived_at is None]
            data["decks"] = [d.to_dict() for d in active]
            data["deck_count"] = len(active)
        return data


class GVDeck(db.Model):
    """A deck imported (and re-syncable) from Archidekt/Moxfield/MTGGoldfish."""

    __tablename__ = "gv_decks"
    __table_args__ = (
        db.Index("ix_gv_decks_owner", "owner_user_id"),
        db.Index("ix_gv_decks_player", "player_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, nullable=False, index=True)
    player_id = db.Column(
        db.Integer,
        db.ForeignKey("gv_players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source = db.Column(db.String(20), nullable=False, default=SOURCE_MANUAL)
    source_id = db.Column(db.String(64), nullable=True)
    url = db.Column(db.String(500), nullable=True)

    name = db.Column(db.String(200), nullable=False)
    commander_name = db.Column(db.String(200), nullable=True)
    commander_image = db.Column(db.String(500), nullable=True)
    color_identity = db.Column(db.String(10), nullable=True)  # subset of WUBRG
    format = db.Column(db.String(32), nullable=True)
    bracket = db.Column(db.Integer, nullable=True)  # 1-5 where known
    card_count = db.Column(db.Integer, nullable=True)
    cards = db.Column(db.JSON, nullable=True)  # [{"name", "quantity"}]

    last_synced_at = db.Column(db.DateTime, nullable=True)
    sync_status = db.Column(db.String(20), nullable=True)  # "ok" | "error"
    sync_error = db.Column(db.String(255), nullable=True)
    archived_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    player = db.relationship("GVPlayer", back_populates="decks")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "player_id": self.player_id,
            "source": self.source,
            "source_id": self.source_id,
            "url": self.url,
            "name": self.name,
            "commander_name": self.commander_name,
            "commander_image": self.commander_image,
            "color_identity": self.color_identity,
            "colors": list(self.color_identity or ""),
            "format": self.format,
            "bracket": self.bracket,
            "card_count": self.card_count,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "sync_status": self.sync_status,
            "sync_error": self.sync_error,
        }


class GVGame(db.Model):
    """A single logged game."""

    __tablename__ = "gv_games"
    __table_args__ = (
        db.Index("ix_gv_games_owner_played", "owner_user_id", "played_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, nullable=False, index=True)
    played_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    format = db.Column(db.String(32), nullable=False, default="commander")
    turns = db.Column(db.Integer, nullable=True)
    duration_minutes = db.Column(db.Integer, nullable=True)
    win_condition = db.Column(db.String(20), nullable=True)
    infinite_win = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    participants = db.relationship(
        "GVGameParticipant",
        back_populates="game",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="GVGameParticipant.turn_order",
    )

    def to_dict(self) -> dict[str, Any]:
        parts = sorted(
            self.participants or [],
            key=lambda p: (p.turn_order if p.turn_order is not None else 99),
        )
        winner = next((p for p in parts if p.is_winner), None)
        return {
            "id": self.id,
            "played_at": self.played_at.isoformat() if self.played_at else None,
            "played_at_label": self.played_at.strftime("%Y-%m-%d") if self.played_at else "",
            "format": self.format,
            "turns": self.turns,
            "duration_minutes": self.duration_minutes,
            "win_condition": self.win_condition,
            "infinite_win": bool(self.infinite_win),
            "notes": self.notes or "",
            "winner_name": winner.player_name if winner else None,
            "winner_deck": winner.deck_name if winner else None,
            "participants": [p.to_dict() for p in parts],
        }


class GVGameParticipant(db.Model):
    """One seat in a logged game. Player/deck are snapshotted so history is
    stable even if the player or deck is later renamed or deleted."""

    __tablename__ = "gv_game_participants"
    __table_args__ = (
        db.Index("ix_gv_gp_game", "game_id"),
        db.Index("ix_gv_gp_player", "player_id"),
        db.Index("ix_gv_gp_deck", "deck_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(
        db.Integer,
        db.ForeignKey("gv_games.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    player_id = db.Column(
        db.Integer,
        db.ForeignKey("gv_players.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    deck_id = db.Column(
        db.Integer,
        db.ForeignKey("gv_decks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    player_name = db.Column(db.String(120), nullable=True)
    deck_name = db.Column(db.String(200), nullable=True)
    commander_name = db.Column(db.String(200), nullable=True)
    turn_order = db.Column(db.Integer, nullable=True)
    is_winner = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    game = db.relationship("GVGame", back_populates="participants")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "player_id": self.player_id,
            "deck_id": self.deck_id,
            "player_name": self.player_name,
            "deck_name": self.deck_name,
            "commander_name": self.commander_name,
            "turn_order": self.turn_order,
            "is_winner": bool(self.is_winner),
        }


__all__ = [
    "GVPlayer",
    "GVDeck",
    "GVGame",
    "GVGameParticipant",
    "SOURCE_ARCHIDEKT",
    "SOURCE_MOXFIELD",
    "SOURCE_GOLDFISH",
    "SOURCE_MANUAL",
    "KNOWN_SOURCES",
    "WIN_CONDITIONS",
]
