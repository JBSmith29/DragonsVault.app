"""Folder-label formatting and result assembly for list checker output."""

from __future__ import annotations

from dataclasses import dataclass

from extensions import db
from models import Folder, User
from core.domains.cards.services import list_checker_result_builder_service as builder_service


def load_owner_label_map(owner_user_ids: set[int]) -> dict[int, str]:
    owner_label_map: dict[int, str] = {}
    if not owner_user_ids:
        return owner_label_map

    owner_rows = (
        db.session.query(User.id, User.display_name, User.username, User.email)
        .filter(User.id.in_(owner_user_ids))
        .all()
    )
    for uid, display_name, username, email in owner_rows:
        label = display_name or username or email
        if label:
            owner_label_map[uid] = label
    return owner_label_map


def load_missing_folder_metadata(
    folder_meta: dict[int, dict],
    owner_label_map: dict[int, str],
    folder_ids: set[int],
) -> None:
    missing_ids = [folder_id for folder_id in folder_ids if folder_id not in folder_meta]
    if not missing_ids:
        return

    folder_rows = (
        db.session.query(
            Folder.id,
            Folder.name,
            Folder.owner_user_id,
            Folder.owner,
            User.display_name,
            User.username,
            User.email,
        )
        .outerjoin(User, User.id == Folder.owner_user_id)
        .filter(Folder.id.in_(missing_ids))
        .all()
    )
    for folder_id, name, owner_user_id, owner, display_name, username, email in folder_rows:
        if folder_id not in folder_meta:
            folder_meta[folder_id] = {
                "name": name or "",
                "owner_user_id": owner_user_id,
                "owner": owner or "",
            }
        if owner_user_id and owner_user_id not in owner_label_map:
            label = display_name or username or email
            if label:
                owner_label_map[owner_user_id] = label


@dataclass(slots=True)
class ListCheckerBreakdownFormatter:
    current_user_id: int | None
    friend_ids: set[int]
    collection_id_set: set[int]
    folder_meta: dict[int, dict]
    owner_label_map: dict[int, str]

    def folder_label(self, folder_id):
        meta = self.folder_meta.get(folder_id) or {}
        name = (meta.get("name") or "").strip()
        if not name:
            return ""
        owner_id = meta.get("owner_user_id")
        owner_label = self.owner_label_map.get(owner_id) or (meta.get("owner") or "").strip()
        if owner_id and owner_id == self.current_user_id:
            return name
        if owner_label:
            return f"{owner_label}: {name}"
        return name

    def label_for_folder(self, folder_id):
        label = self.folder_label(folder_id)
        if label:
            return label
        meta = self.folder_meta.get(folder_id) or {}
        name = (meta.get("name") or "").strip()
        if name:
            return name
        return str(folder_id) if folder_id is not None else ""

    def rank_folder(self, folder_id, label):
        lowered = (label or "").strip().lower()
        meta = self.folder_meta.get(folder_id) or {}
        owner_id = meta.get("owner_user_id")
        owner_rank = 2
        if self.current_user_id and owner_id == self.current_user_id:
            owner_rank = 0
        elif owner_id in self.friend_ids:
            owner_rank = 1
        return (
            0 if (folder_id in self.collection_id_set) else 1,
            owner_rank,
            lowered,
        )

    def format_breakdown(self, breakdown):
        items = []
        for folder_id, count in breakdown.items():
            label = self.label_for_folder(folder_id)
            if not label:
                continue
            items.append((folder_id, label, count))
        items.sort(key=lambda row: self.rank_folder(row[0], row[1]))
        return [(label, count) for _, label, count in items]

    def format_breakdown_detail(self, breakdown):
        items = []
        for folder_id, count in breakdown.items():
            label = self.label_for_folder(folder_id)
            if not label:
                continue
            meta = self.folder_meta.get(folder_id) or {}
            owner_id = meta.get("owner_user_id")
            owner_label = self.owner_label_map.get(owner_id) or (meta.get("owner") or "").strip()
            owner_rank = 2
            if self.current_user_id and owner_id == self.current_user_id:
                owner_rank = 0
            elif owner_id in self.friend_ids:
                owner_rank = 1
            items.append(
                {
                    "folder_id": folder_id,
                    "label": label,
                    "qty": count,
                    "owner_user_id": owner_id,
                    "owner_label": owner_label,
                    "owner_rank": owner_rank,
                }
            )
        items.sort(key=lambda row: self.rank_folder(row["folder_id"], row["label"]))
        return items

    def filter_breakdown_by_owner(self, breakdown, owner_ids):
        if not owner_ids:
            return {}
        filtered = {}
        for folder_id, count in breakdown.items():
            meta = self.folder_meta.get(folder_id) or {}
            owner_id = meta.get("owner_user_id")
            if owner_id in owner_ids:
                filtered[folder_id] = count
        return filtered


def build_results(
    *,
    want,
    basic_land_slugs: set[str],
    per_folder_counts,
    collection_counts,
    deck_counts,
    available_per_folder_counts,
    available_count,
    rep_card_map,
    name_to_sid,
    face_to_sid,
    name_to_meta,
    face_to_meta,
    formatter: ListCheckerBreakdownFormatter,
):
    return builder_service.build_results(
        want=want,
        basic_land_slugs=basic_land_slugs,
        per_folder_counts=per_folder_counts,
        collection_counts=collection_counts,
        deck_counts=deck_counts,
        available_per_folder_counts=available_per_folder_counts,
        available_count=available_count,
        rep_card_map=rep_card_map,
        name_to_sid=name_to_sid,
        face_to_sid=face_to_sid,
        name_to_meta=name_to_meta,
        face_to_meta=face_to_meta,
        formatter=formatter,
    )


__all__ = [
    "ListCheckerBreakdownFormatter",
    "build_results",
    "load_missing_folder_metadata",
    "load_owner_label_map",
]
