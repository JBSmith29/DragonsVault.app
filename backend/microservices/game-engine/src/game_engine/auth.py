from __future__ import annotations

import hashlib
import hmac
from functools import wraps

from flask import g, jsonify, request
from sqlalchemy import text

from .config import load_config
from .db import get_engine


def _extract_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        token = header.split(None, 1)[1].strip()
        return token or None
    token = request.headers.get("X-Api-Token") or request.headers.get("X-Auth-Token")
    return token or None


def _authenticate_shared_secret() -> int | None:
    secret = request.headers.get("X-Engine-Secret") or ""
    if not secret:
        return None
    config = load_config()
    expected = config.shared_secret or ""
    if not expected:
        return None
    if not hmac.compare_digest(secret, expected):
        return None
    user_id = request.headers.get("X-User-Id") or ""
    if not str(user_id).isdigit():
        return None
    return int(user_id)


def _lookup_user_id(digest: str) -> int | None:
    config = load_config()
    engine = get_engine(config)
    schema = config.auth_schema
    table = config.auth_table
    if schema:
        table_ref = f"{schema}.{table}"
    else:
        table_ref = table
    query = text(f"SELECT id FROM {table_ref} WHERE api_token_hash = :digest LIMIT 1")
    with engine.connect() as connection:
        row = connection.execute(query, {"digest": digest}).fetchone()
        if not row:
            return None
        return int(row[0])


def authenticate_request() -> int | None:
    shared_user_id = _authenticate_shared_secret()
    if shared_user_id:
        return shared_user_id
    token = _extract_token()
    if not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return _lookup_user_id(digest)


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user_id = authenticate_request()
        if not user_id:
            return jsonify(status="error", error="unauthorized"), 401
        g.user_id = user_id
        return fn(*args, **kwargs)

    return wrapper
