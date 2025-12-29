import hashlib
import secrets

from extensions import db
from .folder_role import FolderRole
from utils.time import utcnow

class Folder(db.Model):
    __tablename__ = "folder"  # keep singular to match your existing table
    __table_args__ = (
        db.UniqueConstraint("owner_user_id", "name", name="uq_folder_owner_name"),
        db.CheckConstraint(
            "category in ('deck','collection')",
            name="ck_folder_category",
        ),
        db.UniqueConstraint("share_token_hash", name="uq_folder_share_token_hash"),
    )

    CATEGORY_DECK = "deck"
    CATEGORY_COLLECTION = "collection"

    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    category = db.Column(
        db.String(20),
        nullable=False,
        default=CATEGORY_DECK,
        server_default=db.text(f"'{CATEGORY_DECK}'"),
        index=True,
    )

    commander_oracle_id = db.Column(db.String(128), index=True, nullable=True)
    commander_name = db.Column(db.String(200), nullable=True)
    deck_tag = db.Column(db.String(120), nullable=True, index=True)
    owner = db.Column(db.String(120), nullable=True, index=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    is_proxy = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
        index=True,
    )
    notes = db.Column(db.Text, nullable=True)
    is_public = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
        index=True,
    )
    share_token_hash = db.Column(db.String(64), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)
    archived_at = db.Column(db.DateTime, nullable=True, index=True)

    # Relationship to Card
    cards = db.relationship(
        "Card",
        back_populates="folder",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    owner_user = db.relationship("User", back_populates="folders")
    shares = db.relationship(
        "FolderShare",
        back_populates="folder",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    bracket_cache = db.relationship(
        "CommanderBracketCache",
        back_populates="folder",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    role_entries = db.relationship(
        "FolderRole",
        back_populates="folder",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    deck_stats = db.relationship(
        "DeckStats",
        back_populates="folder",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    @property
    def role_names(self) -> set[str]:
        return {entry.role for entry in (self.role_entries or []) if entry.role}

    def has_role(self, role: str | None) -> bool:
        normalized = FolderRole.normalize(role)
        if not normalized:
            return False
        return normalized in self.role_names

    def has_any_role(self, roles) -> bool:
        role_set = {FolderRole.normalize(r) for r in (roles or []) if FolderRole.normalize(r)}
        if not role_set:
            return False
        return bool(self.role_names & role_set)

    def set_primary_role(self, role: str | None) -> None:
        normalized = FolderRole.normalize(role)
        if not normalized:
            return
        if normalized in FolderRole.PRIMARY_ROLES:
            self.category = normalized
        keep = {r for r in self.role_names if r not in FolderRole.PRIMARY_ROLES}
        keep.add(normalized)
        self.role_entries = [FolderRole(role=r) for r in sorted(keep)]

    @property
    def is_collection(self) -> bool:
        return self.has_role(FolderRole.ROLE_COLLECTION)

    @property
    def is_deck(self) -> bool:
        return self.has_role(FolderRole.ROLE_DECK)

    @property
    def is_proxy_deck(self) -> bool:
        return bool(self.is_proxy)

    @staticmethod
    def _hash_share_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def ensure_share_token(self) -> str:
        token = secrets.token_urlsafe(24)
        self.share_token_hash = self._hash_share_token(token)
        # Remember for this request; not persisted.
        self._share_token_preview = token  # type: ignore[attr-defined]
        return token

    def revoke_share_token(self) -> None:
        self.share_token_hash = None

    @property
    def share_token(self) -> str | None:
        """Expose the one-time share token preview when available."""
        return getattr(self, "_share_token_preview", None)

    def __repr__(self):
        name = self.name or "Folder"
        suffix = " (proxy)" if self.is_proxy else ""
        return f"<Folder {name}{suffix}>"


class FolderShare(db.Model):
    __tablename__ = "folder_share"

    id = db.Column(db.Integer, primary_key=True)
    folder_id = db.Column(db.Integer, db.ForeignKey("folder.id", ondelete="CASCADE"), nullable=False, index=True)
    shared_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    folder = db.relationship("Folder", back_populates="shares")
    shared_user = db.relationship("User", back_populates="shared_folders")
