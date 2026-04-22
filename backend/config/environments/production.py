from __future__ import annotations

from .base import BaseConfig


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = BaseConfig.SESSION_COOKIE_SECURE
    # Enforce CSRF referer check over HTTPS in production
    WTF_CSRF_SSL_STRICT = True
