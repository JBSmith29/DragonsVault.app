"""Observability utilities for metrics, tracing, and monitoring.

This module provides basic observability features without requiring
external dependencies like Prometheus or OpenTelemetry initially.

NOTE: metrics live in process memory, so under gunicorn each worker keeps its
own counters and ``/observability/metrics`` reflects only the worker that
served the scrape. Responses are labelled ``scope: per-worker`` with the worker
PID to make this explicit. For fleet-wide aggregation use a multiprocess-aware
collector (e.g. prometheus_client multiprocess mode) rather than backing this
hot path with a per-request Redis write.
"""

from __future__ import annotations

import functools
import os
import time
from collections import defaultdict
from typing import Any, Callable, Optional, TypeVar

from flask import Blueprint, Flask, current_app, g, jsonify, request

F = TypeVar('F', bound=Callable[..., Any])


# In-memory metrics storage, per worker process (see module docstring).
# Replace with a Prometheus/StatsD multiprocess collector for cross-worker totals.
_metrics: dict[str, dict[str, Any]] = defaultdict(lambda: {
    "count": 0,
    "total_time": 0.0,
    "min_time": float('inf'),
    "max_time": 0.0,
    "errors": 0,
})


def track_metric(name: str, duration: float, error: bool = False) -> None:
    """Track a metric (request, query, etc.).
    
    Args:
        name: Metric name (e.g., "api.folders.get")
        duration: Duration in seconds
        error: Whether this was an error
    """
    metric = _metrics[name]
    metric["count"] += 1
    metric["total_time"] += duration
    metric["min_time"] = min(metric["min_time"], duration)
    metric["max_time"] = max(metric["max_time"], duration)
    if error:
        metric["errors"] += 1


def get_metrics() -> dict[str, dict[str, Any]]:
    """Get all tracked metrics.
    
    Returns:
        Dictionary of metrics with statistics
    """
    result = {}
    for name, data in _metrics.items():
        count = data["count"]
        result[name] = {
            "count": count,
            "avg_time": data["total_time"] / count if count > 0 else 0,
            "min_time": data["min_time"] if data["min_time"] != float('inf') else 0,
            "max_time": data["max_time"],
            "total_time": data["total_time"],
            "errors": data["errors"],
            "error_rate": data["errors"] / count if count > 0 else 0,
        }
    return result


def reset_metrics() -> None:
    """Reset all metrics."""
    _metrics.clear()


def track_time(metric_name: str) -> Callable[[F], F]:
    """Decorator to track execution time of a function.
    
    Args:
        metric_name: Name for the metric
    
    Returns:
        Decorated function
    
    Example:
        @track_time("api.folders.list")
        def list_folders():
            return Folder.query.all()
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.time()
            error = False
            try:
                return func(*args, **kwargs)
            except Exception:
                error = True
                raise
            finally:
                duration = time.time() - start
                track_metric(metric_name, duration, error)
        return wrapper  # type: ignore
    return decorator


def track_request_metrics() -> None:
    """Track metrics for the current request."""
    if not hasattr(g, 'request_start_time'):
        return
    
    duration = time.time() - g.request_start_time
    endpoint = request.endpoint or "unknown"
    method = request.method
    
    # Track by endpoint
    metric_name = f"request.{endpoint}.{method}"
    error = hasattr(g, 'request_error') and g.request_error
    track_metric(metric_name, duration, error)
    
    # Track by status code
    status = getattr(g, 'response_status', 200)
    status_metric = f"response.status.{status}"
    track_metric(status_metric, duration, error)


def create_observability_blueprint() -> Blueprint:
    """Create blueprint for observability endpoints.
    
    Returns:
        Flask Blueprint with /metrics and /stats routes
    """
    obs_bp = Blueprint("observability", __name__, url_prefix="/observability")
    
    @obs_bp.route("/metrics")
    def metrics():
        """Return application metrics in JSON format.

        Metrics are collected in process memory, so each gunicorn worker reports
        only the requests it handled. The response is labelled with the worker
        PID and a ``scope`` of ``per-worker`` so consumers do not mistake a
        single scrape for fleet-wide totals. Use a multiprocess collector
        (e.g. prometheus_client multiprocess mode) for true aggregation.
        """
        return jsonify({
            "scope": "per-worker",
            "worker_pid": os.getpid(),
            "metrics": get_metrics(),
        })
    
    @obs_bp.route("/stats")
    def stats():
        """Return application statistics."""
        from extensions import db
        from shared.circuit_breaker import get_all_circuit_breaker_stats
        
        # Database connection pool stats
        pool_stats = {}
        try:
            pool = db.engine.pool
            pool_stats = {
                "size": getattr(pool, 'size', lambda: 0)(),
                "checked_in": getattr(pool, 'checkedin', lambda: 0)(),
                "checked_out": getattr(pool, 'checkedout', lambda: 0)(),
                "overflow": getattr(pool, 'overflow', lambda: 0)(),
            }
        except Exception as exc:
            current_app.logger.debug("Failed to get pool stats: %s", exc)
        
        # Circuit breaker stats
        circuit_breakers = get_all_circuit_breaker_stats()
        
        # Request metrics
        metrics = get_metrics()
        
        return jsonify({
            "scope": "per-worker",
            "worker_pid": os.getpid(),
            "database": {
                "pool": pool_stats,
            },
            "circuit_breakers": circuit_breakers,
            "metrics": metrics,
        })
    
    @obs_bp.route("/health")
    def health():
        """Detailed health check with component status."""
        from extensions import db, cache
        from sqlalchemy import text
        
        health_status = {
            "status": "healthy",
            "components": {},
        }
        
        # Check database
        try:
            db.session.execute(text("SELECT 1"))
            db.session.commit()
            health_status["components"]["database"] = {
                "status": "healthy",
                "latency_ms": 0,  # Could track this
            }
        except Exception as exc:
            health_status["status"] = "unhealthy"
            health_status["components"]["database"] = {
                "status": "unhealthy",
                "error": str(exc),
            }
            db.session.rollback()
        
        # Check cache
        try:
            cache.set("health_check", "ok", timeout=1)
            cache.get("health_check")
            health_status["components"]["cache"] = {
                "status": "healthy",
            }
        except Exception as exc:
            health_status["components"]["cache"] = {
                "status": "degraded",
                "error": str(exc),
            }
        
        status_code = 200 if health_status["status"] == "healthy" else 503
        return jsonify(health_status), status_code
    
    return obs_bp


def register_observability(app: Flask) -> None:
    """Register observability features on the Flask app.
    
    Args:
        app: Flask application instance
    """
    # Register blueprint
    obs_bp = create_observability_blueprint()
    app.register_blueprint(obs_bp)
    
    # Add request timing
    @app.before_request
    def start_request_timer():
        g.request_start_time = time.time()
    
    @app.after_request
    def track_request(response):
        g.response_status = response.status_code
        track_request_metrics()
        return response
    
    @app.teardown_request
    def track_request_error(exc=None):
        if exc is not None:
            g.request_error = True
            track_request_metrics()


__all__ = [
    "track_metric",
    "get_metrics",
    "reset_metrics",
    "track_time",
    "create_observability_blueprint",
    "register_observability",
]
