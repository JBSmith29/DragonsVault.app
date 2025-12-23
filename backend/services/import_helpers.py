"""Shared helpers for CSV imports that manipulate persistent data."""

from __future__ import annotations

from typing import Dict

from flask import current_app
from sqlalchemy import func

from extensions import db
from models import Card, Folder


def purge_cards_preserve_commanders() -> Dict[str, Dict[str, str | None]]:
    preserved = {
        (f.name or "").lower(): {
            "commander_oracle_id": f.commander_oracle_id,
            "commander_name": f.commander_name,
        }
        for f in Folder.query.all()
    }
    db.session.query(Card).delete(synchronize_session=False)
    db.session.commit()
    current_app.logger.info("Purged all cards prior to import.")
    return preserved


def restore_commander_metadata(preserved: Dict[str, Dict[str, str | None]]) -> None:
    if not preserved:
        return
    changed = False
    for f in Folder.query.all():
        meta = preserved.get((f.name or "").lower())
        if not meta:
            continue
        if meta["commander_oracle_id"] and f.commander_oracle_id != meta["commander_oracle_id"]:
            f.commander_oracle_id = meta["commander_oracle_id"]
            f.commander_name = meta["commander_name"]
            changed = True
    if changed:
        db.session.commit()
        current_app.logger.info("Commander metadata restored for %s folders.", len(preserved))


def delete_empty_folders() -> int:
    empties = (
        db.session.query(Folder)
        .outerjoin(Card)
        .group_by(Folder.id)
        .having(func.count(Card.id) == 0)
        .all()
    )
    removed = 0
    for folder in empties:
        db.session.delete(folder)
        removed += 1
    if removed:
        db.session.commit()
        current_app.logger.info("Removed %s empty folders.", removed)
    return removed
