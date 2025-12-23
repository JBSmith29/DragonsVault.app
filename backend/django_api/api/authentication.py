"""Token authentication against the existing DragonsVault users table."""

from __future__ import annotations

import hashlib
import hmac

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import User


class ApiTokenAuthentication(BaseAuthentication):
    keyword = "bearer"

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            return None
        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != self.keyword:
            raise AuthenticationFailed("Invalid authorization header.")
        token = parts[1].strip()
        if not token:
            raise AuthenticationFailed("Empty token.")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            user = User.objects.get(api_token_hash=digest)
        except User.DoesNotExist as exc:
            raise AuthenticationFailed("Invalid token.") from exc
        if not user.api_token_hash or not hmac.compare_digest(user.api_token_hash, digest):
            raise AuthenticationFailed("Invalid token.")
        return (user, token)

    def authenticate_header(self, request) -> str:
        return "Bearer"
