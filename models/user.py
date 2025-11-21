from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Optional

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text("0"))
    display_name = db.Column(db.String(120), nullable=True)
    api_token_hash = db.Column(db.String(64), nullable=True, unique=True)
    api_token_hint = db.Column(db.String(12), nullable=True)
    api_token_created_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    folders = db.relationship("Folder", back_populates="owner_user", lazy="dynamic")
    shared_folders = db.relationship("FolderShare", back_populates="shared_user", lazy="dynamic")
    audit_logs = db.relationship("AuditLog", back_populates="user", lazy="dynamic")

    def get_id(self) -> str:
        return str(self.id)

    # Password helpers -----------------------------------------------------
    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password.strip())

    def check_password(self, raw_password: str | None) -> bool:
        if not raw_password or not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw_password.strip())

    # API token helpers ----------------------------------------------------
    def issue_api_token(self) -> str:
        """
        Create a new API token, returning the plaintext value exactly once.
        The hashed token is persisted; callers must display/store the plaintext safely.
        """
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.api_token_hash = digest
        self.api_token_hint = token[-8:]
        self.api_token_created_at = datetime.utcnow()
        return token

    def clear_api_token(self) -> None:
        self.api_token_hash = None
        self.api_token_hint = None
        self.api_token_created_at = None

    @classmethod
    def verify_api_token(cls, token: str | None) -> Optional["User"]:
        if not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        candidate = cls.query.filter_by(api_token_hash=digest).first()
        if candidate and candidate.api_token_hash:
            if hmac.compare_digest(candidate.api_token_hash, digest):
                return candidate
        return None


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    action = db.Column(db.String(120), nullable=False)
    details = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    user = db.relationship("User", back_populates="audit_logs")
