"""Game roster and pod management route wrapper."""

from __future__ import annotations

from typing import Any

from flask import render_template, request
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from extensions import db

from . import game_compat_service as legacy
from . import game_players_action_service as action_service
from . import game_players_payload_service as payload_service

__all__ = [
    "_accessible_deck_options",
    "_pod_access_flags",
    "_pod_payloads_for_management",
    "_pod_payloads_for_owner",
    "_roster_payloads_for_owner",
    "_roster_players",
    "accessible_deck_options",
    "games_players",
]


def _accessible_deck_options(
    owner_user_id: int | None = None,
    *,
    commander_only: bool = False,
) -> list[dict[str, Any]]:
    return payload_service._accessible_deck_options(
        owner_user_id,
        commander_only=commander_only,
    )


def accessible_deck_options(
    owner_user_id: int | None = None,
    *,
    commander_only: bool = False,
) -> list[dict[str, Any]]:
    return payload_service.accessible_deck_options(
        owner_user_id,
        commander_only=commander_only,
    )


def _roster_players(owner_user_id: int) -> list[dict[str, Any]]:
    return payload_service._roster_players(owner_user_id)


def _roster_payloads_for_owner(owner_user_id: int) -> list[dict[str, Any]]:
    return payload_service._roster_payloads_for_owner(owner_user_id)


def _pod_access_flags(pod: legacy.GamePod, user_id: int) -> tuple[bool, bool]:
    return payload_service._pod_access_flags(pod, user_id)


def _pod_payloads_for_owner(owner_user_id: int, roster_players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return payload_service._pod_payloads_for_owner(owner_user_id, roster_players)


def _pod_payloads_for_management(
    pods: list[legacy.GamePod],
    roster_label_map_by_owner: dict[int, dict[int, str]],
    roster_options_by_owner: dict[int, list[dict[str, Any]]],
    owner_label_map: dict[int, str],
    current_user_id: int,
) -> list[dict[str, Any]]:
    return payload_service._pod_payloads_for_management(
        pods,
        roster_label_map_by_owner,
        roster_options_by_owner,
        owner_label_map,
        current_user_id,
    )


def _games_players_pods() -> list[legacy.GamePod]:
    member_pod_ids = [
        pod_id
        for (pod_id,) in db.session.query(legacy.GamePodMember.pod_id)
        .join(legacy.GameRosterPlayer, legacy.GameRosterPlayer.id == legacy.GamePodMember.roster_player_id)
        .filter(legacy.GameRosterPlayer.user_id == current_user.id)
        .distinct()
        .all()
    ]
    pod_filters = [legacy.GamePod.owner_user_id == current_user.id]
    if member_pod_ids:
        pod_filters.append(legacy.GamePod.id.in_(member_pod_ids))
    return (
        legacy.GamePod.query.options(
            selectinload(legacy.GamePod.members)
            .selectinload(legacy.GamePodMember.roster_player)
            .selectinload(legacy.GameRosterPlayer.user)
        )
        .filter(or_(*pod_filters))
        .order_by(func.lower(legacy.GamePod.name), legacy.GamePod.id.asc())
        .all()
    )


def games_players():
    if request.method == "POST":
        return action_service.handle_games_players_post()

    pods = _games_players_pods()
    return render_template(
        "games/players.html",
        **payload_service.build_games_players_page_context(pods),
    )
