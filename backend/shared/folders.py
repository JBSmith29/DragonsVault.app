"""Shared folder naming helpers."""

from __future__ import annotations

from sqlalchemy import func

from extensions import db
from models import Folder


def folder_name_exists(name: str, *, owner_user_id: int | None = None, exclude_id: int | None = None) -> bool:
    normalized = (name or "").strip().lower()
    if not normalized:
        return False
    query = Folder.query.filter(func.lower(Folder.name) == normalized)
    if owner_user_id is not None:
        query = query.filter(Folder.owner_user_id == owner_user_id)
    if exclude_id:
        query = query.filter(Folder.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def generate_unique_folder_name(base_name: str, *, owner_user_id: int | None = None, exclude_id: int | None = None) -> str:
    candidate = base_name
    suffix = 2
    while folder_name_exists(candidate, owner_user_id=owner_user_id, exclude_id=exclude_id):
        candidate = f"{base_name} ({suffix})"
        suffix += 1
    return candidate


__all__ = [
    "folder_name_exists",
    "generate_unique_folder_name",
]
