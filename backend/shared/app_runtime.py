"""Shared Flask runtime setup helpers."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, g, has_request_context, request


class RequestIdFilter(logging.Filter):
    """Inject request-scoped metadata into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if has_request_context():
            record.request_id = getattr(g, "request_id", "n/a")
            record.path = request.path
            record.method = request.method
        else:
            record.request_id = getattr(record, "request_id", "startup")
            record.path = getattr(record, "path", "")
            record.method = getattr(record, "method", "")
        return True


class JsonRequestFormatter(logging.Formatter):
    """Simple JSON formatter for logfmt-friendly ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "n/a"),
            "path": getattr(record, "path", ""),
            "method": getattr(record, "method", ""),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_request_logging(app: Flask) -> None:
    """Configure structured logging with request IDs."""
    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(RequestIdFilter())
    stream_handler.setFormatter(JsonRequestFormatter())
    stream_handler.setLevel(logging.INFO)

    handlers = [stream_handler]

    try:
        logs_dir = Path(app.instance_path) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            logs_dir / "app.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.addFilter(RequestIdFilter())
        file_handler.setFormatter(JsonRequestFormatter())
        file_handler.setLevel(logging.INFO)
        handlers.append(file_handler)
    except Exception as exc:
        app.logger.warning("Falling back to stream-only logging (file handler unavailable): %s", exc)

    app.logger.handlers = handlers
    app.logger.setLevel(logging.INFO)
    logging.getLogger("werkzeug").handlers = handlers
    logging.getLogger("werkzeug").setLevel(logging.INFO)


def csp_origin_from_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def append_csp_source(existing: object, origin: str) -> object:
    if not existing:
        return origin
    if isinstance(existing, (list, tuple, set)):
        items = list(existing)
        if origin not in items:
            items.append(origin)
        return items
    if isinstance(existing, str):
        parts = existing.split()
        if origin in parts:
            return existing
        return f"{existing} {origin}"
    return existing


def extend_csp_for_static_assets(app: Flask) -> None:
    origin = csp_origin_from_url(app.config.get("STATIC_ASSET_BASE_URL"))
    if not origin:
        return
    csp = app.config.get("CONTENT_SECURITY_POLICY")
    if not isinstance(csp, dict):
        return
    for directive in ("img-src", "script-src", "style-src", "font-src"):
        csp[directive] = append_csp_source(csp.get(directive), origin)
    app.config["CONTENT_SECURITY_POLICY"] = csp
