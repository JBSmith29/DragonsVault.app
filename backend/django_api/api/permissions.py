"""Custom permissions for the migration API."""

from rest_framework.permissions import BasePermission


class ApiTokenRequired(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        return bool(user and getattr(user, "is_authenticated", False))
