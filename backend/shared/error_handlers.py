"""Centralized error handlers for HTML and JSON responses."""

from __future__ import annotations

from typing import Callable

from flask import jsonify, render_template, request


def _wants_json() -> bool:
    return request.path.startswith("/api/") or request.headers.get("HX-Request") or (
        request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]
    )


def _json_error(code: str, detail: str, status: int):
    payload = {"error": code, "detail": detail}
    return jsonify(payload), status


def register_error_handlers(app) -> Callable[[], None]:
    """Register error handlers on the Flask app."""

    @app.errorhandler(404)
    def not_found(err):  # type: ignore[no-redef]
        if _wants_json():
            return _json_error("not_found", "Resource not found.", 404)
        return render_template("shared/system/404.html", e=err), 404

    @app.errorhandler(500)
    def internal(err):  # type: ignore[no-redef]
        from extensions import db

        db.session.rollback()
        if _wants_json():
            return _json_error("server_error", "A server error occurred.", 500)
        return render_template(
            "shared/system/500.html",
            e=err,
            message="A database error occurred. Please try again.",
        ), 500

    def _noop() -> None:
        return None

    return _noop
