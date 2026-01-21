"""Shared exception types (placeholder)."""

class AppError(Exception):
    """Base exception for application-level errors."""


class ValidationError(AppError):
    """Raised when request or payload validation fails."""


class NotFoundError(AppError):
    """Raised when a requested resource cannot be located."""
