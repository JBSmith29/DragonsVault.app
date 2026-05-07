"""Improved exception handling utilities and decorators.

This module provides consistent error handling patterns to replace
broad `except Exception:` handlers throughout the codebase.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Optional, TypeVar, Union

from flask import current_app, jsonify
from requests.exceptions import RequestException, Timeout, ConnectionError, HTTPError

# Type variable for generic function decoration
F = TypeVar('F', bound=Callable[..., Any])


class ServiceError(Exception):
    """Base exception for service-layer errors."""
    
    def __init__(self, message: str, code: str = "service_error", details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class ExternalServiceError(ServiceError):
    """Exception for external service failures (Scryfall, EDHREC, etc.)."""
    
    def __init__(self, service: str, message: str, details: Optional[dict] = None):
        super().__init__(
            message=f"{service} service error: {message}",
            code="external_service_error",
            details={"service": service, **(details or {})}
        )
        self.service = service


class ValidationError(ServiceError):
    """Exception for validation failures."""
    
    def __init__(self, message: str, field: Optional[str] = None, details: Optional[dict] = None):
        super().__init__(
            message=message,
            code="validation_error",
            details={"field": field, **(details or {})} if field else (details or {})
        )
        self.field = field


class DatabaseError(ServiceError):
    """Exception for database operation failures."""
    
    def __init__(self, message: str, operation: Optional[str] = None, details: Optional[dict] = None):
        super().__init__(
            message=message,
            code="database_error",
            details={"operation": operation, **(details or {})} if operation else (details or {})
        )
        self.operation = operation


def handle_external_api_errors(
    service_name: str,
    default_return: Any = None,
    log_level: int = logging.WARNING
) -> Callable[[F], F]:
    """Decorator to handle external API errors consistently.
    
    Args:
        service_name: Name of the external service (e.g., "Scryfall", "EDHREC")
        default_return: Value to return on error (default: None)
        log_level: Logging level for errors (default: WARNING)
    
    Returns:
        Decorated function that handles external API errors
    
    Example:
        @handle_external_api_errors("Scryfall", default_return=[])
        def fetch_cards():
            response = requests.get("https://api.scryfall.com/cards")
            return response.json()
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Timeout as exc:
                current_app.logger.log(
                    log_level,
                    "%s request timed out: %s",
                    service_name,
                    str(exc),
                    exc_info=True
                )
                return default_return
            except ConnectionError as exc:
                current_app.logger.log(
                    log_level,
                    "%s connection failed: %s",
                    service_name,
                    str(exc),
                    exc_info=True
                )
                return default_return
            except HTTPError as exc:
                current_app.logger.log(
                    log_level,
                    "%s HTTP error %s: %s",
                    service_name,
                    exc.response.status_code if exc.response else "unknown",
                    str(exc),
                    exc_info=True
                )
                return default_return
            except RequestException as exc:
                current_app.logger.log(
                    log_level,
                    "%s request failed: %s",
                    service_name,
                    str(exc),
                    exc_info=True
                )
                return default_return
            except Exception as exc:
                current_app.logger.error(
                    "%s unexpected error: %s",
                    service_name,
                    str(exc),
                    exc_info=True
                )
                return default_return
        return wrapper  # type: ignore
    return decorator


