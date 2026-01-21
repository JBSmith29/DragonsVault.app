from __future__ import annotations

import os

from .environments import BaseConfig, DevelopmentConfig, ProductionConfig, TestingConfig
from .paths import BASE_DIR, DEFAULT_INSTANCE_DIR, INSTANCE_DIR, LEGACY_INSTANCE_DIR, SECRET_KEY_VALUE


def _select_config():
    env = os.getenv("FLASK_ENV")
    if env == "development":
        return DevelopmentConfig
    if env == "testing":
        return TestingConfig
    secret = SECRET_KEY_VALUE or "dev"
    if not secret or secret == "dev":
        raise RuntimeError(
            "SECRET_KEY must be provided via SECRET_KEY or SECRET_KEY_FILE in production."
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
