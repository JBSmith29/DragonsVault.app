from __future__ import annotations

from extensions import db
from utils.time import utcnow


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    label = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

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
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

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
    primary = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())


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


class OracleKeywordTag(db.Model):
    __tablename__ = "oracle_keyword_tags"

    id = db.Column(db.Integer, primary_key=True)
    oracle_id = db.Column(db.String(64), nullable=False, index=True)
    keyword = db.Column(db.String(128), nullable=False, index=True)
    source = db.Column(db.String(64), nullable=False, default="derived")
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("oracle_id", "keyword", "source", name="uq_oracle_keyword_tag"),
    )


class OracleRoleTag(db.Model):
    __tablename__ = "oracle_role_tags"

    id = db.Column(db.Integer, primary_key=True)
    oracle_id = db.Column(db.String(64), nullable=False, index=True)
    role = db.Column(db.String(128), nullable=False, index=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    source = db.Column(db.String(64), nullable=False, default="derived")
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("oracle_id", "role", name="uq_oracle_role_tag"),
    )


class OracleCoreRoleTag(db.Model):
    __tablename__ = "oracle_core_role_tags"

    id = db.Column(db.Integer, primary_key=True)
    oracle_id = db.Column(db.String(64), nullable=False, index=True)
    role = db.Column(db.String(128), nullable=False, index=True)
    source = db.Column(db.String(64), nullable=False, default="core-role")
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("oracle_id", "role", "source", name="uq_oracle_core_role_tag"),
    )


class OracleTypalTag(db.Model):
    __tablename__ = "oracle_typal_tags"

    id = db.Column(db.Integer, primary_key=True)
    oracle_id = db.Column(db.String(64), nullable=False, index=True)
    typal = db.Column(db.String(128), nullable=False, index=True)
    source = db.Column(db.String(64), nullable=False, default="derived")
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("oracle_id", "typal", "source", name="uq_oracle_typal_tag"),
    )


class OracleDeckTag(db.Model):
    __tablename__ = "oracle_deck_tags"

    id = db.Column(db.Integer, primary_key=True)
    oracle_id = db.Column(db.String(64), nullable=False, index=True)
    tag = db.Column(db.String(128), nullable=False, index=True)
    category = db.Column(db.String(128), nullable=True)
    source = db.Column(db.String(64), nullable=False, default="derived")
    version = db.Column(db.Integer, nullable=False, default=1)
    source_version = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("oracle_id", "tag", "source", name="uq_oracle_deck_tag"),
    )


class OracleEvergreenTag(db.Model):
    __tablename__ = "oracle_evergreen_tags"

    id = db.Column(db.Integer, primary_key=True)
    oracle_id = db.Column(db.String(64), nullable=False, index=True)
    keyword = db.Column(db.String(128), nullable=False, index=True)
    source = db.Column(db.String(64), nullable=False, default="derived")
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("oracle_id", "keyword", "source", name="uq_oracle_evergreen_tag"),
    )


class DeckTagCoreRoleSynergy(db.Model):
    __tablename__ = "deck_tag_core_role_synergies"

    id = db.Column(db.Integer, primary_key=True)
    deck_tag = db.Column(db.String(128), nullable=False, index=True)
    role = db.Column(db.String(128), nullable=False, index=True)
    weight = db.Column(db.Float, nullable=True)
    source = db.Column(db.String(64), nullable=False, default="derived")
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("deck_tag", "role", "source", name="uq_deck_tag_core_role_synergy"),
    )


class DeckTagEvergreenSynergy(db.Model):
    __tablename__ = "deck_tag_evergreen_synergies"

    id = db.Column(db.Integer, primary_key=True)
    deck_tag = db.Column(db.String(128), nullable=False, index=True)
    keyword = db.Column(db.String(128), nullable=False, index=True)
    weight = db.Column(db.Float, nullable=True)
    source = db.Column(db.String(64), nullable=False, default="derived")
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("deck_tag", "keyword", "source", name="uq_deck_tag_evergreen_synergy"),
    )


class DeckTagCardSynergy(db.Model):
    __tablename__ = "deck_tag_card_synergies"

    id = db.Column(db.Integer, primary_key=True)
    deck_tag = db.Column(db.String(128), nullable=False, index=True)
    oracle_id = db.Column(db.String(64), nullable=False, index=True)
    weight = db.Column(db.Float, nullable=True)
    source = db.Column(db.String(64), nullable=False, default="derived")
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("deck_tag", "oracle_id", "source", name="uq_deck_tag_card_synergy"),
    )
