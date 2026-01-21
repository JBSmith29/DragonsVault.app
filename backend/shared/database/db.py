"""Database helpers for route handlers."""

from __future__ import annotations

from typing import Iterable, TypeVar, Any

from flask import abort, current_app, has_app_context
from sqlalchemy import inspect
from sqlalchemy.sql.sqltypes import Integer

from extensions import db

T = TypeVar("T")


def get_or_404(model: type[T], ident: Any, *, options: Iterable[object] | None = None) -> T:
    """Load a row by primary key or abort with 404."""
    try:
        mapper = inspect(model)
        pk_cols = mapper.primary_key
    except Exception:
        pk_cols = []

    if pk_cols:
        pk_col = pk_cols[0]
        if isinstance(pk_col.type, Integer):
            try:
                ident = int(ident)
            except (TypeError, ValueError):
                if has_app_context():
                    current_app.logger.warning(
                        "Invalid id for %s: %r",
                        getattr(model, "__name__", "model"),
                        ident,
                    )
                abort(404)
            if ident <= 0:
                if has_app_context():
                    current_app.logger.warning(
                        "Invalid id for %s: %r",
                        getattr(model, "__name__", "model"),
                        ident,
                    )
                abort(404)
    load_options = list(options) if options else None
    instance = db.session.get(model, ident, options=load_options)
    if instance is None:
        abort(404)
    return instance
