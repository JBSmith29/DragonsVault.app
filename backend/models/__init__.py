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
from .build_session import BuildSession, BuildSessionCard  # type: ignore F401
from .folder import Folder, FolderShare  # type: ignore F401
from .folder_role import FolderRole  # type: ignore F401
from .deck_stats import DeckStats  # type: ignore F401
from .wishlist import WishlistItem  # type: ignore F401
from .user import User, AuditLog  # type: ignore F401
from .site_request import SiteRequest  # type: ignore F401
from .user_setting import UserSetting  # type: ignore F401
from .edhrec_cache import (  # type: ignore F401
    EdhrecCommanderCard,
    EdhrecCommanderCategoryCard,
    EdhrecCommanderTag,
    EdhrecCommanderTagCard,
    EdhrecCommanderTagCategoryCard,
    EdhrecTagCommander,
    EdhrecMetadata,
)
from .role import (  # type: ignore F401
    Role,
    SubRole,
    CardRole,
    CardSubRole,
    OracleRole,
    OracleKeywordTag,
    OracleRoleTag,
    OracleTypalTag,
    OracleDeckTag,
    OracleEvergreenTag,
    OracleCoreRoleTag,
    OracleCardRole,
    CardMechanic,
    DeckTagCoreRoleSynergy,
    DeckTagEvergreenSynergy,
    DeckTagCardSynergy,
)

__all__ = [
    "db",
    "Card",
    "CommanderBracketCache",
    "BuildSession",
    "BuildSessionCard",
    "Folder",
    "FolderShare",
    "FolderRole",
    "DeckStats",
    "WishlistItem",
    "User",
    "AuditLog",
    "SiteRequest",
    "UserSetting",
    "EdhrecCommanderCard",
    "EdhrecCommanderCategoryCard",
    "EdhrecCommanderTag",
    "EdhrecCommanderTagCard",
    "EdhrecCommanderTagCategoryCard",
    "EdhrecTagCommander",
    "EdhrecMetadata",
    "Role",
    "SubRole",
    "CardRole",
    "CardSubRole",
    "OracleRole",
    "OracleKeywordTag",
    "OracleRoleTag",
    "OracleTypalTag",
    "OracleDeckTag",
    "OracleEvergreenTag",
    "OracleCoreRoleTag",
    "OracleCardRole",
    "CardMechanic",
    "DeckTagCoreRoleSynergy",
    "DeckTagEvergreenSynergy",
    "DeckTagCardSynergy",
]
