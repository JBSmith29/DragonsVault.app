from __future__ import annotations

import logging
import os

from .environments import BaseConfig, DevelopmentConfig, ProductionConfig, TestingConfig
from .paths import BASE_DIR, DEFAULT_INSTANCE_DIR, INSTANCE_DIR, LEGACY_INSTANCE_DIR, SECRET_KEY_VALUE

_LOG = logging.getLogger(__name__)


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
    secret_is_weak = _is_weak_secret(SECRET_KEY_VALUE)
    if env == "development":
        if secret_is_weak:
            _LOG.warning(
                "SECRET_KEY is missing or weak; using development fallback. "
                "Set SECRET_KEY or SECRET_KEY_FILE to a 32+ character value before any non-dev deployment."
            )
        return DevelopmentConfig
    if env == "testing":
        if secret_is_weak:
            _LOG.warning(
                "SECRET_KEY is missing or weak in testing mode. Tests will run, "
                "but do not reuse this configuration outside of the test suite."
            )
        return TestingConfig
    if secret_is_weak:
        raise RuntimeError(
            "SECRET_KEY must be a strong value (>=32 chars) via SECRET_KEY or SECRET_KEY_FILE in production."
        )
    return ProductionConfig


Config = _select_config()


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
