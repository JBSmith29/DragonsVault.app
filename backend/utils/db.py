"""Database helpers for route handlers."""

from __future__ import annotations

from typing import Iterable, TypeVar, Any

from flask import abort

from extensions import db

T = TypeVar("T")


def get_or_404(model: type[T], ident: Any, *, options: Iterable[object] | None = None) -> T:
    """Load a row by primary key or abort with 404."""
    load_options = list(options) if options else None
    instance = db.session.get(model, ident, options=load_options)
    if instance is None:
        abort(404)
    return instance
