"""ASGI config for Django API service."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dvapi.settings")

application = get_asgi_application()
