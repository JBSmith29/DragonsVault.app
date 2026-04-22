"""Shared MTG and folder-domain helpers used by routes and services."""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from flask import current_app
from sqlalchemy import Integer, case, cast, func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only

from extensions import cache, db
from models import Card, Folder, FolderRole
from core.domains.cards.services.scryfall_cache import (
    cache_ready,
    ensure_cache_loaded,
    prints_for_oracle,
    unique_oracle_by_name,
)
from shared.mtg_prints import (
    WUBRG_ORDER,
    _bulk_print_lookup,
    _card_type_flags,
    _color_letters_list,
    _effective_color_identity,
    _faces_image_payload,
    _faces_list,
    _img_url_for_print,
    _lookup_print_data,
    _mana_cost_from_faces,
    _normalize_name,
    _oracle_text_from_faces,
    _small_thumb_for_print,
    _scryfall_card_url,
    _scryfall_set_url,
    _token_stubs_from_oracle_text,
    _type_line_from_faces,
    _type_line_from_print,
    _unique_art_variants,
    color_identity_name,
)

DEFAULT_COLLECTION_FOLDERS = {"lands", "common", "uncommon", "rare", "mythic", "to add"}
API_PAGE_SIZE = 175  # Scryfall /cards/search page size


def _collection_rows_with_fallback(
    owner_user_ids: Iterable[int] | None = None,
) -> list[tuple[int | None, str | None]]:
    """Return (id, name) tuples for folders that represent collection buckets."""
    rows: list[tuple[int | None, str | None]] = []
    owner_ids = [oid for oid in (owner_user_ids or []) if oid is not None]
    try:
        query = (
            db.session.query(Folder.id, Folder.name)
            .join(FolderRole, FolderRole.folder_id == Folder.id)
            .filter(FolderRole.role == FolderRole.ROLE_COLLECTION)
        )
        if owner_ids:
            query = query.filter(Folder.owner_user_id.in_(owner_ids))
        rows = query.order_by(func.lower(Folder.name)).all()
    except SQLAlchemyError:
        current_app.logger.exception("Failed to load collection folders (primary query)")
        db.session.rollback()
        return [(None, name.title()) for name in sorted(DEFAULT_COLLECTION_FOLDERS)]
    if rows:
        return rows
    try:
        has_folders_query = db.session.query(Folder.id)
        if owner_ids:
            has_folders_query = has_folders_query.filter(Folder.owner_user_id.in_(owner_ids))
        has_folders = has_folders_query.limit(1).first()
    except SQLAlchemyError:
        current_app.logger.exception("Failed to check folder existence (collection fallback)")
        db.session.rollback()
        return [(None, name.title()) for name in sorted(DEFAULT_COLLECTION_FOLDERS)]
    if has_folders:
        return []
    # Last-resort hard-coded defaults so the UI can still render on empty installs.
    return [(None, name.title()) for name in sorted(DEFAULT_COLLECTION_FOLDERS)]


def _collection_folder_ids() -> set[int]:
    """Convenience accessor for the collection folder primary keys."""
    return {fid for fid, _ in _collection_rows_with_fallback() if fid is not None}


def _collection_folder_names() -> list[str]:
    """List the human-facing names of folders currently treated as collection buckets."""
    return [name for _, name in _collection_rows_with_fallback() if name]


def _collection_folder_lower_names() -> set[str]:
    """Return normalized names for folders explicitly tagged as collection buckets."""
    rows = _collection_rows_with_fallback()
    return {(name or "").strip().lower() for fid, name in rows if fid is not None and name}


def _collection_metadata() -> tuple[list[int], list[str], set[str]]:
    """Gather ids/names in one go so expensive lookups happen once per request."""
    rows = _collection_rows_with_fallback()
    ids = [fid for fid, _ in rows if fid is not None]
    names = [name for _, name in rows if name]
    lowered = {(name or "").strip().lower() for fid, name in rows if fid is not None and name}
    return ids, names, lowered


@lru_cache(maxsize=1)
def _folder_id_name_map() -> dict[int, str]:
    rows = db.session.query(Folder.id, Folder.name).all()
    return {fid: name for fid, name in rows}


