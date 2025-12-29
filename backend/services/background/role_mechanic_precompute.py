"""Background job for precomputing oracle roles and mechanics."""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import OracleCardRole, CardMechanic
from services import scryfall_cache as sc
from services.build_deck import build_mechanic_service, build_role_service

_LOG = logging.getLogger(__name__)


def _oracle_ids() -> list[str]:
    oracle_map = getattr(sc, "_by_oracle", {}) or {}
    return [oid for oid in oracle_map.keys() if oid]


def _table_count(model) -> int:
    try:
        return int(db.session.query(func.count()).select_from(model).scalar() or 0)
    except Exception:
        return 0


def _chunked(values: Iterable[str], size: int) -> Iterable[list[str]]:
    chunk: list[str] = []
    for value in values:
        chunk.append(value)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def precompute_oracle_roles_mechanics(
    *,
    limit: int | None = None,
    chunk_size: int = 500,
) -> dict:
    """
    Precompute oracle-level roles and mechanic hooks for all cached Scryfall oracles.
    """
    try:
        if not sc.ensure_cache_loaded():
            return {"oracles_scanned": 0, "roles_added": 0, "mechanics_added": 0, "errors": 1}
    except Exception as exc:
        _LOG.error("Unable to load Scryfall cache for role/mechanic precompute: %s", exc)
        return {"oracles_scanned": 0, "roles_added": 0, "mechanics_added": 0, "errors": 1}

    build_role_service.ensure_tables()
    build_mechanic_service.ensure_tables()

    start_roles = _table_count(OracleCardRole)
    start_mechanics = _table_count(CardMechanic)

    oracle_ids = _oracle_ids()
    if limit:
        oracle_ids = oracle_ids[: max(int(limit), 0)]

    errors = 0
    scanned = 0
    for batch in _chunked(oracle_ids, max(int(chunk_size), 1)):
        scanned += len(batch)
        try:
            build_role_service.get_roles_for_oracles(batch, persist=True)
            build_mechanic_service.get_mechanics_for_oracles(batch, persist=True)
        except SQLAlchemyError as exc:
            db.session.rollback()
            errors += 1
            _LOG.warning("Role/mechanic precompute failed for batch: %s", exc)
        except Exception as exc:
            errors += 1
            _LOG.warning("Role/mechanic precompute failed for batch: %s", exc)

    end_roles = _table_count(OracleCardRole)
    end_mechanics = _table_count(CardMechanic)

    return {
        "oracles_scanned": scanned,
        "roles_added": max(end_roles - start_roles, 0),
        "mechanics_added": max(end_mechanics - start_mechanics, 0),
        "errors": errors,
    }


__all__ = ["precompute_oracle_roles_mechanics"]
