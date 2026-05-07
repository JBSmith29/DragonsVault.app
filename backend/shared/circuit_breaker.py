"""Circuit breaker pattern implementation for resilient service calls.

This module provides a circuit breaker to prevent cascading failures when
external services are unavailable or slow.
"""

from __future__ import annotations

import functools
import logging
import time
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

from flask import current_app

F = TypeVar('F', bound=Callable[..., Any])


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Circuit is open, requests fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """Circuit breaker for external service calls.
    
    The circuit breaker prevents cascading failures by:
    1. Tracking failures for a service
    2. Opening the circuit after threshold failures
    3. Failing fast while circuit is open
    4. Periodically testing if service recovered
    
    States:
    - CLOSED: Normal operation, all requests pass through
    - OPEN: Too many failures, requests fail immediately
    - HALF_OPEN: Testing recovery, limited requests allowed
    
    Example:
        breaker = CircuitBreaker("scryfall", failure_threshold=5, timeout=60)
        
        @breaker.call
        def fetch_card():
            return requests.get("https://api.scryfall.com/cards/123")
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        timeout: int = 60,
        expected_exception: type[Exception] = Exception
    ):
        """Initialize circuit breaker.
        
        Args:
            name: Service name for logging
            failure_threshold: Number of failures before opening circuit
            timeout: Seconds to wait before attempting recovery (half-open)
            expected_exception: Exception type to catch (default: Exception)
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.expected_exception = expected_exception
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._last_success_time: Optional[float] = None
        
        self.logger = logging.getLogger(f"{__name__}.{name}")
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state
    
    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self._state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open (failing fast)."""
        return self._state == CircuitState.OPEN
    
    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)."""
        return self._state == CircuitState.HALF_OPEN
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if self._last_failure_time is None:
            return False
        return time.time() - self._last_failure_time >= self.timeout
    
    def _record_success(self) -> None:
        """Record successful call."""
        self._failure_count = 0
        self._last_success_time = time.time()
        
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            self.logger.info(
                "Circuit breaker for %s closed after successful recovery",
                self.name
            )
    
    def _record_failure(self) -> None:
        """Record failed call."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self._state == CircuitState.HALF_OPEN:
            # Failed during recovery, reopen circuit
            self._state = CircuitState.OPEN
            self.logger.warning(
                "Circuit breaker for %s reopened after failed recovery attempt",
                self.name
            )
        elif self._failure_count >= self.failure_threshold:
            # Too many failures, open circuit
            self._state = CircuitState.OPEN
            self.logger.error(
                "Circuit breaker for %s opened after %d failures",
                self.name,
                self._failure_count
            )
    
    def call(self, func: F) -> F:
        """Decorator to wrap function with circuit breaker.
        
        Args:
            func: Function to wrap
        
        Returns:
            Wrapped function
        
        Example:
            breaker = CircuitBreaker("api")
            
            @breaker.call
            def fetch_data():
                return requests.get("https://api.example.com/data")
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Check if circuit is open
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    # Try recovery
                    self._state = CircuitState.HALF_OPEN
                    self.logger.info(
                        "Circuit breaker for %s entering half-open state",
                        self.name
                    )
                else:
                    # Fail fast
                    raise CircuitBreakerError(
                        f"Circuit breaker for {self.name} is open"
                    )
            
            # Attempt call
            try:
                result = func(*args, **kwargs)
                self._record_success()
                return result
            except self.expected_exception as exc:
                self._record_failure()
                raise
        
        return wrapper  # type: ignore
    
    def reset(self) -> None:
        """Manually reset circuit breaker to closed state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = None
        self.logger.info("Circuit breaker for %s manually reset", self.name)
    
    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics.
        
        Returns:
            Dictionary with state, failure count, and timestamps
        """
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "timeout": self.timeout,
            "last_failure_time": self._last_failure_time,
            "last_success_time": self._last_success_time,
        }


class CircuitBreakerError(Exception):
    """Exception raised when circuit breaker is open."""
    pass


# Global circuit breakers for common services
_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    timeout: int = 60,
    expected_exception: type[Exception] = Exception
) -> CircuitBreaker:
    """Get or create a circuit breaker for a service.
    
    Args:
        name: Service name
        failure_threshold: Number of failures before opening circuit
        timeout: Seconds to wait before attempting recovery
        expected_exception: Exception type to catch
    
    Returns:
        CircuitBreaker instance
    
    Example:
        breaker = get_circuit_breaker("scryfall", failure_threshold=5)
        
        @breaker.call
        def fetch_card():
            return requests.get("https://api.scryfall.com/cards/123")
    """
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            timeout=timeout,
            expected_exception=expected_exception
        )
    return _circuit_breakers[name]


def reset_all_circuit_breakers() -> None:
    """Reset all circuit breakers to closed state."""
    for breaker in _circuit_breakers.values():
        breaker.reset()


def get_all_circuit_breaker_stats() -> list[dict[str, Any]]:
    """Get statistics for all circuit breakers.
    
    Returns:
        List of circuit breaker statistics
    """
    return [breaker.get_stats() for breaker in _circuit_breakers.values()]


__all__ = [
    "CircuitBreaker",
    "CircuitBreakerError",
    "CircuitState",
    "get_circuit_breaker",
    "reset_all_circuit_breakers",
    "get_all_circuit_breaker_stats",
]
