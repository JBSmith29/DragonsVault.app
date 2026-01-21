"""Centralized error handling utilities."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Dict, Optional

from flask import current_app, has_app_context, request
from werkzeug.exceptions import HTTPException


logger = logging.getLogger(__name__)


class DragonsVaultError(Exception):
    """Base exception for DragonsVault application errors."""
    
    def __init__(self, message: str, code: str = "UNKNOWN_ERROR", details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class ValidationError(DragonsVaultError):
    """Raised when input validation fails."""
    
    def __init__(self, message: str, field: Optional[str] = None, value: Any = None):
        super().__init__(message, "VALIDATION_ERROR", {"field": field, "value": value})
        self.field = field
        self.value = value


class DatabaseError(DragonsVaultError):
    """Raised when database operations fail."""
    
    def __init__(self, message: str, operation: Optional[str] = None):
        super().__init__(message, "DATABASE_ERROR", {"operation": operation})
        self.operation = operation


class CacheError(DragonsVaultError):
    """Raised when cache operations fail."""
    
    def __init__(self, message: str, cache_key: Optional[str] = None):
        super().__init__(message, "CACHE_ERROR", {"cache_key": cache_key})
        self.cache_key = cache_key


def log_error(error: Exception, context: Optional[Dict[str, Any]] = None) -> None:
    """Log error with context information."""
    
    error_info = {
        "error_type": type(error).__name__,
        "error_message": str(error),
        "traceback": traceback.format_exc(),
    }
    
    if context:
        error_info["context"] = context
    
    if has_app_context():
        error_info["request_info"] = {
            "method": getattr(request, "method", None),
            "path": getattr(request, "path", None),
            "remote_addr": getattr(request, "remote_addr", None),
            "user_agent": getattr(request, "headers", {}).get("User-Agent"),
        }
    
    if isinstance(error, DragonsVaultError):
        error_info["error_code"] = error.code
        error_info["error_details"] = error.details
    
    logger.error("Application error occurred", extra=error_info)


def handle_database_error(func):
    """Decorator to handle database errors gracefully."""
    
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            log_error(e, {"function": func.__name__, "args": args, "kwargs": kwargs})
            raise DatabaseError(f"Database operation failed in {func.__name__}: {str(e)}")
    
    return wrapper


def handle_cache_error(func):
    """Decorator to handle cache errors gracefully."""
    
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            log_error(e, {"function": func.__name__, "args": args, "kwargs": kwargs})
            # Cache errors should not break the application
            logger.warning(f"Cache operation failed in {func.__name__}: {str(e)}")
            return None
    
    return wrapper


def safe_int_conversion(value: Any, default: int = 0) -> int:
    """Safely convert value to integer."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_str_conversion(value: Any, default: str = "") -> str:
    """Safely convert value to string."""
    try:
        return str(value) if value is not None else default
    except Exception:
        return default