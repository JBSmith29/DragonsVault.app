"""Request parsing helpers for the collection browser."""

from __future__ import annotations

from dataclasses import dataclass

from flask import request
from flask_login import current_user

from extensions import db
from models import Folder
from shared.mtg import _collection_metadata
from shared.validation import ValidationError, parse_positive_int


@dataclass(slots=True)
class CollectionBrowserRequest:
    q_text: str
    folder_arg: str
    folder_id_int: int | None
    set_code: str
    typal: str
    foil_only: bool
    rarity: str
    role_query_text: str
    role_list: list[str]
    subrole_list: list[str]
    type_mode: str
    selected_types: list[str]
    selected_colors: list[str]
    color_mode: str
    collection_flag: bool
    show_friends: bool
    sort: str
    direction: str
    reverse: bool
    per: int
    page: int
    is_authenticated: bool
    current_user_id: int | None
    is_deck_folder: bool
    folder_is_proxy: bool
    collection_ids: list[int]
    collection_names: list[str]


def parse_collection_browser_request() -> CollectionBrowserRequest:
    collection_ids, collection_names, _collection_lower = _collection_metadata()

    q_text = (request.args.get("q") or "").strip()
    folder_arg = (request.args.get("folder") or "").strip()
    if folder_arg:
        parse_positive_int(folder_arg, field="folder id")

    set_code = (request.args.get("set") or "").strip().lower()
    typal = (request.args.get("tribe") or request.args.get("typal") or "").strip().lower()
    foil_arg = (request.args.get("foil_only") or request.args.get("foil") or "").strip().lower()
    foil_only = foil_arg in {"1", "true", "yes", "on", "y"}
    rarity = (request.args.get("rarity") or "").strip().lower()
    if rarity == "any":
        rarity = ""

    role_query_text = (request.args.get("role_q") or "").strip()
    roles_param_vals = request.args.getlist("roles")
    subroles_param_vals = request.args.getlist("subroles")
    roles_param = (request.args.get("roles") or "").strip()
    subroles_param = (request.args.get("subroles") or "").strip()
    role_list = [value.strip() for value in roles_param.split(",") if value.strip()] if roles_param else []
    subrole_list = [value.strip() for value in subroles_param.split(",") if value.strip()] if subroles_param else []
    if roles_param_vals:
        role_list.extend(
            item.strip()
            for value in roles_param_vals[1 if roles_param else 0:]
            for item in value.split(",")
            if item.strip()
        )
    if subroles_param_vals:
        subrole_list.extend(
            item.strip()
            for value in subroles_param_vals[1 if subroles_param else 0:]
            for item in value.split(",")
            if item.strip()
        )
    role_list = [value for value in role_list if value]
    subrole_list = [value for value in subrole_list if value]

    type_mode = (request.args.get("type_mode") or "contains").lower()
    raw_types_any = [value for value in request.args.getlist("type_any") if value]
    raw_types = [value for value in request.args.getlist("type") if value]
    selected_types = [
        value.lower()
        for value in ((raw_types if type_mode == "exact" else raw_types_any) or raw_types or raw_types_any)
    ]

    selected_colors = [color.lower() for color in request.args.getlist("color")]
    color_mode = (request.args.get("color_mode") or "contains").lower()

    scope = (request.args.get("scope") or "").lower()
    collection_flag = (request.args.get("collection") == "1") or (scope == "collection")
    is_authenticated = bool(current_user and getattr(current_user, "is_authenticated", False))
    show_friends_arg = (request.args.get("show_friends") or "").strip().lower()
    show_friends = show_friends_arg in {"1", "true", "yes", "on", "y"}
    if not is_authenticated:
        show_friends = False

    sort = (request.args.get("sort") or "name").lower()
    direction = (request.args.get("dir") or "asc").lower()
    reverse = direction == "desc"

    allowed_per_page = (25, 50, 100, 150, 200)
    try:
        per = int(request.args.get("per", request.args.get("per_page", request.args.get("page_size", 25))))
    except Exception:
        per = 25
    if per not in allowed_per_page:
        per = 25
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except Exception:
        page = 1

    folder_id_int = int(folder_arg) if folder_arg.isdigit() else None
    folder_obj = db.session.get(Folder, folder_id_int) if folder_id_int else None
    is_deck_folder = bool(folder_obj and not folder_obj.is_collection)
    folder_is_proxy = bool(getattr(folder_obj, "is_proxy_deck", False))
    current_user_id = current_user.id if is_authenticated else None

    return CollectionBrowserRequest(
        q_text=q_text,
        folder_arg=folder_arg,
        folder_id_int=folder_id_int,
        set_code=set_code,
        typal=typal,
        foil_only=foil_only,
        rarity=rarity,
        role_query_text=role_query_text,
        role_list=role_list,
        subrole_list=subrole_list,
        type_mode=type_mode,
        selected_types=selected_types,
        selected_colors=selected_colors,
        color_mode=color_mode,
        collection_flag=collection_flag,
        show_friends=show_friends,
        sort=sort,
        direction=direction,
        reverse=reverse,
        per=per,
        page=page,
        is_authenticated=is_authenticated,
        current_user_id=current_user_id,
        is_deck_folder=is_deck_folder,
        folder_is_proxy=folder_is_proxy,
        collection_ids=collection_ids,
        collection_names=collection_names,
    )


__all__ = [
    "CollectionBrowserRequest",
    "parse_collection_browser_request",
]
