"""Standardized API response formatting for consistent client experience.

This module provides utilities for creating consistent JSON responses
across all API endpoints, including success responses, error responses,
and paginated responses.
"""

from __future__ import annotations

from typing import Any, Optional, Union

from flask import jsonify, Response


class APIResponse:
    """Standardized API response builder.
    
    All API responses follow this format:
    
    Success:
    {
        "success": true,
        "data": {...},
        "meta": {...}
    }
    
    Error:
    {
        "success": false,
        "error": {
            "code": "ERROR_CODE",
            "message": "Human-readable message",
            "details": {...}
        }
    }
    
    Paginated:
    {
        "success": true,
        "data": [...],
        "pagination": {
            "total": 100,
            "limit": 20,
            "offset": 0,
            "has_more": true
        }
    }
    """
    
    @staticmethod
    def success(
        data: Any = None,
        meta: Optional[dict[str, Any]] = None,
        status: int = 200
    ) -> tuple[Response, int]:
        """Create a success response.
        
        Args:
            data: Response data (can be dict, list, or any JSON-serializable type)
            meta: Optional metadata (e.g., timestamps, request_id)
            status: HTTP status code (default: 200)
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.success(
                data={"folders": [...]},
                meta={"request_id": "abc123"}
            )
        """
        payload: dict[str, Any] = {"success": True}
        
        if data is not None:
            payload["data"] = data
        
        if meta:
            payload["meta"] = meta
        
        return jsonify(payload), status
    
    @staticmethod
    def error(
        code: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
        status: int = 400
    ) -> tuple[Response, int]:
        """Create an error response.
        
        Args:
            code: Error code (e.g., "VALIDATION_ERROR", "NOT_FOUND")
            message: Human-readable error message
            details: Optional error details (e.g., field errors, stack trace)
            status: HTTP status code (default: 400)
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.error(
                code="VALIDATION_ERROR",
                message="Invalid folder ID",
                details={"field": "folder_id", "value": -1},
                status=400
            )
        """
        payload = {
            "success": False,
            "error": {
                "code": code,
                "message": message,
            }
        }
        
        if details:
            payload["error"]["details"] = details
        
        return jsonify(payload), status
    
    @staticmethod
    def paginated(
        data: list[Any],
        total: int,
        limit: int,
        offset: int,
        meta: Optional[dict[str, Any]] = None
    ) -> tuple[Response, int]:
        """Create a paginated response.
        
        Args:
            data: List of items for current page
            total: Total number of items across all pages
            limit: Number of items per page
            offset: Starting offset for current page
            meta: Optional metadata
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.paginated(
                data=cards,
                total=1000,
                limit=20,
                offset=40
            )
        """
        has_more = (offset + limit) < total
        
        payload: dict[str, Any] = {
            "success": True,
            "data": data,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "page": (offset // limit) + 1 if limit > 0 else 1,
                "total_pages": (total + limit - 1) // limit if limit > 0 else 1,
            }
        }
        
        if meta:
            payload["meta"] = meta
        
        return jsonify(payload), 200
    
    @staticmethod
    def created(
        data: Any,
        location: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None
    ) -> tuple[Response, int]:
        """Create a 201 Created response.
        
        Args:
            data: Created resource data
            location: Optional Location header value (resource URL)
            meta: Optional metadata
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.created(
                data={"id": 123, "name": "New Folder"},
                location="/api/folders/123"
            )
        """
        response, _ = APIResponse.success(data=data, meta=meta, status=201)
        
        if location:
            response.headers["Location"] = location
        
        return response, 201
    
    @staticmethod
    def no_content() -> tuple[Response, int]:
        """Create a 204 No Content response.
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            # After successful DELETE
            return APIResponse.no_content()
        """
        return jsonify({}), 204
    
    @staticmethod
    def not_found(
        resource: str = "Resource",
        resource_id: Optional[Union[int, str]] = None
    ) -> tuple[Response, int]:
        """Create a 404 Not Found response.
        
        Args:
            resource: Resource type (e.g., "Folder", "Card")
            resource_id: Optional resource identifier
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.not_found("Folder", folder_id)
        """
        message = f"{resource} not found"
        if resource_id is not None:
            message = f"{resource} with ID {resource_id} not found"
        
        return APIResponse.error(
            code="NOT_FOUND",
            message=message,
            details={"resource": resource, "id": resource_id} if resource_id else None,
            status=404
        )
    
    @staticmethod
    def unauthorized(message: str = "Authentication required") -> tuple[Response, int]:
        """Create a 401 Unauthorized response.
        
        Args:
            message: Error message
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.unauthorized("Invalid API token")
        """
        response, status = APIResponse.error(
            code="UNAUTHORIZED",
            message=message,
            status=401
        )
        response.headers["WWW-Authenticate"] = 'Bearer realm="API"'
        return response, status
    
    @staticmethod
    def forbidden(message: str = "Access denied") -> tuple[Response, int]:
        """Create a 403 Forbidden response.
        
        Args:
            message: Error message
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.forbidden("You don't have access to this folder")
        """
        return APIResponse.error(
            code="FORBIDDEN",
            message=message,
            status=403
        )
    
    @staticmethod
    def validation_error(
        message: str,
        errors: Optional[dict[str, list[str]]] = None
    ) -> tuple[Response, int]:
        """Create a 400 Bad Request response for validation errors.
        
        Args:
            message: General error message
            errors: Field-specific validation errors
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.validation_error(
                message="Invalid input",
                errors={
                    "folder_id": ["Must be a positive integer"],
                    "name": ["Required field"]
                }
            )
        """
        return APIResponse.error(
            code="VALIDATION_ERROR",
            message=message,
            details={"errors": errors} if errors else None,
            status=400
        )
    
    @staticmethod
    def rate_limited(
        retry_after: Optional[int] = None
    ) -> tuple[Response, int]:
        """Create a 429 Too Many Requests response.
        
        Args:
            retry_after: Seconds until rate limit resets
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.rate_limited(retry_after=60)
        """
        response, status = APIResponse.error(
            code="RATE_LIMITED",
            message="Too many requests",
            details={"retry_after": retry_after} if retry_after else None,
            status=429
        )
        
        if retry_after:
            response.headers["Retry-After"] = str(retry_after)
        
        return response, status
    
    @staticmethod
    def server_error(
        message: str = "Internal server error",
        error_id: Optional[str] = None
    ) -> tuple[Response, int]:
        """Create a 500 Internal Server Error response.
        
        Args:
            message: Error message (avoid exposing sensitive details)
            error_id: Optional error tracking ID
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.server_error(
                message="An unexpected error occurred",
                error_id="err_abc123"
            )
        """
        return APIResponse.error(
            code="INTERNAL_ERROR",
            message=message,
            details={"error_id": error_id} if error_id else None,
            status=500
        )
    
    @staticmethod
    def service_unavailable(
        service: Optional[str] = None,
        retry_after: Optional[int] = None
    ) -> tuple[Response, int]:
        """Create a 503 Service Unavailable response.
        
        Args:
            service: Name of unavailable service
            retry_after: Seconds until service may be available
        
        Returns:
            Tuple of (Flask Response, status code)
        
        Example:
            return APIResponse.service_unavailable(
                service="Scryfall API",
                retry_after=30
            )
        """
        message = "Service temporarily unavailable"
        if service:
            message = f"{service} is temporarily unavailable"
        
        response, status = APIResponse.error(
            code="SERVICE_UNAVAILABLE",
            message=message,
            details={"service": service} if service else None,
            status=503
        )
        
        if retry_after:
            response.headers["Retry-After"] = str(retry_after)
        
        return response, status


# Convenience functions for common responses
def success(data: Any = None, **kwargs) -> tuple[Response, int]:
    """Shorthand for APIResponse.success()."""
    return APIResponse.success(data=data, **kwargs)


def error(code: str, message: str, **kwargs) -> tuple[Response, int]:
    """Shorthand for APIResponse.error()."""
    return APIResponse.error(code=code, message=message, **kwargs)


def paginated(data: list[Any], total: int, limit: int, offset: int, **kwargs) -> tuple[Response, int]:
    """Shorthand for APIResponse.paginated()."""
    return APIResponse.paginated(data=data, total=total, limit=limit, offset=offset, **kwargs)


__all__ = [
    "APIResponse",
    "success",
    "error",
    "paginated",
]