def _move_folder_choices(exclude_folder_id: int | None = None) -> list:
    """Return folder options that the current user is allowed to move cards into."""
    from flask_login import current_user
    from core.domains.decks.viewmodels.folder_vm import FolderMoveOptionVM

    if not current_user.is_authenticated:
        return []

    query = Folder.query
    if not getattr(current_user, "is_admin", False):
        query = query.filter(or_(Folder.owner_user_id == current_user.id, Folder.owner_user_id.is_(None)))
    folders = query.order_by(func.lower(Folder.name)).all()

    options: list[FolderMoveOptionVM] = []
    for folder in folders:
        if exclude_folder_id and folder.id == exclude_folder_id:
            continue
        options.append(
            FolderMoveOptionVM(
                id=folder.id,
                name=folder.name or f"Folder {folder.id}",
                is_collection=bool(folder.is_collection),
                is_proxy=bool(folder.is_proxy_deck),
            )
        )
    return options


def _name_sort_expr():
    """Prefer Card.name_sort if schema has it, else lower(name)."""
    return getattr(Card, "name_sort", None) or func.lower(Card.name)


def _collector_number_numeric():
    """
    Naturalize collector_number for sorting:
      - Cast only when the entire collector_number is digits; otherwise None.
    """
    if db.engine.dialect.name == "sqlite":
        has_digits = Card.collector_number.op("GLOB")("[0-9]*")
        has_nondigit = Card.collector_number.op("GLOB")("*[^0-9]*")
        is_numeric = (Card.collector_number != "") & has_digits & ~has_nondigit
    else:
        is_numeric = Card.collector_number.op("~")(r"^[0-9]+$")
    return case((is_numeric, cast(Card.collector_number, Integer)), else_=None)


def _commander_candidates_for_folder(folder_id: int, limit: int = 60):
    """Likely commander candidates for a folder, enriched with image and print info."""
    if not cache_ready():
        ensure_cache_loaded()
    qs = (
        Card.query.filter(Card.folder_id == folder_id)
        .options(
            load_only(
                Card.id,
                Card.name,
                Card.set_code,
                Card.collector_number,
                Card.lang,
                Card.is_foil,
                Card.oracle_id,
                Card.type_line,
            )
        )
        .all()
    )

    def _image_from_print(pr):
        if not pr:
            return None
        iu = pr.get("image_uris") or {}
        if iu:
            return iu.get("small") or iu.get("normal") or iu.get("large")
        faces = pr.get("card_faces") or []
        if faces:
            face_uris = (faces[0] or {}).get("image_uris") or {}
            return face_uris.get("small") or face_uris.get("normal") or face_uris.get("large")
        return None

    out = []
    for card in qs:
        pr = _lookup_print_data(card.set_code, card.collector_number, card.name, card.oracle_id)
        tline = getattr(card, "type_line", "") or pr.get("type_line") or ""
        if not tline:
            faces = (pr or {}).get("card_faces") or []
            if faces:
                tline = (faces[0] or {}).get("type_line") or ""
        lowered = tline.lower()
        if ("legendary" in lowered) and ("creature" in lowered or "artifact" in lowered):
            out.append(
                {
                    "card_id": card.id,
                    "name": card.name,
                    "oracle_id": card.oracle_id,
                    "set_code": card.set_code,
                    "collector_number": card.collector_number,
                    "lang": card.lang,
                    "is_foil": bool(card.is_foil),
                    "image": _image_from_print(pr),
                    "type_line": tline,
                }
            )

    out.sort(key=lambda row: (row["name"] or "").lower())
    return out[:limit]