def handle_database_errors(
    operation: str,
    default_return: Any = None,
    rollback: bool = True
) -> Callable[[F], F]:
    """Decorator to handle database errors consistently.
    
    Args:
        operation: Description of the database operation (e.g., "fetch cards", "update folder")
        default_return: Value to return on error (default: None)
        rollback: Whether to rollback the session on error (default: True)
    
    Returns:
        Decorated function that handles database errors
    
    Example:
        @handle_database_errors("fetch user folders", default_return=[])
        def get_user_folders(user_id):
            return Folder.query.filter_by(owner_user_id=user_id).all()
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                from extensions import db
                
                current_app.logger.error(
                    "Database error during %s: %s",
                    operation,
                    str(exc),
                    exc_info=True,
                    extra={
                        "operation": operation,
                        "error_type": type(exc).__name__
                    }
                )
                
                if rollback:
                    try:
                        db.session.rollback()
                    except Exception as rollback_exc:
                        current_app.logger.error(
                            "Failed to rollback session: %s",
                            str(rollback_exc)
                        )
                
                return default_return
        return wrapper  # type: ignore
    return decorator


def handle_cache_errors(
    cache_key: str,
    default_return: Any = None
) -> Callable[[F], F]:
    """Decorator to handle cache errors gracefully.
    
    Args:
        cache_key: Description of what's being cached
        default_return: Value to return on error (default: None)
    
    Returns:
        Decorated function that handles cache errors
    
    Example:
        @handle_cache_errors("user folders", default_return=[])
        def get_cached_folders(user_id):
            return cache.get(f"folders:{user_id}")
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                current_app.logger.debug(
                    "Cache operation failed for %s: %s",
                    cache_key,
                    str(exc),
                    exc_info=True
                )
                return default_return
        return wrapper  # type: ignore
    return decorator


def safe_json_response(
    data: Any = None,
    error: Optional[str] = None,
    detail: Optional[str] = None,
    status: int = 200
) -> tuple[Any, int]:
    """Create a safe JSON response with consistent error handling.
    
    Args:
        data: Response data (for success responses)
        error: Error code (for error responses)
        detail: Human-readable error message
        status: HTTP status code
    
    Returns:
        Tuple of (response, status_code)
    
    Example:
        # Success response
        return safe_json_response(data={"folders": [...]})
        
        # Error response
        return safe_json_response(
            error="not_found",
            detail="Folder not found",
            status=404
        )
    """
    if error:
        payload = {"error": error}
        if detail:
            payload["detail"] = detail
        return jsonify(payload), status
    
    if data is not None:
        return jsonify({"data": data}), status
    
    return jsonify({}), status


def log_and_return_error(
    logger: logging.Logger,
    message: str,
    exc: Optional[Exception] = None,
    level: int = logging.ERROR,
    **extra: Any
) -> None:
    """Log an error with context and return None.
    
    Args:
        logger: Logger instance
        message: Error message
        exc: Optional exception to log
        level: Logging level
        **extra: Additional context to log
    
    Example:
        if not user:
            return log_and_return_error(
                logger,
                "User not found",
                user_id=user_id
            )
    """
    if exc:
        logger.log(level, message, exc_info=exc, extra=extra)
    else:
        logger.log(level, message, extra=extra)


class ErrorContext:
    """Context manager for consistent error handling.
    
    Example:
        with ErrorContext("fetch user data", logger, default_return=[]):
            return User.query.filter_by(id=user_id).all()
    """
    
    def __init__(
        self,
        operation: str,
        logger: logging.Logger,
        default_return: Any = None,
        rollback: bool = False,
        reraise: bool = False
    ):
        self.operation = operation
        self.logger = logger
        self.default_return = default_return
        self.rollback = rollback
        self.reraise = reraise
        self.exception: Optional[Exception] = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            return True
        
        self.exception = exc_val
        
        self.logger.error(
            "Error during %s: %s",
            self.operation,
            str(exc_val),
            exc_info=exc_val,
            extra={
                "operation": self.operation,
                "error_type": exc_type.__name__
            }
        )
        
        if self.rollback:
            try:
                from extensions import db
                db.session.rollback()
            except Exception as rollback_exc:
                self.logger.error(
                    "Failed to rollback session: %s",
                    str(rollback_exc)
                )
        
        if self.reraise:
            return False
        
        return True  # Suppress exception


__all__ = [
    "ServiceError",
    "ExternalServiceError",
    "ValidationError",
    "DatabaseError",
    "handle_external_api_errors",
    "handle_database_errors",
    "handle_cache_errors",
    "safe_json_response",
    "log_and_return_error",
    "ErrorContext",
]
