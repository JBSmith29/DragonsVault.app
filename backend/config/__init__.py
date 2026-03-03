from __future__ import annotations

import os

from .environments import BaseConfig, DevelopmentConfig, ProductionConfig, TestingConfig
from .paths import BASE_DIR, DEFAULT_INSTANCE_DIR, INSTANCE_DIR, LEGACY_INSTANCE_DIR, SECRET_KEY_VALUE


def _is_weak_secret(secret: str | None) -> bool:
    token = (secret or "").strip()
    if not token:
        return True
    weak_values = {
        "dev",
        "changeme",
        "change-me",
        "default",
        "secret",
        "please_change_me",
        "please_change_me_to_a_strong_password",
    }
    if token.lower() in weak_values:
        return True
    return len(token) < 32


def _select_config():
    env = os.getenv("FLASK_ENV")
    if env == "development":
        return DevelopmentConfig
    if env == "testing":
        return TestingConfig
    secret = SECRET_KEY_VALUE
    if _is_weak_secret(secret):
        raise RuntimeError(
            "SECRET_KEY must be a strong value (>=32 chars) via SECRET_KEY or SECRET_KEY_FILE in production."
        )
    return ProductionConfig


Config = _select_config()

__all__ = [
    "BaseConfig",
    "DevelopmentConfig",
    "ProductionConfig",
    "TestingConfig",
    "Config",
    "BASE_DIR",
    "DEFAULT_INSTANCE_DIR",
    "LEGACY_INSTANCE_DIR",
    "INSTANCE_DIR",
    "SECRET_KEY_VALUE",
]
