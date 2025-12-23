from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from config import ServiceConfig
from db import ensure_tables, get_session_factory
from models import ScryfallBulkMeta, ScryfallOracle

SCRYFALL_BULK_URL = "https://api.scryfall.com/bulk-data"
DEFAULT_BULK_NAME = "default_cards"
SYNC_LOCK_KEY = 948512
EXCLUDED_SET_TYPES = {"token", "memorabilia", "art_series"}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    token = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(token)
    except ValueError:
        return None


def _build_session(config: ServiceConfig) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": config.scryfall_user_agent,
            "Accept": "application/json",
        }
    )
    return session


def _fetch_bulk_index(session: requests.Session, timeout: int) -> dict[str, Any]:
    response = session.get(SCRYFALL_BULK_URL, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _select_bulk_entry(index: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in index.get("data", []):
        if entry.get("type") == name or entry.get("name") == name:
            return entry
    raise RuntimeError(f"bulk_dataset_not_found:{name}")


def _ensure_data_dir(path: str) -> Path:
    data_dir = Path(path)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _download_bulk(
    session: requests.Session, download_uri: str, dest: Path, timeout: int
) -> str | None:
    response = session.get(download_uri, stream=True, timeout=timeout)
    response.raise_for_status()
    etag = response.headers.get("ETag")
    with dest.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    return etag


def _stream_json_array(handle, chunk_size: int = 1024 * 1024) -> Iterable[Any]:
    decoder = json.JSONDecoder()
    buffer = ""
    in_array = False
    while True:
        chunk = handle.read(chunk_size)
        if not chunk:
            break
        buffer += chunk
        while True:
            buffer = buffer.lstrip()
            if not buffer:
                break
            if not in_array:
                if buffer.startswith("["):
                    buffer = buffer[1:]
                    in_array = True
                    continue
                raise RuntimeError("invalid_json_array")
            if buffer.startswith("]"):
                return
            try:
                obj, index = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                break
            yield obj
            buffer = buffer[index:]
            if buffer.startswith(","):
                buffer = buffer[1:]
    if buffer.strip() and buffer.strip() != "]":
        raise RuntimeError("incomplete_json_array")


def _join_faces(faces: list[dict], key: str) -> str | None:
    parts = []
    for face in faces:
        value = (face or {}).get(key)
        if value:
            parts.append(value)
    if not parts:
        return None
    return "\n\n//\n\n".join(parts)


def _compact_faces(faces: list[dict]) -> list[dict]:
    compacted = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        compacted.append(
            {
                "name": face.get("name"),
                "mana_cost": face.get("mana_cost"),
                "type_line": face.get("type_line"),
                "oracle_text": face.get("oracle_text"),
                "colors": face.get("colors"),
                "power": face.get("power"),
                "toughness": face.get("toughness"),
                "loyalty": face.get("loyalty"),
                "defense": face.get("defense"),
            }
        )
    return compacted


def _score_card(card: dict[str, Any]) -> int:
    score = 0
    if card.get("lang") == "en":
        score += 3
    if (card.get("set_type") or "") not in EXCLUDED_SET_TYPES:
        score += 2
    if "paper" in (card.get("games") or []):
        score += 1
    if not card.get("digital"):
        score += 1
    return score


def _build_oracle_record(card: dict[str, Any]) -> dict[str, Any] | None:
    oracle_id = card.get("oracle_id")
    name = card.get("name")
    if not oracle_id or not name:
        return None
    faces = card.get("card_faces") or []
    is_multiface = bool(faces)
    oracle_text = card.get("oracle_text") or _join_faces(faces, "oracle_text")
    type_line = card.get("type_line") or _join_faces(faces, "type_line")
    mana_cost = card.get("mana_cost") or _join_faces(faces, "mana_cost")

    record = {
        "oracle_id": oracle_id,
        "name": name,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "mana_cost": mana_cost,
        "cmc": card.get("cmc"),
        "colors": card.get("colors"),
        "color_identity": card.get("color_identity"),
        "legalities": card.get("legalities"),
        "layout": card.get("layout"),
        "card_faces": _compact_faces(faces) if is_multiface else None,
        "edhrec_rank": card.get("edhrec_rank"),
        "power": card.get("power"),
        "toughness": card.get("toughness"),
        "loyalty": card.get("loyalty"),
        "defense": card.get("defense"),
        "scryfall_uri": card.get("scryfall_uri"),
    }
    return record


def _collect_oracles(path: Path) -> tuple[list[dict[str, Any]], int]:
    oracles: dict[str, tuple[int, dict[str, Any]]] = {}
    total_cards = 0
    with path.open("r", encoding="utf-8") as handle:
        for card in _stream_json_array(handle):
            total_cards += 1
            if not isinstance(card, dict):
                continue
            record = _build_oracle_record(card)
            if not record:
                continue
            oracle_id = record["oracle_id"]
            score = _score_card(card)
            existing = oracles.get(oracle_id)
            if existing is None or score > existing[0]:
                oracles[oracle_id] = (score, record)
    records = [entry[1] for entry in oracles.values()]
    return records, total_cards


def _upsert_oracles(session, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    total = 0
    for idx in range(0, len(records), 500):
        batch = records[idx : idx + 500]
        stmt = insert(ScryfallOracle).values(batch)
        update_cols = {
            "name": stmt.excluded.name,
            "type_line": stmt.excluded.type_line,
            "oracle_text": stmt.excluded.oracle_text,
            "mana_cost": stmt.excluded.mana_cost,
            "cmc": stmt.excluded.cmc,
            "colors": stmt.excluded.colors,
            "color_identity": stmt.excluded.color_identity,
            "legalities": stmt.excluded.legalities,
            "layout": stmt.excluded.layout,
            "card_faces": stmt.excluded.card_faces,
            "edhrec_rank": stmt.excluded.edhrec_rank,
            "power": stmt.excluded.power,
            "toughness": stmt.excluded.toughness,
            "loyalty": stmt.excluded.loyalty,
            "defense": stmt.excluded.defense,
            "scryfall_uri": stmt.excluded.scryfall_uri,
            "updated_at": text("now()"),
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["oracle_id"],
            set_=update_cols,
        )
        session.execute(stmt)
        total += len(batch)
    return total


def _serialize_dt(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat()


def get_status(session) -> dict[str, Any]:
    meta = session.get(ScryfallBulkMeta, DEFAULT_BULK_NAME)
    oracle_count = session.execute(select(func.count()).select_from(ScryfallOracle)).scalar()
    payload = {
        "dataset": DEFAULT_BULK_NAME,
        "records": oracle_count or 0,
    }
    if meta:
        payload.update(
            {
                "updated_at": _serialize_dt(meta.updated_at),
                "processed_at": _serialize_dt(meta.processed_at),
                "download_uri": meta.download_uri,
                "etag": meta.etag,
                "status": meta.status,
                "record_count": meta.record_count,
            }
        )
    return payload


def sync_scryfall(engine, config: ServiceConfig, force: bool = False) -> dict[str, Any]:
    ensure_tables(engine)
    lock_conn = engine.connect()
    locked = lock_conn.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": SYNC_LOCK_KEY}
    ).scalar()
    if not locked:
        lock_conn.close()
        return {"status": "locked"}

    session_factory = get_session_factory(config)
    session = session_factory()
    run_id = uuid.uuid4().hex[:12]
    data_path = None
    try:
        client = _build_session(config)
        bulk_index = _fetch_bulk_index(client, config.scryfall_timeout)
        entry = _select_bulk_entry(bulk_index, DEFAULT_BULK_NAME)
        updated_at = _parse_iso(entry.get("updated_at"))
        download_uri = entry.get("download_uri")
        if not download_uri:
            raise RuntimeError("missing_download_uri")

        existing = session.get(ScryfallBulkMeta, DEFAULT_BULK_NAME)
        if (
            existing
            and existing.updated_at
            and updated_at
            and existing.updated_at == updated_at
            and not force
        ):
            return {
                "status": "not_modified",
                "updated_at": _serialize_dt(existing.updated_at),
                "processed_at": _serialize_dt(existing.processed_at),
                "records": existing.record_count or 0,
            }

        data_dir = _ensure_data_dir(config.scryfall_data_dir)
        data_path = data_dir / f"scryfall_{DEFAULT_BULK_NAME}.json"
        etag = _download_bulk(client, download_uri, data_path, config.scryfall_timeout)
        records, total_cards = _collect_oracles(data_path)
        upserted = _upsert_oracles(session, records)
        session.commit()

        meta = ScryfallBulkMeta(
            dataset_name=DEFAULT_BULK_NAME,
            download_uri=download_uri,
            updated_at=updated_at,
            etag=etag,
            processed_at=datetime.now(timezone.utc),
            record_count=len(records),
            status="ok",
        )
        session.merge(meta)
        session.commit()

        return {
            "status": "ok",
            "run_id": run_id,
            "updated_at": _serialize_dt(updated_at),
            "records": len(records),
            "processed_cards": total_cards,
            "upserted": upserted,
        }
    finally:
        session.close()
        lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SYNC_LOCK_KEY})
        lock_conn.close()
        if data_path and not config.scryfall_keep_downloads:
            try:
                os.remove(data_path)
            except OSError:
                pass
