"""Helpers for building static asset URLs."""

from __future__ import annotations

from flask import current_app, has_app_context, has_request_context, url_for


def static_url(path: str) -> str:
    """Return the absolute static asset URL, honoring STATIC_ASSET_BASE_URL when set."""
    clean_path = (path or "").lstrip("/")
    base_url = None
    if has_app_context():
        base_url = current_app.config.get("STATIC_ASSET_BASE_URL")
    if base_url:
        return f"{base_url.rstrip('/')}/{clean_path}"
    if has_request_context():
        try:
            return url_for("static", filename=clean_path)
        except Exception:
            pass
    return f"/static/{clean_path}"
