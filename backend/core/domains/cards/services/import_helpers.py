"""Shared helpers for CSV imports that manipulate persistent data."""

from __future__ import annotations

from typing import Dict

from flask import current_app
from sqlalchemy import func

from extensions import db
from models import Card, Folder


def purge_cards_preserve_commanders(
    *,
    owner_user_id: int | None,
    commit: bool = True,
) -> Dict[int, Dict[str, str | None]]:
    if owner_user_id is None:
        current_app.logger.warning("Import purge skipped: missing owner_user_id.")
        return {}
    folders = Folder.query.filter(Folder.owner_user_id == owner_user_id).all()
    preserved = {
        f.id: {
            "commander_oracle_id": f.commander_oracle_id,
            "commander_name": f.commander_name,
        }
        for f in folders
        if f.id is not None
    }
    folder_ids = [f.id for f in folders if f.id is not None]
    if folder_ids:
        db.session.query(Card).filter(Card.folder_id.in_(folder_ids)).delete(synchronize_session=False)
    if commit:
        db.session.commit()
    current_app.logger.info("Purged cards for user %s prior to import.", owner_user_id)
    return preserved


def restore_commander_metadata(
    preserved: Dict[int, Dict[str, str | None]],
    *,
    owner_user_id: int | None,
    commit: bool = True,
) -> None:
    if not preserved or owner_user_id is None:
        return
    changed = False
    for f in Folder.query.filter(Folder.owner_user_id == owner_user_id).all():
        meta = preserved.get(f.id)
        if not meta:
            continue
        if meta["commander_oracle_id"] and f.commander_oracle_id != meta["commander_oracle_id"]:
            f.commander_oracle_id = meta["commander_oracle_id"]
            f.commander_name = meta["commander_name"]
            changed = True
    if changed and commit:
        db.session.commit()
        current_app.logger.info("Commander metadata restored for %s folders.", len(preserved))


def delete_empty_folders(*, owner_user_id: int | None, commit: bool = True) -> int:
    if owner_user_id is None:
        current_app.logger.warning("Empty folder cleanup skipped: missing owner_user_id.")
        return 0
    empties = (
        db.session.query(Folder)
        .outerjoin(Card)
        .filter(Folder.owner_user_id == owner_user_id)
        .group_by(Folder.id)
        .having(func.count(Card.id) == 0)
        .all()
    )
    removed = 0
    for folder in empties:
        db.session.delete(folder)
        removed += 1
    if removed and commit:
        db.session.commit()
        current_app.logger.info("Removed %s empty folders.", removed)
    return removed
