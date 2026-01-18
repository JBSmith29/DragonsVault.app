"""API views mirroring Flask JSON endpoints."""

from __future__ import annotations

from typing import Any

from django.db import connection
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, Lower
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import Card, Folder, FolderShare


def _serialize_folder(folder: Folder, counts: dict[str, int] | None = None) -> dict[str, Any]:
    counts = counts or {}
    return {
        "id": folder.id,
        "name": folder.name,
        "category": folder.category,
        "deck_tag": folder.deck_tag,
        "commander_name": folder.commander_name,
        "is_proxy": bool(folder.is_proxy),
        "is_public": bool(folder.is_public),
        "owner_user_id": folder.owner_user_id,
        "updated_at": folder.updated_at.isoformat() if folder.updated_at else None,
        "counts": {
            "unique": int(counts.get("unique") or 0),
            "total": int(counts.get("total") or 0),
        },
    }


def _serialize_card(card: Card) -> dict[str, Any]:
    return {
        "id": card.id,
        "name": card.name,
        "set_code": card.set_code,
        "collector_number": card.collector_number,
        "lang": card.lang,
        "quantity": card.quantity,
        "is_foil": bool(card.is_foil),
        "folder_id": card.folder_id,
        "oracle_id": card.oracle_id,
        "type_line": card.type_line,
        "rarity": card.rarity,
        "color_identity_mask": card.color_identity_mask,
    }


def _counts_for_folder_ids(folder_ids: list[int]) -> dict[int, dict[str, int]]:
    if not folder_ids:
        return {}
    rows = (
        Card.objects.filter(folder_id__in=folder_ids)
        .values("folder_id")
        .annotate(
            unique=Count("id"),
            total=Coalesce(Sum("quantity"), 0),
        )
    )
    return {
        row["folder_id"]: {"unique": int(row["unique"] or 0), "total": int(row["total"] or 0)}
        for row in rows
    }


def _user_can_access_folder(user, folder: Folder) -> bool:
    if folder.owner_user_id == user.id:
        return True
    if folder.owner_user_id is None:
        return True
    if folder.is_public:
        return True
    return FolderShare.objects.filter(folder_id=folder.id, shared_user_id=user.id).exists()


@api_view(["GET"])
@permission_classes([])
def healthz(_request):
    return Response({"status": "ok"})


@api_view(["GET"])
@permission_classes([])
def health(_request):
    return Response({"status": "ok", "service": "django-api"})


@api_view(["GET"])
@permission_classes([])
def readyz(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return Response({"status": "error", "reason": "database"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({"status": "ready", "service": "django-api"})


@api_view(["GET"])
def me(request):
    user = request.user
    return Response(
        {
            "data": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "is_admin": bool(getattr(user, "is_admin", False)),
            }
        }
    )


@api_view(["GET"])
def folders(request):
    user = request.user
    accessible = (
        Folder.objects.filter(
            Q(owner_user_id=user.id)
            | Q(owner_user__isnull=True)
            | Q(is_public=True)
            | Q(shares__shared_user_id=user.id)
        )
        .distinct()
        .order_by(Lower("name"))
    )
    folder_ids = [folder.id for folder in accessible]
    counts_map = _counts_for_folder_ids(folder_ids)
    data = [_serialize_folder(folder, counts_map.get(folder.id, {})) for folder in accessible]
    return Response({"data": data})


@api_view(["GET"])
def folder_detail(request, folder_id: int):
    try:
        folder = Folder.objects.get(id=folder_id)
    except Folder.DoesNotExist:
        return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
    if not _user_can_access_folder(request.user, folder):
        return Response({"error": "forbidden"}, status=status.HTTP_403_FORBIDDEN)
    counts = _counts_for_folder_ids([folder.id]).get(folder.id, {})
    return Response({"data": _serialize_folder(folder, counts)})


@api_view(["GET"])
def folder_cards(request, folder_id: int):
    try:
        folder = Folder.objects.get(id=folder_id)
    except Folder.DoesNotExist:
        return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
    if not _user_can_access_folder(request.user, folder):
        return Response({"error": "forbidden"}, status=status.HTTP_403_FORBIDDEN)

    try:
        limit = int(request.query_params.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    try:
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    base_query = Card.objects.filter(folder_id=folder.id).order_by(Lower("name"), "id")
    total = base_query.count()
    cards = list(base_query[offset : offset + limit])

    return Response(
        {
            "data": [_serialize_card(card) for card in cards],
            "pagination": {"total": total, "limit": limit, "offset": offset},
        }
    )
