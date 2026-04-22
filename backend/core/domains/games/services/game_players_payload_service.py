"""Payload builders for game roster and pod management views."""

from __future__ import annotations

from typing import Any

from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from extensions import db

from . import game_compat_service as legacy


def _deck_label(folder) -> str:
    label = folder.name or f"Deck {folder.id}"
    if folder.commander_name:
        label = f"{label} · {folder.commander_name}"
    return label


def _player_label(player) -> str:
    return (
        player.display_name
        or (player.user.display_name if player.user else None)
        or (player.user.username if player.user else None)
        or (player.user.email if player.user else None)
        or "Player"
    )


def _current_user_label() -> str:
    return current_user.display_name or current_user.username or current_user.email or f"User {current_user.id}"


def _accessible_deck_options(
    owner_user_id: int | None = None,
    *,
    commander_only: bool = False,
) -> list[dict[str, Any]]:
    query = (
        db.session.query(
            legacy.Folder.id,
            legacy.Folder.name,
            legacy.Folder.commander_name,
            legacy.Folder.owner,
            legacy.Folder.is_proxy,
        )
        .outerjoin(legacy.FolderRole, legacy.FolderRole.folder_id == legacy.Folder.id)
        .filter(
            or_(
                legacy.FolderRole.role.in_(legacy.FolderRole.DECK_ROLES),
                legacy.Folder.category == legacy.Folder.CATEGORY_DECK,
            )
        )
    )
    if owner_user_id is not None:
        query = query.filter(legacy.Folder.owner_user_id == owner_user_id)
    if commander_only:
        query = query.filter(legacy.Folder.commander_name.isnot(None), legacy.Folder.commander_name != "")
    rows = (
        query.group_by(
            legacy.Folder.id,
            legacy.Folder.name,
            legacy.Folder.commander_name,
            legacy.Folder.owner,
            legacy.Folder.is_proxy,
        )
        .order_by(func.lower(legacy.Folder.name))
        .all()
    )
    options: list[dict[str, Any]] = []
    for row in rows:
        label = row.name or f"Deck {row.id}"
        if row.commander_name:
            label = f"{label} · {row.commander_name}"
        if row.owner:
            label = f"{label} · {row.owner}"
        if row.is_proxy:
            label = f"{label} · Proxy"
        options.append({"id": row.id, "label": label, "ref": f"folder:{row.id}"})
    return options


def accessible_deck_options(
    owner_user_id: int | None = None,
    *,
    commander_only: bool = False,
) -> list[dict[str, Any]]:
    return _accessible_deck_options(owner_user_id, commander_only=commander_only)


def _roster_players(owner_user_id: int) -> list[dict[str, Any]]:
    players = (
        legacy.GameRosterPlayer.query.options(
            selectinload(legacy.GameRosterPlayer.user),
            selectinload(legacy.GameRosterPlayer.decks),
        )
        .filter(legacy.GameRosterPlayer.owner_user_id == owner_user_id)
        .order_by(func.lower(func.coalesce(legacy.GameRosterPlayer.display_name, "")), legacy.GameRosterPlayer.id.asc())
        .all()
    )
    deck_ids = {
        deck.folder_id
        for player in players
        for deck in (player.decks or [])
        if deck.folder_id is not None
    }
    folders = legacy.Folder.query.filter(legacy.Folder.id.in_(deck_ids)).all() if deck_ids else []
    folder_map = {folder.id: folder for folder in folders}

    payloads: list[dict[str, Any]] = []
    for player in players:
        assigned_decks: list[dict[str, Any]] = []
        for assignment in player.decks or []:
            if assignment.folder_id:
                folder = folder_map.get(assignment.folder_id)
                if not folder:
                    continue
                assigned_decks.append({"ref": f"folder:{folder.id}", "label": _deck_label(folder)})
            elif assignment.deck_name:
                assigned_decks.append({"ref": f"manual:{assignment.id}", "label": assignment.deck_name})
        assigned_decks.sort(key=lambda item: item["label"].lower())
        payloads.append(
            {
                "id": player.id,
                "label": _player_label(player),
                "user_id": player.user_id,
                "deck_options": assigned_decks,
            }
        )
    return payloads


