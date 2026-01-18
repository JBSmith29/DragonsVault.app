"""URL configuration for the Django API service."""

from django.urls import path

from api import views as api_views

urlpatterns = [
    path("healthz", api_views.healthz, name="healthz"),
    path("readyz", api_views.readyz, name="readyz"),
    path("api-next/health", api_views.health, name="api-health"),
    path("api-next/me", api_views.me, name="api-me"),
    path("api-next/folders", api_views.folders, name="api-folders"),
    path("api-next/folders/<int:folder_id>", api_views.folder_detail, name="api-folder-detail"),
    path("api-next/folders/<int:folder_id>/cards", api_views.folder_cards, name="api-folder-cards"),
]
