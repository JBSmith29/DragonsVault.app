"""Compatibility shim for folder detail views."""

from __future__ import annotations

from core.domains.decks.services.folder_detail_service import (
    folder_detail,
    shared_folder_by_token,
    shared_folder_detail,
)

__all__ = [
    "folder_detail",
    "shared_folder_by_token",
    "shared_folder_detail",
]
