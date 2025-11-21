"""SQLAlchemy models package for DragonsVault.
Re-exports the shared `db` instance to avoid import loops and provides
convenient names for the model classes.

Usage:
    from models import db, Card, Folder, WishlistItem
"""
from __future__ import annotations

from extensions import db  # shared SQLAlchemy() instance

# Import models only after db exists to avoid circular imports
from .card import Card  # type: ignore F401
from .commander_cache import CommanderBracketCache  # type: ignore F401
from .folder import Folder, FolderShare  # type: ignore F401
from .wishlist import WishlistItem  # type: ignore F401
from .user import User, AuditLog  # type: ignore F401
from .site_request import SiteRequest  # type: ignore F401

__all__ = [
    "db",
    "Card",
    "CommanderBracketCache",
    "Folder",
    "FolderShare",
    "WishlistItem",
    "User",
    "AuditLog",
    "SiteRequest",
]