def _roster_payloads_for_owner(owner_user_id: int) -> list[dict[str, Any]]:
    roster_players = (
        legacy.GameRosterPlayer.query.options(
            selectinload(legacy.GameRosterPlayer.user),
            selectinload(legacy.GameRosterPlayer.decks),
        )
        .filter(legacy.GameRosterPlayer.owner_user_id == owner_user_id)
        .order_by(func.lower(func.coalesce(legacy.GameRosterPlayer.display_name, "")), legacy.GameRosterPlayer.id.asc())
        .all()
    )
    deck_ids = {
        deck.folder_id
        for player in roster_players
        for deck in (player.decks or [])
        if deck.folder_id is not None
    }
    folders = legacy.Folder.query.filter(legacy.Folder.id.in_(deck_ids)).all() if deck_ids else []
    folder_map = {folder.id: folder for folder in folders}
    roster_decks_map: dict[int, list[dict[str, Any]]] = {player.id: [] for player in roster_players}
    for player in roster_players:
        for deck in player.decks or []:
            if deck.folder_id:
                folder = folder_map.get(deck.folder_id)
                if not folder:
                    continue
                roster_decks_map[player.id].append(
                    {
                        "assignment_id": deck.id,
                        "deck_id": folder.id,
                        "label": _deck_label(folder),
                        "is_manual": False,
                    }
                )
            elif deck.deck_name:
                roster_decks_map[player.id].append(
                    {
                        "assignment_id": deck.id,
                        "deck_id": None,
                        "label": deck.deck_name,
                        "is_manual": True,
                    }
                )
        roster_decks_map[player.id].sort(key=lambda item: item["label"].lower())

    payloads: list[dict[str, Any]] = []
    for player in roster_players:
        payloads.append(
            {
                "id": player.id,
                "owner_user_id": owner_user_id,
                "user_id": player.user_id,
                "label": _player_label(player),
                "user_label": (player.user.username or player.user.email) if player.user else None,
                "deck_assignments": roster_decks_map.get(player.id, []),
            }
        )
    return payloads


def _pod_access_flags(pod: legacy.GamePod, user_id: int) -> tuple[bool, bool]:
    is_owner = pod.owner_user_id == user_id
    is_member = any(
        member.roster_player and member.roster_player.user_id == user_id
        for member in (pod.members or [])
    )
    return is_owner, is_member


