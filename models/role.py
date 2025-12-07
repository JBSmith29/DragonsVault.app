from __future__ import annotations

from datetime import datetime

from extensions import db


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    label = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    subroles = db.relationship("SubRole", back_populates="role", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover - repr helper
        return f"<Role {self.key}>"


class SubRole(db.Model):
    __tablename__ = "sub_roles"

    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    key = db.Column(db.String(64), nullable=False)
    label = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    role = db.relationship("Role", back_populates="subroles")

    __table_args__ = (
        db.UniqueConstraint("role_id", "key", name="uq_sub_roles_role_key"),
    )

    def __repr__(self) -> str:  # pragma: no cover - repr helper
        return f"<SubRole {self.role_id}:{self.key}>"


class CardRole(db.Model):
    __tablename__ = "card_roles"

    card_id = db.Column(db.Integer, db.ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    primary = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text("0"))


class CardSubRole(db.Model):
    __tablename__ = "card_subroles"

    card_id = db.Column(db.Integer, db.ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True)
    subrole_id = db.Column(db.Integer, db.ForeignKey("sub_roles.id", ondelete="CASCADE"), primary_key=True)


class OracleRole(db.Model):
    __tablename__ = "oracle_roles"

    oracle_id = db.Column(db.String(64), primary_key=True)
    name = db.Column(db.String(255), nullable=True)
    type_line = db.Column(db.Text, nullable=True)
    primary_role = db.Column(db.String(128), nullable=True)
    roles = db.Column(db.JSON, nullable=True)
    subroles = db.Column(db.JSON, nullable=True)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OracleRole {self.oracle_id}>"
