"""Shared auth helpers (legacy implementations)."""

from .authz import ensure_folder_access, require_admin

__all__ = [
    "ensure_folder_access",
    "require_admin",
]
