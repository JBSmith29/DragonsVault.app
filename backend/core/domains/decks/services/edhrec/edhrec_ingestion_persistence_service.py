"""Schema and write helpers for EDHREC ingestion jobs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import (
    EdhrecCommanderCard,
    EdhrecCommanderCategoryCard,
    EdhrecCommanderTag,
    EdhrecCommanderTagCard,
    EdhrecCommanderTagCategoryCard,
    EdhrecCommanderTypeDistribution,
    EdhrecMetadata,
    EdhrecTagCommander,
)

_LOG = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema() -> None:
    db.metadata.create_all(
        db.engine,
        tables=[
            EdhrecCommanderCard.__table__,
            EdhrecCommanderCategoryCard.__table__,
            EdhrecCommanderTag.__table__,
            EdhrecCommanderTagCard.__table__,
            EdhrecCommanderTagCategoryCard.__table__,
            EdhrecTagCommander.__table__,
            EdhrecCommanderTypeDistribution.__table__,
            EdhrecMetadata.__table__,
        ],
    )
    inspector = inspect(db.engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("edhrec_commander_cards")}
        if "synergy_rank" not in columns:
            db.session.execute(text("ALTER TABLE edhrec_commander_cards ADD COLUMN synergy_rank INTEGER"))
        if "inclusion_percent" not in columns:
            db.session.execute(text("ALTER TABLE edhrec_commander_cards ADD COLUMN inclusion_percent FLOAT"))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _LOG.warning("EDHREC schema update skipped: %s", exc)

    def _ensure_inclusion_column(table: str) -> None:
        try:
            table_columns = {col["name"] for col in inspector.get_columns(table)}
            if "inclusion_percent" not in table_columns:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN inclusion_percent FLOAT"))
                db.session.commit()
        except Exception as exc:
            db.session.rollback()
            _LOG.warning("EDHREC schema update skipped for %s: %s", table, exc)

    _ensure_inclusion_column("edhrec_commander_tag_cards")
    _ensure_inclusion_column("edhrec_commander_category_cards")
    _ensure_inclusion_column("edhrec_commander_tag_category_cards")


def set_metadata(key: str, value: str) -> None:
    if not key:
        return
    db.session.merge(EdhrecMetadata(key=key, value=value))


def source_version_label(default_source_version: str | None = None) -> str:
    if default_source_version:
        return default_source_version
    now = datetime.now(timezone.utc)
    return f"edhrec-{now.year}-{now.month:02d}"


def commander_tag_refresh_ready(commander_oracle_id: str, requested_tags: Iterable[str]) -> bool:
    commander_ready = (
        EdhrecCommanderCategoryCard.query.filter_by(
            commander_oracle_id=commander_oracle_id
        ).first()
        is not None
    )
    commander_type_ready = (
        EdhrecCommanderTypeDistribution.query.filter_by(
            commander_oracle_id=commander_oracle_id,
            tag="",
        ).first()
        is not None
    )
    if not commander_ready or not commander_type_ready:
        return False
    for tag in requested_tags:
        tag_ready = (
            EdhrecCommanderTagCategoryCard.query.filter_by(
                commander_oracle_id=commander_oracle_id,
                tag=tag,
            ).first()
            is not None
        )
        tag_type_ready = (
            EdhrecCommanderTypeDistribution.query.filter_by(
                commander_oracle_id=commander_oracle_id,
                tag=tag,
            ).first()
            is not None
        )
        if not tag_ready or not tag_type_ready:
            return False
    return True


def persist_monthly_commander_rows(
    commander_oracle_id: str,
    *,
    needs_commander: bool,
    synergy_rows: list[dict],
    category_rows: list[dict],
    tags: list[str],
    commander_type_rows: list[dict],
    tag_card_rows: dict[str, list[dict]],
    tag_category_rows: dict[str, list[dict]],
    tag_type_rows: dict[str, list[dict]],
) -> None:
    try:
        if needs_commander:
            EdhrecCommanderCard.query.filter_by(
                commander_oracle_id=commander_oracle_id
            ).delete(synchronize_session=False)
            if synergy_rows:
                db.session.bulk_insert_mappings(
                    EdhrecCommanderCard,
                    [{"commander_oracle_id": commander_oracle_id, **row} for row in synergy_rows],
                )

            EdhrecCommanderTag.query.filter_by(
                commander_oracle_id=commander_oracle_id
            ).delete(synchronize_session=False)
            if tags:
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTag,
                    [{"commander_oracle_id": commander_oracle_id, "tag": tag} for tag in tags],
                )

            if commander_type_rows:
                EdhrecCommanderTypeDistribution.query.filter_by(
                    commander_oracle_id=commander_oracle_id,
                    tag="",
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTypeDistribution,
                    [{"commander_oracle_id": commander_oracle_id, "tag": "", **row} for row in commander_type_rows],
                )

        if category_rows:
            EdhrecCommanderCategoryCard.query.filter_by(
                commander_oracle_id=commander_oracle_id
            ).delete(synchronize_session=False)
            db.session.bulk_insert_mappings(
                EdhrecCommanderCategoryCard,
                [{"commander_oracle_id": commander_oracle_id, **row} for row in category_rows],
            )

        for tag, rows in tag_card_rows.items():
            EdhrecCommanderTagCard.query.filter_by(
                commander_oracle_id=commander_oracle_id,
                tag=tag,
            ).delete(synchronize_session=False)
            db.session.bulk_insert_mappings(
                EdhrecCommanderTagCard,
                [{"commander_oracle_id": commander_oracle_id, "tag": tag, **row} for row in rows],
            )

        for tag, rows in tag_category_rows.items():
            EdhrecCommanderTagCategoryCard.query.filter_by(
                commander_oracle_id=commander_oracle_id,
                tag=tag,
            ).delete(synchronize_session=False)
            db.session.bulk_insert_mappings(
                EdhrecCommanderTagCategoryCard,
                [{"commander_oracle_id": commander_oracle_id, "tag": tag, **row} for row in rows],
            )

        for tag, rows in tag_type_rows.items():
            EdhrecCommanderTypeDistribution.query.filter_by(
                commander_oracle_id=commander_oracle_id,
                tag=tag,
            ).delete(synchronize_session=False)
            db.session.bulk_insert_mappings(
                EdhrecCommanderTypeDistribution,
                [{"commander_oracle_id": commander_oracle_id, "tag": tag, **row} for row in rows],
            )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise


def finalize_monthly_ingestion(
    missing_slugs: dict[str, dict],
    *,
    default_source_version: str | None = None,
    now_iso_fn=now_iso,
) -> None:
    try:
        EdhrecTagCommander.query.delete(synchronize_session=False)
        rows = db.session.query(EdhrecCommanderTag.tag, EdhrecCommanderTag.commander_oracle_id).all()
        if rows:
            db.session.bulk_insert_mappings(
                EdhrecTagCommander,
                [{"tag": tag, "commander_oracle_id": oracle_id} for tag, oracle_id in rows],
            )
        set_metadata("last_updated", now_iso_fn())
        set_metadata("source_version", source_version_label(default_source_version))
        set_metadata("missing_slugs", json.dumps(missing_slugs))
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise


def persist_commander_tag_refresh(
    commander_oracle_id: str,
    *,
    synergy_rows: list[dict],
    category_rows: list[dict],
    tags: list[str],
    commander_type_rows: list[dict],
    tag_card_rows: dict[str, list[dict]],
    tag_category_rows: dict[str, list[dict]],
    tag_type_rows: dict[str, list[dict]],
    default_source_version: str | None = None,
    now_iso_fn=now_iso,
) -> None:
    try:
        with db.session.no_autoflush:
            EdhrecCommanderCard.query.filter_by(
                commander_oracle_id=commander_oracle_id
            ).delete(synchronize_session=False)
            if synergy_rows:
                db.session.bulk_insert_mappings(
                    EdhrecCommanderCard,
                    [{"commander_oracle_id": commander_oracle_id, **row} for row in synergy_rows],
                )

            EdhrecCommanderCategoryCard.query.filter_by(
                commander_oracle_id=commander_oracle_id
            ).delete(synchronize_session=False)
            if category_rows:
                db.session.bulk_insert_mappings(
                    EdhrecCommanderCategoryCard,
                    [{"commander_oracle_id": commander_oracle_id, **row} for row in category_rows],
                )

            EdhrecCommanderTag.query.filter_by(
                commander_oracle_id=commander_oracle_id
            ).delete(synchronize_session=False)
            if tags:
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTag,
                    [{"commander_oracle_id": commander_oracle_id, "tag": tag} for tag in tags],
                )

            if commander_type_rows:
                EdhrecCommanderTypeDistribution.query.filter_by(
                    commander_oracle_id=commander_oracle_id,
                    tag="",
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTypeDistribution,
                    [{"commander_oracle_id": commander_oracle_id, "tag": "", **row} for row in commander_type_rows],
                )

            for tag, rows in tag_card_rows.items():
                EdhrecCommanderTagCard.query.filter_by(
                    commander_oracle_id=commander_oracle_id,
                    tag=tag,
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTagCard,
                    [{"commander_oracle_id": commander_oracle_id, "tag": tag, **row} for row in rows],
                )

            for tag, rows in tag_category_rows.items():
                EdhrecCommanderTagCategoryCard.query.filter_by(
                    commander_oracle_id=commander_oracle_id,
                    tag=tag,
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTagCategoryCard,
                    [{"commander_oracle_id": commander_oracle_id, "tag": tag, **row} for row in rows],
                )

            for tag, rows in tag_type_rows.items():
                EdhrecCommanderTypeDistribution.query.filter_by(
                    commander_oracle_id=commander_oracle_id,
                    tag=tag,
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTypeDistribution,
                    [{"commander_oracle_id": commander_oracle_id, "tag": tag, **row} for row in rows],
                )

            EdhrecTagCommander.query.filter_by(
                commander_oracle_id=commander_oracle_id
            ).delete(synchronize_session=False)
            if tags:
                db.session.bulk_insert_mappings(
                    EdhrecTagCommander,
                    [{"tag": tag, "commander_oracle_id": commander_oracle_id} for tag in tags],
                )

            set_metadata("last_updated", now_iso_fn())
            set_metadata("source_version", source_version_label(default_source_version))
            db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise


__all__ = [
    "commander_tag_refresh_ready",
    "ensure_schema",
    "finalize_monthly_ingestion",
    "now_iso",
    "persist_commander_tag_refresh",
    "persist_monthly_commander_rows",
    "set_metadata",
    "source_version_label",
]