def _pod_payloads_for_owner(owner_user_id: int, roster_players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roster_label_map = {player["id"]: player["label"] for player in roster_players}
    pods = (
        legacy.GamePod.query.options(
            selectinload(legacy.GamePod.members)
            .selectinload(legacy.GamePodMember.roster_player)
            .selectinload(legacy.GameRosterPlayer.user)
        )
        .filter(legacy.GamePod.owner_user_id == owner_user_id)
        .order_by(func.lower(legacy.GamePod.name), legacy.GamePod.id.asc())
        .all()
    )
    payloads: list[dict[str, Any]] = []
    for pod in pods:
        members: list[dict[str, Any]] = []
        member_ids: list[int] = []
        for member in pod.members or []:
            roster_player = member.roster_player
            if not roster_player:
                continue
            label = roster_label_map.get(roster_player.id) or _player_label(roster_player)
            members.append(
                {
                    "member_id": member.id,
                    "roster_id": roster_player.id,
                    "label": label,
                }
            )
            member_ids.append(roster_player.id)
        members.sort(key=lambda item: item["label"].lower())
        payloads.append(
            {
                "id": pod.id,
                "name": pod.name,
                "members": members,
                "member_ids": member_ids,
            }
        )
    return payloads


def _pod_payloads_for_management(
    pods: list[legacy.GamePod],
    roster_label_map_by_owner: dict[int, dict[int, str]],
    roster_options_by_owner: dict[int, list[dict[str, Any]]],
    owner_label_map: dict[int, str],
    current_user_id: int,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for pod in pods:
        roster_label_map = roster_label_map_by_owner.get(pod.owner_user_id, {})
        members: list[dict[str, Any]] = []
        member_ids: list[int] = []
        self_member_id = None
        for member in pod.members or []:
            roster_player = member.roster_player
            if not roster_player:
                continue
            label = roster_label_map.get(roster_player.id) or _player_label(roster_player)
            members.append(
                {
                    "member_id": member.id,
                    "roster_id": roster_player.id,
                    "label": label,
                }
            )
            member_ids.append(roster_player.id)
            if roster_player.user_id == current_user_id:
                self_member_id = member.id
        members.sort(key=lambda item: item["label"].lower())
        is_owner, _ = _pod_access_flags(pod, current_user_id)
        payloads.append(
            {
                "id": pod.id,
                "name": pod.name,
                "members": members,
                "member_ids": member_ids,
                "owner_user_id": pod.owner_user_id,
                "owner_label": owner_label_map.get(pod.owner_user_id) or "Unknown owner",
                "is_owner": is_owner,
                "can_manage": is_owner,
                "self_member_id": self_member_id,
                "roster_options": roster_options_by_owner.get(pod.owner_user_id, []),
            }
        )
    return payloads


def build_games_players_page_context(pods: list[legacy.GamePod]) -> dict[str, Any]:
    pod_owner_ids = {pod.owner_user_id for pod in pods}
    managed_owner_ids = {current_user.id}
    owner_label_map: dict[int, str] = {current_user.id: _current_user_label()}

    sorted_owner_ids = sorted(
        managed_owner_ids,
        key=lambda owner_id: (
            0 if owner_id == current_user.id else 1,
            (owner_label_map.get(owner_id) or "").lower(),
            owner_id,
        ),
    )
    roster_groups: list[dict[str, Any]] = []
    roster_owner_options: list[dict[str, Any]] = []
    for owner_id in sorted_owner_ids:
        owner_label = owner_label_map.get(owner_id) or f"User {owner_id}"
        roster_groups.append(
            {
                "owner_user_id": owner_id,
                "owner_label": owner_label,
                "is_owner": owner_id == current_user.id,
                "players": _roster_payloads_for_owner(owner_id),
                "deck_options": _accessible_deck_options(owner_id),
            }
        )
        roster_owner_options.append({"id": owner_id, "label": owner_label})

    roster_label_map_by_owner: dict[int, dict[int, str]] = {}
    roster_options_by_owner: dict[int, list[dict[str, Any]]] = {}
    for owner_id in pod_owner_ids:
        owner_roster = _roster_players(owner_id)
        roster_label_map_by_owner[owner_id] = {player["id"]: player["label"] for player in owner_roster}
        roster_options_by_owner[owner_id] = [
            {"id": player["id"], "label": player["label"]}
            for player in owner_roster
        ]

    if pod_owner_ids:
        for user in legacy.User.query.filter(legacy.User.id.in_(pod_owner_ids)).all():
            owner_label_map[user.id] = user.display_name or user.username or user.email or f"User {user.id}"
    pods_payloads = _pod_payloads_for_management(
        pods,
        roster_label_map_by_owner,
        roster_options_by_owner,
        owner_label_map,
        current_user.id,
    )
    return {
        "roster_groups": roster_groups,
        "roster_owner_options": roster_owner_options,
        "current_owner_id": current_user.id,
        "has_roster_players": any(group.get("players") for group in roster_groups),
        "pods": pods_payloads,
    }


__all__ = [
    "_accessible_deck_options",
    "_pod_access_flags",
    "_pod_payloads_for_management",
    "_pod_payloads_for_owner",
    "_roster_payloads_for_owner",
    "_roster_players",
    "accessible_deck_options",
    "build_games_players_page_context",
]
