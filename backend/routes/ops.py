"""Operational endpoints for health/readiness/metrics checks."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from flask import Response, jsonify, current_app, request
from sqlalchemy import func, text

from extensions import db
from models import Card, Folder, WishlistItem
from services import scryfall_cache as sc
from services.edhrec_cache_service import edhrec_cache_snapshot
from services.edhrec_client import edhrec_service_enabled
from services.live_updates import latest_job_events
from .base import views


@views.route("/healthz", methods=["GET"])
def healthz():
    """Lightweight health probe that requires no external dependencies."""
    return jsonify(status="ok", service="DragonsVault"), 200


@views.route("/readyz", methods=["GET"])
def readyz():
    """Readiness probe that verifies database connectivity."""
    try:
        db.session.execute(text("SELECT 1"))
        db.session.commit()
    except Exception as exc:
        current_app.logger.warning("Readiness check failed: %s", exc, exc_info=True)
        db.session.rollback()
        return jsonify(status="error", reason="database"), 503
    return jsonify(status="ok"), 200


@views.route("/api/healthz", methods=["GET"])
def api_healthz():
    return healthz()


@views.route("/api/readyz", methods=["GET"])
def api_readyz():
    return readyz()


def _resolve_service_url(key: str, default: str | None) -> str | None:
    raw = (os.getenv(key) or current_app.config.get(key) or default or "").strip()
    if not raw:
        return None
    if raw.lower() in {"0", "off", "false", "none"}:
        return None
    return raw


def _service_targets() -> dict[str, str]:
    return {
        "user-manager": _resolve_service_url("USER_MANAGER_URL", "http://user-manager:5000"),
        "card-data": _resolve_service_url("CARD_DATA_URL", "http://card-data:5000"),
        "folder-service": _resolve_service_url("FOLDER_SERVICE_URL", "http://folder-service:5000"),
        "price-service": _resolve_service_url("PRICE_SERVICE_URL", "http://price-service:5000"),
        "edhrec-service": _resolve_service_url("EDHREC_SERVICE_URL", "http://edhrec-service:5000"),
        "django-api": _resolve_service_url("DJANGO_API_URL", "http://django-api:5000"),
    }


def _probe_http_service(base_url: str, *, path: str = "/readyz", timeout: float = 2.0) -> dict[str, object]:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    started = time.monotonic()
    try:
        resp = requests.get(url, timeout=timeout)
        latency_ms = int((time.monotonic() - started) * 1000)
        payload: dict[str, object] = {
            "status": "ok" if resp.ok else "error",
            "http_status": resp.status_code,
            "latency_ms": latency_ms,
        }
        try:
            body = resp.json()
            if isinstance(body, dict) and body:
                payload["detail"] = body.get("status") or body.get("service") or body
        except Exception:
            pass
        return payload
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"status": "error", "error": str(exc), "latency_ms": latency_ms}


def _db_readiness() -> dict[str, object]:
    started = time.monotonic()
    try:
        db.session.execute(text("SELECT 1"))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return {"status": "error", "error": str(exc)}
    latency_ms = int((time.monotonic() - started) * 1000)
    return {"status": "ok", "latency_ms": latency_ms}


def _redis_readiness() -> dict[str, object]:
    url = current_app.config.get("REDIS_URL") or os.getenv("REDIS_URL")
    if not url:
        return {"status": "skipped", "reason": "not_configured"}
    started = time.monotonic()
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(url)
        client.ping()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    latency_ms = int((time.monotonic() - started) * 1000)
    return {"status": "ok", "latency_ms": latency_ms}


@views.route("/ops/health", methods=["GET"])
@views.route("/api/ops/health", methods=["GET"])
def overall_health():
    raw_timeout = os.getenv("OPS_HEALTHCHECK_TIMEOUT", "2.0")
    try:
        timeout = float(raw_timeout)
    except ValueError:
        timeout = 2.0
    services: dict[str, object] = {
        "database": _db_readiness(),
        "redis": _redis_readiness(),
    }
    for name, base_url in _service_targets().items():
        if not base_url:
            services[name] = {"status": "skipped", "reason": "not_configured"}
            continue
        services[name] = _probe_http_service(base_url, path="/readyz", timeout=timeout)
    degraded = any((info or {}).get("status") == "error" for info in services.values())
    return jsonify({"status": "error" if degraded else "ok", "services": services}), (503 if degraded else 200)


def _queue_names() -> list[str]:
    raw = os.getenv("RQ_QUEUES") or current_app.config.get("RQ_QUEUES")
    if raw:
        return [name.strip() for name in raw.split(",") if name.strip()]
    return ["default"]


def _as_int(value) -> int:
    try:
        return int(value() if callable(value) else value)
    except Exception:
        return 0


def _queue_snapshot() -> dict[str, dict[str, int | str]]:
    payload: dict[str, dict[str, int | str]] = {}
    try:
        from services.task_queue import get_queue
    except Exception as exc:
        current_app.logger.debug("Queue metrics unavailable: %s", exc)
        for name in _queue_names():
            payload[name] = {"up": 0, "depth": 0, "failed": 0, "started": 0, "scheduled": 0, "deferred": 0}
        return payload

    for name in _queue_names():
        metrics = {"up": 1, "depth": 0, "failed": 0, "started": 0, "scheduled": 0, "deferred": 0}
        try:
            queue = get_queue(name)
            metrics["depth"] = _as_int(getattr(queue, "count", 0))
            metrics["failed"] = _as_int(getattr(queue, "failed_job_registry", None).count if hasattr(queue, "failed_job_registry") else 0)
            metrics["started"] = _as_int(getattr(queue, "started_job_registry", None).count if hasattr(queue, "started_job_registry") else 0)
            metrics["scheduled"] = _as_int(getattr(queue, "scheduled_job_registry", None).count if hasattr(queue, "scheduled_job_registry") else 0)
            metrics["deferred"] = _as_int(getattr(queue, "deferred_job_registry", None).count if hasattr(queue, "deferred_job_registry") else 0)
        except Exception as exc:
            current_app.logger.debug("Queue metrics unavailable for %s: %s", name, exc)
            metrics["up"] = 0
        payload[name] = metrics
    return payload


def _prometheus_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\"", "\\\"")


def _prometheus_sample(name: str, value: int, labels: dict[str, str] | None = None) -> str:
    if not labels:
        return f"{name} {value}"
    parts = [f'{key}="{_prometheus_escape(val)}"' for key, val in labels.items()]
    return f"{name}{{{','.join(parts)}}} {value}"


def _metrics_snapshot() -> dict[str, object]:
    total_cards = db.session.query(func.count(Card.id)).scalar() or 0
    total_folders = db.session.query(func.count(Folder.id)).scalar() or 0
    total_wishlist = db.session.query(func.count(WishlistItem.id)).scalar() or 0
    proxy_decks = (
        db.session.query(func.count(Folder.id))
        .filter(Folder.is_proxy.is_(True))
        .scalar()
        or 0
    )
    return {
        "cards_total": total_cards,
        "folders_total": total_folders,
        "wishlist_total": total_wishlist,
        "proxy_folders": proxy_decks,
        "queues": _queue_snapshot(),
    }


def _wants_json() -> bool:
    if (request.args.get("format") or "").lower() == "json":
        return True
    accepts = request.accept_mimetypes
    return accepts and accepts["application/json"] > accepts["text/plain"]


@views.route("/metrics", methods=["GET"])
def metrics():
    """Expose application metrics for dashboards/alerts."""
    payload = _metrics_snapshot()
    if _wants_json():
        return jsonify(payload)

    lines: list[str] = []
    lines.append("# HELP dragonsvault_cards_total Total number of cards in the collection.")
    lines.append("# TYPE dragonsvault_cards_total gauge")
    lines.append(_prometheus_sample("dragonsvault_cards_total", int(payload["cards_total"])))
    lines.append("# HELP dragonsvault_folders_total Total number of folders.")
    lines.append("# TYPE dragonsvault_folders_total gauge")
    lines.append(_prometheus_sample("dragonsvault_folders_total", int(payload["folders_total"])))
    lines.append("# HELP dragonsvault_wishlist_total Total number of wishlist entries.")
    lines.append("# TYPE dragonsvault_wishlist_total gauge")
    lines.append(_prometheus_sample("dragonsvault_wishlist_total", int(payload["wishlist_total"])))
    lines.append("# HELP dragonsvault_proxy_folders_total Total number of proxy folders.")
    lines.append("# TYPE dragonsvault_proxy_folders_total gauge")
    lines.append(_prometheus_sample("dragonsvault_proxy_folders_total", int(payload["proxy_folders"])))

    queue_stats = payload.get("queues", {}) or {}
    lines.append("# HELP dragonsvault_queue_up Queue connectivity status (1=up, 0=down).")
    lines.append("# TYPE dragonsvault_queue_up gauge")
    lines.append("# HELP dragonsvault_queue_depth Pending jobs in the queue.")
    lines.append("# TYPE dragonsvault_queue_depth gauge")
    lines.append("# HELP dragonsvault_queue_failed Failed jobs in the queue.")
    lines.append("# TYPE dragonsvault_queue_failed gauge")
    lines.append("# HELP dragonsvault_queue_started In-progress jobs in the queue.")
    lines.append("# TYPE dragonsvault_queue_started gauge")
    lines.append("# HELP dragonsvault_queue_scheduled Scheduled jobs in the queue.")
    lines.append("# TYPE dragonsvault_queue_scheduled gauge")
    lines.append("# HELP dragonsvault_queue_deferred Deferred jobs in the queue.")
    lines.append("# TYPE dragonsvault_queue_deferred gauge")
    for queue_name, metrics in queue_stats.items():
        labels = {"queue": str(queue_name)}
        lines.append(_prometheus_sample("dragonsvault_queue_up", int(metrics.get("up", 0)), labels))
        lines.append(_prometheus_sample("dragonsvault_queue_depth", int(metrics.get("depth", 0)), labels))
        lines.append(_prometheus_sample("dragonsvault_queue_failed", int(metrics.get("failed", 0)), labels))
        lines.append(_prometheus_sample("dragonsvault_queue_started", int(metrics.get("started", 0)), labels))
        lines.append(_prometheus_sample("dragonsvault_queue_scheduled", int(metrics.get("scheduled", 0)), labels))
        lines.append(_prometheus_sample("dragonsvault_queue_deferred", int(metrics.get("deferred", 0)), labels))

    body = "\n".join(lines) + "\n"
    return Response(body, content_type="text/plain; version=0.0.4; charset=utf-8")


def _iso_from_ts(ts: float | None) -> str | None:
    if not ts:
        return None
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _latest_job_event(scope: str, dataset: str | None) -> dict | None:
    events = latest_job_events(scope, dataset)
    if not events:
        return None
    for event in reversed(events):
        if event.get("type") in {"completed", "failed"}:
            return event
    return events[-1]


def _sync_status(scope: str, dataset: str | None, fallback_ts: float | None) -> dict:
    event = _latest_job_event(scope, dataset)
    if event:
        return {
            "last_sync": event.get("recorded_at"),
            "status": event.get("status") or event.get("download_status") or event.get("type"),
            "source": "job",
        }
    return {
        "last_sync": _iso_from_ts(fallback_ts),
        "status": "file" if fallback_ts else "unknown",
        "source": "file" if fallback_ts else "none",
    }


@views.route("/api/ops/maintenance", methods=["GET"])
def maintenance_status():
    data_root = Path(sc.default_cards_path()).parent
    default_cards = Path(sc.default_cards_path())
    rulings = Path(sc.rulings_bulk_path())
    spellbook = data_root / "spellbook_combos.json"

    payload = {
        "scryfall": _sync_status(
            "scryfall",
            "default_cards",
            default_cards.stat().st_mtime if default_cards.exists() else None,
        ),
        "rulings": _sync_status(
            "scryfall",
            "rulings",
            rulings.stat().st_mtime if rulings.exists() else None,
        ),
        "spellbook": _sync_status(
            "spellbook",
            "spellbook",
            spellbook.stat().st_mtime if spellbook.exists() else None,
        ),
        "fts": _sync_status("search_index", "cards", None),
    }

    if edhrec_service_enabled():
        snapshot = edhrec_cache_snapshot()
        payload["edhrec"] = {
            "last_sync": (snapshot.get("metadata") or {}).get("last_updated"),
            "status": snapshot.get("status") or "unknown",
            "source": "cache",
        }
    else:
        payload["edhrec"] = {
            "last_sync": None,
            "status": "disabled",
            "source": "none",
        }

    return jsonify(payload)
