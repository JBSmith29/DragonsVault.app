"""Input validation utilities for enhanced security."""

from __future__ import annotations

import re
from typing import Any, Optional

# Common validation patterns
EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{3,30}$')
CARD_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9\s\',.-]{1,200}$')
SET_CODE_PATTERN = re.compile(r'^[a-zA-Z0-9]{2,10}$')


def sanitize_string(value: Any, max_length: int = 255) -> str:
    """Sanitize and validate string input."""
    if not isinstance(value, str):
        value = str(value) if value is not None else ""
    
    # Remove null bytes and control characters
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)
    
    # Trim whitespace and limit length
    value = value.strip()[:max_length]
    
    return value


def validate_email(email: str) -> bool:
    """Validate email format."""
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_PATTERN.match(email))


def validate_username(username: str) -> bool:
    """Validate username format."""
    if not username:
        return False
    return bool(USERNAME_PATTERN.match(username))


def validate_card_name(name: str) -> bool:
    """Validate card name format."""
    if not name or len(name) > 200:
        return False
    return bool(CARD_NAME_PATTERN.match(name))


def validate_set_code(code: str) -> bool:
    """Validate set code format."""
    if not code or len(code) > 10:
        return False
    return bool(SET_CODE_PATTERN.match(code))


def validate_positive_integer(value: Any, min_val: int = 1, max_val: int = 999999) -> Optional[int]:
    """Validate and convert to positive integer."""
    try:
        int_val = int(value)
        if min_val <= int_val <= max_val:
            return int_val
    except (TypeError, ValueError):
        pass
    return None


def validate_folder_name(name: str) -> bool:
    """Validate folder name."""
    if not name or len(name) > 100:
        return False
    # Prevent path traversal
    if '..' in name or '/' in name or '\\' in name:
        return False
    return True


def sanitize_sql_like_pattern(pattern: str) -> str:
    """Sanitize LIKE pattern to prevent SQL injection."""
    if not pattern:
        return ""
    
    # Escape SQL LIKE wildcards
    pattern = pattern.replace('\\', '\\\\')
    pattern = pattern.replace('%', '\\%')
    pattern = pattern.replace('_', '\\_')
    
    return pattern