@cache.memoize(timeout=300)
def compute_folder_color_identity(folder_id: int, cache_version: str = "20260311a"):
    """
    Return (letters, label) for the folder's color identity using Scryfall cache.
    letters: e.g., "WUG" or "" (colorless). label: friendly name.
    """
    _ = cache_version  # cache key versioning for memoized results
    seen = set()

    def _letters_from_value(value):
        if not value:
            return []
        if isinstance(value, (list, tuple, set)):
            raw = [str(v).upper() for v in value]
        else:
            raw = [ch for ch in str(value).upper()]
        return [ch for ch in raw if ch in WUBRG_ORDER]

    def _commander_oracle_ids(raw_value) -> list[str]:
        text = str(raw_value or "").strip()
        if not text:
            return []
        return [piece.strip() for piece in text.split(",") if piece.strip()]

    def _commander_names(raw_value) -> list[str]:
        text = str(raw_value or "").strip()
        if not text:
            return []
        return [piece.strip() for piece in text.split("//") if piece.strip()]

    folder_row = (
        db.session.query(Folder.commander_oracle_id, Folder.commander_name)
        .filter(Folder.id == folder_id)
        .first()
    )
    commander_blob = folder_row.commander_oracle_id if folder_row else None
    commander_name_blob = folder_row.commander_name if folder_row else None

    commander_resolved = False
    for oracle_id in _commander_oracle_ids(commander_blob):
        try:
            prints = prints_for_oracle(oracle_id) or []
        except Exception:
            prints = []
        if not prints:
            continue
        commander_resolved = True
        commander_letters = _letters_from_value(prints[0].get("color_identity")) or _letters_from_value(
            prints[0].get("colors")
        )
        seen.update(commander_letters)

    if not commander_resolved:
        for commander_name in _commander_names(commander_name_blob):
            try:
                oracle_id = unique_oracle_by_name(commander_name)
            except Exception:
                oracle_id = None
            if not oracle_id:
                continue
            try:
                prints = prints_for_oracle(oracle_id) or []
            except Exception:
                prints = []
            if not prints:
                continue
            commander_resolved = True
            commander_letters = _letters_from_value(prints[0].get("color_identity")) or _letters_from_value(
                prints[0].get("colors")
            )
            seen.update(commander_letters)

    if commander_resolved:
        letters = "".join([color for color in WUBRG_ORDER if color in seen])
        return letters, color_identity_name(letters)

    rows = (
        db.session.query(
            Card.color_identity,
            Card.colors,
            Card.set_code,
            Card.collector_number,
            Card.name,
            Card.oracle_id,
        )
        .filter(Card.folder_id == folder_id)
        .all()
    )
    if not rows:
        return "", color_identity_name([])

    missing_rows = []
    for ci_val, colors_val, set_code, collector_number, card_name, oracle_id in rows:
        letters = _letters_from_value(ci_val) or _letters_from_value(colors_val)
        if letters:
            seen.update(letters)
        else:
            missing_rows.append((set_code, collector_number, card_name, oracle_id))

    if missing_rows:
        for set_code, collector_number, card_name, oracle_id in missing_rows:
            pr = _lookup_print_data(set_code, collector_number, card_name, oracle_id)
            letters = _letters_from_value((pr or {}).get("color_identity")) or _letters_from_value(
                (pr or {}).get("colors")
            )
            seen.update(letters)
            if len(seen) >= len(WUBRG_ORDER):
                break

    letters = "".join([color for color in WUBRG_ORDER if color in seen])
    label = color_identity_name(letters)
    return letters, label


__all__ = [
    "API_PAGE_SIZE",
    "DEFAULT_COLLECTION_FOLDERS",
    "WUBRG_ORDER",
    "_bulk_print_lookup",
    "_collection_folder_ids",
    "_collection_folder_lower_names",
    "_collection_folder_names",
    "_collection_metadata",
    "_collection_rows_with_fallback",
    "_color_letters_list",
    "_effective_color_identity",
    "_faces_list",
    "_faces_image_payload",
    "_collector_number_numeric",
    "_card_type_flags",
    "_commander_candidates_for_folder",
    "_folder_id_name_map",
    "_img_url_for_print",
    "_lookup_print_data",
    "_mana_cost_from_faces",
    "_oracle_text_from_faces",
    "_move_folder_choices",
    "_name_sort_expr",
    "_normalize_name",
    "_scryfall_card_url",
    "_scryfall_set_url",
    "_small_thumb_for_print",
    "_token_stubs_from_oracle_text",
    "_type_line_from_faces",
    "_type_line_from_print",
    "_unique_art_variants",
    "color_identity_name",
    "compute_folder_color_identity",
]
