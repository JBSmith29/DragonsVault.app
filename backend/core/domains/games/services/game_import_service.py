"""Game CSV import parsing and persistence helpers."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from extensions import db
from core.domains.decks.models import Folder, FolderRole
from core.domains.games.models import GameDeck, GamePlayer, GameSeat, GameSeatAssignment, GameSession
from core.domains.users.models import User


class GameImportError(ValueError):
    """Raised when the upload payload is invalid."""


@dataclass(frozen=True)
class GameImportPayload:
    rows: list[dict[str, str]]
    wide_format: bool


def _norm_key(value: str | None) -> str:
    return (value or "").strip().lower()


def _parse_int(value: str | None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value: str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def _get_seat_value(row: dict[str, str], seat_number: int, key: str) -> str:
    for prefix in (f"seat_{seat_number}_", f"seat{seat_number}_"):
        value = row.get(f"{prefix}{key}")
        if value is not None:
            return value
    return ""


def parse_csv_payload(raw_text: str) -> GameImportPayload:
    try:
        sample = raw_text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(raw_text), dialect=dialect)
        rows = [row for row in reader if row]
    except Exception as exc:
        raise GameImportError("Import file must be valid CSV.") from exc

    if not rows:
        raise GameImportError("Import file is missing rows.")

    normalized_rows = []
    for row in rows:
        normalized = {_norm_key(k): (v or "").strip() for k, v in row.items() if k}
        if normalized:
            normalized_rows.append(normalized)

    if not normalized_rows:
        raise GameImportError("Import file is missing rows.")

    key_set = set()
    for row in normalized_rows:
        key_set.update(row.keys())

    wide_format = any(key.startswith("seat_1_") or key.startswith("seat1_") for key in key_set)
    return GameImportPayload(rows=normalized_rows, wide_format=wide_format)


def _build_commander_lookup(owner_user_id: int) -> tuple[dict[str, list[Folder]], dict[str, list[Folder]]]:
    decks = (
        Folder.query.join(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(
            FolderRole.role.in_(FolderRole.DECK_ROLES),
            Folder.owner_user_id == owner_user_id,
        )
        .all()
    )
    by_oracle: dict[str, list[Folder]] = {}
    by_name: dict[str, list[Folder]] = {}
    for deck in decks:
        if deck.commander_oracle_id:
            by_oracle.setdefault(deck.commander_oracle_id, []).append(deck)
        if deck.commander_name:
            by_name.setdefault(deck.commander_name.strip().lower(), []).append(deck)
    return by_oracle, by_name


def _match_commander_deck(
    lookup: tuple[dict[str, list[Folder]], dict[str, list[Folder]]],
    commander_oracle_id: str | None,
    commander_name: str | None,
) -> Folder | None:
    by_oracle, by_name = lookup
    if commander_oracle_id:
        candidates = by_oracle.get(commander_oracle_id, [])
        if len(candidates) == 1:
            return candidates[0]
    if commander_name:
        candidates = by_name.get(commander_name.strip().lower(), [])
        if len(candidates) == 1:
            return candidates[0]
    return None


def _apply_manual_name(snapshot: dict[str, Any], manual_name: str | None) -> dict[str, Any]:
    name = (manual_name or "").strip()
    if name:
        snapshot["deck_name"] = name
    return snapshot


def _build_folder_map(owner_user_id: int, seat_rows: list[dict[str, str]]) -> dict[int, Folder]:
    folder_ids: set[int] = set()
    for seat_row in seat_rows:
        for seat_number in range(1, 5):
            folder_id = _parse_int(_get_seat_value(seat_row, seat_number, "deck_folder_id"))
            if folder_id:
                folder_ids.add(folder_id)
    if not folder_ids:
        return {}
    return {
        folder.id: folder
        for folder in Folder.query.filter(
            Folder.id.in_(folder_ids),
            Folder.owner_user_id == owner_user_id,
        ).all()
    }


def _import_wide_rows(
    *,
    seat_rows: list[dict[str, str]],
    owner_user_id: int,
    commander_lookup: tuple[dict[str, list[Folder]], dict[str, list[Folder]]],
    parse_played_at: Callable[[str, list[str]], datetime],
    snapshot_deck: Callable[[Folder], dict[str, Any]],
    find_deck_by_name: Callable[[int, str | None], Folder | None],
) -> tuple[int, int, list[str]]:
    imported = 0
    skipped = 0
    errors: list[str] = []
    folder_map = _build_folder_map(owner_user_id, seat_rows)
    user_cache: dict[int, User | None] = {}

    for index, row in enumerate(seat_rows, start=1):
        game_key = row.get("game_id") or f"row-{index}"
        notes = (row.get("notes") or "").strip()
        win_via_combo = _truthy(row.get("win_via_combo"))
        winner_seat_number = _parse_int(row.get("winner_seat") or "")
        played_at_raw = row.get("played_at") or ""

        session_errors: list[str] = []
        played_at = parse_played_at(str(played_at_raw or ""), session_errors)
        if session_errors:
            errors.extend([f"Game {game_key}: {msg}" for msg in session_errors])
            skipped += 1
            continue

        seat_payloads: list[dict[str, Any]] = []
        for seat_number in range(1, 5):
            player_name = _get_seat_value(row, seat_number, "player_name")
            player_user_id = _parse_int(_get_seat_value(row, seat_number, "player_user_id"))
            deck_name = _get_seat_value(row, seat_number, "deck_name")
            deck_folder_id = _parse_int(_get_seat_value(row, seat_number, "deck_folder_id"))
            commander_name = _get_seat_value(row, seat_number, "commander_name")
            commander_oracle_id = _get_seat_value(row, seat_number, "commander_oracle_id")
            bracket_level = _get_seat_value(row, seat_number, "bracket_level")
            bracket_label = _get_seat_value(row, seat_number, "bracket_label")
            bracket_score = _parse_float(_get_seat_value(row, seat_number, "bracket_score"))
            power_score = _parse_float(_get_seat_value(row, seat_number, "power_score"))
            turn_order = _parse_int(_get_seat_value(row, seat_number, "turn_order")) or seat_number
            if not turn_order or turn_order < 1 or turn_order > 4:
                turn_order = seat_number

            has_data = any(
                [
                    player_name,
                    player_user_id,
                    deck_name,
                    deck_folder_id,
                    commander_name,
                    commander_oracle_id,
                ]
            )
            if not has_data:
                continue

            player_user = None
            if player_user_id:
                if player_user_id in user_cache:
                    player_user = user_cache[player_user_id]
                else:
                    player_user = User.query.filter(User.id == player_user_id).first()
                    user_cache[player_user_id] = player_user
            display_name = player_name or None
            if player_user and not display_name:
                display_name = (
                    (player_user.display_name or "").strip()
                    or (player_user.username or "").strip()
                    or (player_user.email or "").strip()
                    or None
                )
            if not display_name:
                display_name = f"Player {seat_number}"

            if deck_folder_id and deck_folder_id in folder_map:
                deck_snapshot = snapshot_deck(folder_map[deck_folder_id])
                deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
            else:
                owner_id = player_user.id if player_user else owner_user_id
                name_match = find_deck_by_name(owner_id, deck_name)
                if name_match:
                    deck_snapshot = snapshot_deck(name_match)
                    deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
                else:
                    matched_deck = _match_commander_deck(
                        commander_lookup,
                        commander_oracle_id or None,
                        commander_name or None,
                    )
                    if matched_deck:
                        deck_snapshot = snapshot_deck(matched_deck)
                        deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
                    else:
                        deck_snapshot = {
                            "folder_id": None,
                            "deck_name": deck_name or "Unknown deck",
                            "commander_name": commander_name or None,
                            "commander_oracle_id": commander_oracle_id or None,
                            "bracket_level": bracket_level or None,
                            "bracket_label": bracket_label or None,
                            "bracket_score": bracket_score,
                            "power_score": power_score,
                        }

            seat_payloads.append(
                {
                    "seat_number": seat_number,
                    "turn_order": turn_order,
                    "player_user": player_user,
                    "player_label": display_name,
                    "deck_snapshot": deck_snapshot,
                }
            )

        seat_count = len(seat_payloads)
        if not (2 <= seat_count <= 4):
            errors.append(f"Game {game_key}: seat count must be 2-4.")
            skipped += 1
            continue

        session = GameSession(
            owner_user_id=owner_user_id,
            played_at=played_at,
            notes=notes or None,
            win_via_combo=bool(win_via_combo),
        )
        db.session.add(session)
        db.session.flush()

        seats_by_number: dict[int, GameSeat] = {}
        for payload in sorted(seat_payloads, key=lambda item: item["seat_number"]):
            seat = GameSeat(
                session_id=session.id,
                seat_number=payload["seat_number"],
                turn_order=payload["turn_order"],
            )
            db.session.add(seat)
            seats_by_number[seat.seat_number] = seat

        for payload in seat_payloads:
            seat = seats_by_number.get(payload["seat_number"])
            if not seat:
                continue
            player = GamePlayer(
                user_id=payload["player_user"].id if payload.get("player_user") else None,
                display_name=payload.get("player_label"),
            )
            db.session.add(player)
            deck_data = payload.get("deck_snapshot") or {}
            deck = GameDeck(
                session_id=session.id,
                folder_id=deck_data.get("folder_id"),
                deck_name=deck_data.get("deck_name") or "Unknown deck",
                commander_name=deck_data.get("commander_name"),
                commander_oracle_id=deck_data.get("commander_oracle_id"),
                bracket_level=deck_data.get("bracket_level"),
                bracket_label=deck_data.get("bracket_label"),
                bracket_score=deck_data.get("bracket_score"),
                power_score=deck_data.get("power_score"),
            )
            db.session.add(deck)
            assignment = GameSeatAssignment(
                session_id=session.id,
                seat=seat,
                player=player,
                deck=deck,
            )
            db.session.add(assignment)

        if winner_seat_number and winner_seat_number in seats_by_number:
            session.winner_seat = seats_by_number[winner_seat_number]
        if not session.winner_seat:
            session.win_via_combo = False

        try:
            db.session.commit()
            imported += 1
        except Exception:
            db.session.rollback()
            errors.append(f"Game {game_key}: unable to import.")
            skipped += 1

    return imported, skipped, errors


def _import_long_rows(
    *,
    seat_rows: list[dict[str, str]],
    owner_user_id: int,
    commander_lookup: tuple[dict[str, list[Folder]], dict[str, list[Folder]]],
    parse_played_at: Callable[[str, list[str]], datetime],
    snapshot_deck: Callable[[Folder], dict[str, Any]],
    find_deck_by_name: Callable[[int, str | None], Folder | None],
) -> tuple[int, int, list[str]]:
    errors: list[str] = []
    grouped: dict[str, list[dict[str, str]]] = {}
    for index, row in enumerate(seat_rows, start=1):
        game_key = row.get("game_id") or row.get("game_key")
        if not game_key:
            errors.append(f"Row {index}: missing game_id column.")
            continue
        grouped.setdefault(str(game_key), []).append(row)
    if not grouped:
        return 0, len(seat_rows), errors

    folder_ids: set[int] = set()
    for grouped_rows in grouped.values():
        for row in grouped_rows:
            folder_id = _parse_int(row.get("deck_folder_id") or "")
            if folder_id:
                folder_ids.add(folder_id)
    folder_map = {
        folder.id: folder
        for folder in Folder.query.filter(
            Folder.id.in_(folder_ids),
            Folder.owner_user_id == owner_user_id,
        ).all()
    } if folder_ids else {}

    imported = 0
    skipped = 0
    user_cache: dict[int, User | None] = {}

    for game_key, grouped_rows in grouped.items():
        seat_numbers = {
            _parse_int(row.get("seat_number") or "")
            for row in grouped_rows
            if row.get("seat_number")
        }
        seat_numbers = {num for num in seat_numbers if num}
        seat_count = len(seat_numbers) if seat_numbers else len(grouped_rows)
        if not (2 <= seat_count <= 4):
            errors.append(f"Game {game_key}: seat count must be 2-4.")
            skipped += 1
            continue

        first_row = grouped_rows[0]
        played_at_raw = first_row.get("played_at") or ""
        notes = (first_row.get("notes") or "").strip()
        win_via_combo = _truthy(first_row.get("win_via_combo"))
        winner_seat_number = _parse_int(first_row.get("winner_seat") or "")

        session_errors: list[str] = []
        played_at = parse_played_at(str(played_at_raw or ""), session_errors)
        if session_errors:
            errors.extend([f"Game {game_key}: {msg}" for msg in session_errors])
            skipped += 1
            continue

        session = GameSession(
            owner_user_id=owner_user_id,
            played_at=played_at,
            notes=notes or None,
            win_via_combo=bool(win_via_combo),
        )
        db.session.add(session)
        db.session.flush()

        seats_by_number: dict[int, GameSeat] = {}
        seat_payloads: list[dict[str, Any]] = []

        for seat_entry in grouped_rows:
            seat_number = _parse_int(seat_entry.get("seat_number") or "")
            if not seat_number:
                seat_number = max(seats_by_number.keys(), default=0) + 1
            if seat_number > 4 or seat_number < 1:
                continue
            if seat_number in seats_by_number:
                continue
            turn_order = _parse_int(seat_entry.get("turn_order") or "") or seat_number
            if not turn_order or turn_order < 1 or turn_order > 4:
                turn_order = seat_number
            seat = GameSeat(
                session_id=session.id,
                seat_number=seat_number,
                turn_order=turn_order,
            )
            db.session.add(seat)
            seats_by_number[seat_number] = seat

            player_user = None
            display_name = (seat_entry.get("player_name") or "").strip() or None
            user_id = _parse_int(seat_entry.get("player_user_id") or "")
            if user_id:
                if user_id in user_cache:
                    player_user = user_cache[user_id]
                else:
                    player_user = User.query.filter(User.id == user_id).first()
                    user_cache[user_id] = player_user
            if player_user and not display_name:
                display_name = (
                    (player_user.display_name or "").strip()
                    or (player_user.username or "").strip()
                    or (player_user.email or "").strip()
                    or None
                )
            if not display_name:
                display_name = f"Player {seat_number}"

            deck_name = seat_entry.get("deck_name") or None
            deck_folder_id = _parse_int(seat_entry.get("deck_folder_id") or "")
            if deck_folder_id and deck_folder_id in folder_map:
                deck_snapshot = snapshot_deck(folder_map[deck_folder_id])
                deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
            else:
                owner_id = player_user.id if player_user else owner_user_id
                name_match = find_deck_by_name(owner_id, deck_name)
                if name_match:
                    deck_snapshot = snapshot_deck(name_match)
                    deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
                else:
                    commander_name = seat_entry.get("commander_name") or None
                    commander_oracle_id = seat_entry.get("commander_oracle_id") or None
                    matched_deck = _match_commander_deck(
                        commander_lookup,
                        commander_oracle_id,
                        commander_name,
                    )
                    if matched_deck:
                        deck_snapshot = snapshot_deck(matched_deck)
                        deck_snapshot = _apply_manual_name(deck_snapshot, deck_name)
                    else:
                        deck_snapshot = {
                            "folder_id": None,
                            "deck_name": deck_name or "Unknown deck",
                            "commander_name": commander_name,
                            "commander_oracle_id": commander_oracle_id,
                            "bracket_level": seat_entry.get("bracket_level") or None,
                            "bracket_label": seat_entry.get("bracket_label") or None,
                            "bracket_score": _parse_float(seat_entry.get("bracket_score") or ""),
                            "power_score": _parse_float(seat_entry.get("power_score") or ""),
                        }

            seat_payloads.append(
                {
                    "seat_number": seat_number,
                    "player_user": player_user,
                    "player_label": display_name,
                    "deck_snapshot": deck_snapshot,
                }
            )

        for payload in seat_payloads:
            seat = seats_by_number.get(payload["seat_number"])
            if not seat:
                continue
            player = GamePlayer(
                user_id=payload["player_user"].id if payload.get("player_user") else None,
                display_name=payload.get("player_label"),
            )
            db.session.add(player)
            deck_data = payload.get("deck_snapshot") or {}
            deck = GameDeck(
                session_id=session.id,
                folder_id=deck_data.get("folder_id"),
                deck_name=deck_data.get("deck_name") or "Unknown deck",
                commander_name=deck_data.get("commander_name"),
                commander_oracle_id=deck_data.get("commander_oracle_id"),
                bracket_level=deck_data.get("bracket_level"),
                bracket_label=deck_data.get("bracket_label"),
                bracket_score=deck_data.get("bracket_score"),
                power_score=deck_data.get("power_score"),
            )
            db.session.add(deck)
            assignment = GameSeatAssignment(
                session_id=session.id,
                seat=seat,
                player=player,
                deck=deck,
            )
            db.session.add(assignment)

        if winner_seat_number and winner_seat_number in seats_by_number:
            session.winner_seat = seats_by_number[winner_seat_number]
        if not session.winner_seat:
            session.win_via_combo = False

        try:
            db.session.commit()
            imported += 1
        except Exception:
            db.session.rollback()
            errors.append(f"Game {game_key}: unable to import.")
            skipped += 1

    return imported, skipped, errors


def import_games_csv(
    *,
    raw_text: str,
    owner_user_id: int,
    parse_played_at: Callable[[str, list[str]], datetime],
    snapshot_deck: Callable[[Folder], dict[str, Any]],
    find_deck_by_name: Callable[[int, str | None], Folder | None],
) -> tuple[int, int, list[str]]:
    payload = parse_csv_payload(raw_text)
    commander_lookup = _build_commander_lookup(owner_user_id)

    if payload.wide_format:
        return _import_wide_rows(
            seat_rows=payload.rows,
            owner_user_id=owner_user_id,
            commander_lookup=commander_lookup,
            parse_played_at=parse_played_at,
            snapshot_deck=snapshot_deck,
            find_deck_by_name=find_deck_by_name,
        )

    return _import_long_rows(
        seat_rows=payload.rows,
        owner_user_id=owner_user_id,
        commander_lookup=commander_lookup,
        parse_played_at=parse_played_at,
        snapshot_deck=snapshot_deck,
        find_deck_by_name=find_deck_by_name,
    )
