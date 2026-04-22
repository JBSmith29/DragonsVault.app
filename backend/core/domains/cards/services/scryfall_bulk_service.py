"""Bulk metadata, download, and file-stat helpers for Scryfall datasets."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional


def fetch_bulk_index(
    *,
    session_factory: Callable[[], Any],
    bulk_meta_url: str,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Fetch Scryfall bulk index metadata."""
    session = session_factory()
    response = session.get(bulk_meta_url, timeout=timeout)
    response.raise_for_status()
    payload = response.json() or {}
    return payload.get("data", [])


def get_bulk_metadata(
    kind: str,
    *,
    fetch_bulk_index_fn: Callable[[], list[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    """Return the metadata block for a given Scryfall bulk dataset."""
    try:
        for item in fetch_bulk_index_fn():
            if item.get("type") == kind:
                return item
    except Exception:
        return None
    return None


def get_bulk_download_uri(
    kind: str,
    *,
    get_bulk_metadata_fn: Callable[[str], Optional[dict[str, Any]]],
) -> Optional[str]:
    entry = get_bulk_metadata_fn(kind)
    return entry.get("download_uri") if entry else None


def get_default_cards_download_uri(
    *,
    get_bulk_download_uri_fn: Callable[[str], Optional[str]],
) -> Optional[str]:
    return get_bulk_download_uri_fn("default_cards")


def _etag_file_for(path: Path, override: Optional[str] = None) -> Path:
    if override:
        return Path(override)
    return path.with_suffix(path.suffix + ".etag")


def stream_download_to(
    path: str,
    url: str,
    *,
    session_factory: Callable[[], Any],
    chunk_size: int = 1 << 20,
    etag_path: Optional[str] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    timeout: int = 600,
    force_download: bool = False,
) -> Dict[str, Any]:
    """
    Download a URL to `path`, supporting ETag-based conditional GET and retries.

    Returns metadata describing whether the file changed and how many bytes were written:
      {"status": "downloaded"|"not_modified", "bytes": <int>, "total": <int>, "etag": <str|None>, "path": <str>}
    """
    session = session_factory()
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    etag_file = _etag_file_for(dest, etag_path)
    headers: Dict[str, str] = {}
    cached_etag = None
    if force_download:
        try:
            if etag_file.exists():
                etag_file.unlink()
        except Exception:
            pass
    else:
        if etag_file.exists():
            try:
                cached_etag = etag_file.read_text(encoding="utf-8").strip()
            except Exception:
                cached_etag = None
            if cached_etag:
                headers["If-None-Match"] = cached_etag

    response = session.get(url, stream=True, timeout=timeout, headers=headers)
    if response.status_code == 304:
        return {
            "status": "not_modified",
            "etag": cached_etag,
            "bytes": 0,
            "total": 0,
            "path": str(dest),
        }

    response.raise_for_status()
    total = int(response.headers.get("Content-Length") or 0)
    written = 0

    try:
        with open(tmp, "wb") as handle:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
                if progress_cb:
                    progress_cb(written, total)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass

    new_etag = response.headers.get("ETag")
    if new_etag:
        try:
            etag_file.write_text(new_etag, encoding="utf-8")
        except Exception:
            pass
    elif etag_file.exists():
        try:
            etag_file.unlink()
        except Exception:
            pass

    return {
        "status": "downloaded",
        "etag": new_etag,
        "bytes": written,
        "total": total,
        "path": str(dest),
    }


def cache_stats(
    path: Optional[str] = None,
    *,
    default_cards_path_fn: Callable[[Optional[str]], str],
    rulings_bulk_path_fn: Callable[[Optional[str]], str],
    default_is_stale_fn: Callable[[Optional[str]], bool],
    rulings_is_stale_fn: Callable[[Optional[str]], bool],
    prints_record_count: int,
    unique_set_count: int,
    unique_oracle_count: int,
    by_set_cn_count: int,
    by_set_num_count: int,
    by_name_count: int,
    by_front_count: int,
    rulings_oracle_key_count: int,
    rulings_entry_count: int,
) -> Dict[str, Any]:
    prints_path = default_cards_path_fn(path)
    rulings_path = rulings_bulk_path_fn(path)

    prints_exists = os.path.exists(prints_path)
    prints_size = os.path.getsize(prints_path) if prints_exists else 0
    prints_mtime = os.path.getmtime(prints_path) if prints_exists else None

    rulings_exists = os.path.exists(rulings_path)
    rulings_size = os.path.getsize(rulings_path) if rulings_exists else 0
    rulings_mtime = os.path.getmtime(rulings_path) if rulings_exists else None

    return {
        "prints": {
            "file": prints_path,
            "exists": prints_exists,
            "size_bytes": prints_size,
            "modified_at": prints_mtime,
            "age_seconds": (time.time() - prints_mtime) if prints_mtime else None,
            "stale": default_is_stale_fn(prints_path) if prints_exists else True,
            "records": prints_record_count,
            "unique_sets": unique_set_count,
            "unique_oracles": unique_oracle_count,
            "index_sizes": {
                "by_set_cn": by_set_cn_count,
                "by_oracle": unique_oracle_count,
                "by_set_num": by_set_num_count,
                "by_name": by_name_count,
                "by_front": by_front_count,
            },
        },
        "rulings": {
            "file": rulings_path,
            "exists": rulings_exists,
            "size_bytes": rulings_size,
            "modified_at": rulings_mtime,
            "age_seconds": (time.time() - rulings_mtime) if rulings_mtime else None,
            "stale": rulings_is_stale_fn(rulings_path) if rulings_exists else True,
            "oracle_keys": rulings_oracle_key_count,
            "entries": rulings_entry_count,
        },
    }
