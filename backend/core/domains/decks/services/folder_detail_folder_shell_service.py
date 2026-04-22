"""Folder shell builders for folder detail rendering."""

from __future__ import annotations

from dataclasses import dataclass

from models import Folder
from core.domains.decks.viewmodels.folder_vm import FolderOptionVM, FolderVM


@dataclass(slots=True)
class FolderDetailFolderShell:
    folder: FolderVM
    move_targets: list[FolderOptionVM]


def build_folder_detail_folder_shell(folder: Folder) -> FolderDetailFolderShell:
    category_labels = {
        Folder.CATEGORY_DECK: "Deck",
        Folder.CATEGORY_COLLECTION: "Collection",
    }
    role_label_map = {
        "deck": "Deck",
        "collection": "Binder",
        "wishlist": "Wishlist",
        "binder": "Binder",
    }
    raw_roles = set(folder.role_names) if hasattr(folder, "role_names") else set()
    if not raw_roles and folder.category:
        raw_roles.add(folder.category)
    role_labels = [
        role_label_map.get(role, role.replace("_", " ").title())
        for role in sorted(raw_roles)
        if role
    ]
    folder_vm = FolderVM(
        id=folder.id,
        name=folder.name,
        category=folder.category,
        category_label=category_labels.get(folder.category or Folder.CATEGORY_DECK, "Deck"),
        owner=folder.owner,
        owner_label=folder.owner,
        owner_user_id=folder.owner_user_id,
        is_collection=bool(folder.is_collection),
        is_deck=bool(folder.is_deck),
        is_proxy=bool(getattr(folder, "is_proxy", False)),
        is_public=bool(getattr(folder, "is_public", False)),
        deck_tag=folder.deck_tag,
        deck_tag_label=folder.deck_tag,
        commander_name=folder.commander_name,
        commander_oracle_id=folder.commander_oracle_id,
        commander_slot_count=len(folder.commander_name.split("//")) if folder.commander_name else 0,
        notes=folder.notes,
        role_labels=role_labels,
    )

    move_targets = [
        FolderOptionVM(id=row.id, name=row.name)
        for row in (
            Folder.query.filter(
                Folder.owner_user_id == folder.owner_user_id,
                Folder.id != folder.id,
            ).order_by(Folder.name).all()
            if folder.owner_user_id
            else []
        )
    ]

    return FolderDetailFolderShell(folder=folder_vm, move_targets=move_targets)


__all__ = ["FolderDetailFolderShell", "build_folder_detail_folder_shell"]
