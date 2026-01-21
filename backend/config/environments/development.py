from __future__ import annotations

from .base import BaseConfig


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True
