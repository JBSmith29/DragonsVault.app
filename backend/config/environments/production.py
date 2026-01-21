from __future__ import annotations

from .base import BaseConfig


class ProductionConfig(BaseConfig):
    DEBUG = False
    # Respect environment override instead of forcing secure cookies (can be re-enabled via env).
    SESSION_COOKIE_SECURE = BaseConfig.SESSION_COOKIE_SECURE
