"""Commander game tracking models."""

from __future__ import annotations

from extensions import db
from utils.time import utcnow


class GameSession(db.Model):
    __tablename__ = "game_sessions"

    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    played_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    notes = db.Column(db.Text, nullable=True)
    winner_seat_id = db.Column(
        db.Integer,
        db.ForeignKey("game_seats.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    win_via_combo = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

    seats = db.relationship(
        "GameSeat",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="GameSeat.session_id",
    )
    decks = db.relationship(
        "GameDeck",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    seat_assignments = db.relationship(
        "GameSeatAssignment",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    winner_seat = db.relationship("GameSeat", foreign_keys=[winner_seat_id], post_update=True)


class GameSeat(db.Model):
    __tablename__ = "game_seats"
    __table_args__ = (
        db.UniqueConstraint("session_id", "seat_number", name="uq_game_seat_number"),
        db.UniqueConstraint("session_id", "turn_order", name="uq_game_seat_turn_order"),
        db.CheckConstraint("seat_number >= 1 AND seat_number <= 4", name="ck_game_seat_number"),
        db.CheckConstraint("turn_order >= 1 AND turn_order <= 4", name="ck_game_seat_turn_order"),
    )

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer,
        db.ForeignKey("game_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seat_number = db.Column(db.Integer, nullable=False)
    turn_order = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    session = db.relationship("GameSession", back_populates="seats", foreign_keys=[session_id])
    assignment = db.relationship(
        "GameSeatAssignment",
        back_populates="seat",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class GamePlayer(db.Model):
    __tablename__ = "game_players"
    __table_args__ = (
        db.CheckConstraint(
            "user_id IS NOT NULL OR display_name IS NOT NULL",
            name="ck_game_player_identity",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    display_name = db.Column(db.String(120), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    assignments = db.relationship(
        "GameSeatAssignment",
        back_populates="player",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class GameDeck(db.Model):
    __tablename__ = "game_decks"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer,
        db.ForeignKey("game_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    folder_id = db.Column(db.Integer, db.ForeignKey("folder.id", ondelete="SET NULL"), nullable=True, index=True)
    deck_name = db.Column(db.String(200), nullable=False)
    commander_name = db.Column(db.String(200), nullable=True)
    commander_oracle_id = db.Column(db.String(128), nullable=True)
    bracket_level = db.Column(db.String(16), nullable=True)
    bracket_label = db.Column(db.String(120), nullable=True)
    bracket_score = db.Column(db.Float, nullable=True)
    power_score = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    session = db.relationship("GameSession", back_populates="decks")
    assignments = db.relationship(
        "GameSeatAssignment",
        back_populates="deck",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class GameSeatAssignment(db.Model):
    __tablename__ = "game_seat_assignments"
    __table_args__ = (
        db.UniqueConstraint("seat_id", name="uq_game_seat_assignment_seat"),
    )

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer,
        db.ForeignKey("game_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seat_id = db.Column(db.Integer, db.ForeignKey("game_seats.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = db.Column(db.Integer, db.ForeignKey("game_players.id", ondelete="SET NULL"), nullable=True, index=True)
    deck_id = db.Column(db.Integer, db.ForeignKey("game_decks.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    session = db.relationship("GameSession", back_populates="seat_assignments")
    seat = db.relationship("GameSeat", back_populates="assignment")
    player = db.relationship("GamePlayer", back_populates="assignments")
    deck = db.relationship("GameDeck", back_populates="assignments")


class GameRosterPlayer(db.Model):
    __tablename__ = "game_roster_players"
    __table_args__ = (
        db.UniqueConstraint("owner_user_id", "user_id", name="uq_game_roster_user"),
        db.UniqueConstraint("owner_user_id", "display_name", name="uq_game_roster_display_name"),
        db.CheckConstraint(
            "user_id IS NOT NULL OR display_name IS NOT NULL",
            name="ck_game_roster_identity",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    display_name = db.Column(db.String(120), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

    owner_user = db.relationship("User", foreign_keys=[owner_user_id])
    user = db.relationship("User", foreign_keys=[user_id])
    decks = db.relationship(
        "GameRosterDeck",
        back_populates="player",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class GamePod(db.Model):
    __tablename__ = "game_pods"
    __table_args__ = (
        db.UniqueConstraint("owner_user_id", "name", name="uq_game_pod_owner_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(120), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

    members = db.relationship(
        "GamePodMember",
        back_populates="pod",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class GamePodMember(db.Model):
    __tablename__ = "game_pod_members"
    __table_args__ = (
        db.UniqueConstraint("pod_id", "roster_player_id", name="uq_game_pod_member"),
    )

    id = db.Column(db.Integer, primary_key=True)
    pod_id = db.Column(
        db.Integer,
        db.ForeignKey("game_pods.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    roster_player_id = db.Column(
        db.Integer,
        db.ForeignKey("game_roster_players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    pod = db.relationship("GamePod", back_populates="members")
    roster_player = db.relationship("GameRosterPlayer")


class GameRosterDeck(db.Model):
    __tablename__ = "game_roster_decks"
    __table_args__ = (
        db.UniqueConstraint("roster_player_id", "folder_id", name="uq_game_roster_deck"),
    )

    id = db.Column(db.Integer, primary_key=True)
    roster_player_id = db.Column(
        db.Integer,
        db.ForeignKey("game_roster_players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    folder_id = db.Column(db.Integer, db.ForeignKey("folder.id", ondelete="SET NULL"), nullable=True, index=True)
    deck_name = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    player = db.relationship("GameRosterPlayer", back_populates="decks")
    owner_user = db.relationship("User", foreign_keys=[owner_user_id])
