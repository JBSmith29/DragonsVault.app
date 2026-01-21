"""Performance monitoring and optimization utilities."""

from __future__ import annotations

import functools
import time
from typing import Any, Callable, Dict, Optional

from flask import current_app, g, has_app_context, request


class PerformanceMonitor:
    """Monitor and track application performance metrics."""
    
    def __init__(self):
        self.metrics: Dict[str, Dict[str, Any]] = {}
        self.slow_query_threshold = 1.0  # seconds
        self.slow_request_threshold = 5.0  # seconds
    
    def start_timer(self, name: str) -> None:
        """Start a performance timer."""
        if not hasattr(g, 'perf_timers'):
            g.perf_timers = {}
        g.perf_timers[name] = time.time()
    
    def end_timer(self, name: str) -> float:
        """End a performance timer and return duration."""
        if not hasattr(g, 'perf_timers') or name not in g.perf_timers:
            return 0.0
        
        duration = time.time() - g.perf_timers[name]
        del g.perf_timers[name]
        
        # Log slow operations
        if duration > self.slow_query_threshold:
            self._log_slow_operation(name, duration)
        
        return duration
    
    def _log_slow_operation(self, operation: str, duration: float) -> None:
        """Log slow operations for optimization."""
        if has_app_context() and current_app:
            current_app.logger.warning(
                f"Slow operation detected: {operation} took {duration:.2f}s",
                extra={
                    'operation': operation,
                    'duration': duration,
                    'request_path': getattr(request, 'path', None),
                    'request_method': getattr(request, 'method', None)
                }
            )


# Global performance monitor instance
perf_monitor = PerformanceMonitor()


def monitor_performance(operation_name: Optional[str] = None):
    """Decorator to monitor function performance."""
    
    def decorator(func: Callable) -> Callable:
        name = operation_name or f"{func.__module__}.{func.__name__}"
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            perf_monitor.start_timer(name)
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                perf_monitor.end_timer(name)
        
        return wrapper
    
    return decorator


def monitor_database_query(func: Callable) -> Callable:
    """Decorator specifically for monitoring database queries."""
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        query_name = f"db_query_{func.__name__}"
        perf_monitor.start_timer(query_name)
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            duration = perf_monitor.end_timer(query_name)
            if duration > 0.5:  # Log queries taking more than 500ms
                if has_app_context() and current_app:
                    current_app.logger.info(
                        f"Database query {func.__name__} took {duration:.2f}s"
                    )
    
    return wrapper


class CacheMetrics:
    """Track cache hit/miss ratios for optimization."""
    
    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.errors = 0
    
    def record_hit(self):
        """Record a cache hit."""
        self.hits += 1
    
    def record_miss(self):
        """Record a cache miss."""
        self.misses += 1
    
    def record_error(self):
        """Record a cache error."""
        self.errors += 1
    
    @property
    def hit_ratio(self) -> float:
        """Calculate cache hit ratio."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            'hits': self.hits,
            'misses': self.misses,
            'errors': self.errors,
            'hit_ratio': self.hit_ratio,
            'total_requests': self.hits + self.misses
        }


# Global cache metrics instance
cache_metrics = CacheMetrics()


def optimized_cache_get(cache, key: str, default=None):
    """Optimized cache get with metrics tracking."""
    try:
        value = cache.get(key)
        if value is not None:
            cache_metrics.record_hit()
            return value
        else:
            cache_metrics.record_miss()
            return default
    except Exception:
        cache_metrics.record_error()
        return default


def batch_database_operations(operations: list, batch_size: int = 100):
    """Execute database operations in batches for better performance."""
    from extensions import db
    
    for i in range(0, len(operations), batch_size):
        batch = operations[i:i + batch_size]
        try:
            for operation in batch:
                operation()
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise