"""HTTP session and bulk-download helpers for Scryfall access."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

import requests

from core.domains.cards.services import scryfall_bulk_service as bulk
from core.domains.cards.services import scryfall_runtime_service as runtime_service

SCRYFALL_UA = os.getenv("SCRYFALL_UA", "DragonsVault/6 (+https://dragonsvault.app)")
SCRYFALL_RETRY_TOTAL = int(os.getenv("SCRYFALL_HTTP_RETRIES", "5"))
SCRYFALL_RETRY_BACKOFF = float(os.getenv("SCRYFALL_HTTP_BACKOFF", "0.5"))
_status_raw = os.getenv("SCRYFALL_HTTP_STATUS_FORCELIST", "429,500,502,503,504")
_status_codes: List[int] = []
for _code in _status_raw.split(","):
    token = _code.strip()
    if not token:
        continue
    try:
        _status_codes.append(int(token))
    except ValueError:
        continue
if not _status_codes:
    _status_codes = [429, 500, 502, 503, 504]
SCRYFALL_STATUS_FORCELIST = tuple(_status_codes)
BULK_META_URL = "https://api.scryfall.com/bulk-data"

_session: Optional[requests.Session] = None


def scryfall_session() -> requests.Session:
    """Return a shared requests Session with UA + retry config."""
    global _session
    if _session is not None:
        return _session

    _session = runtime_service.build_scryfall_session(
        user_agent=SCRYFALL_UA,
        retry_total=SCRYFALL_RETRY_TOTAL,
        retry_backoff=SCRYFALL_RETRY_BACKOFF,
        status_forcelist=SCRYFALL_STATUS_FORCELIST,
    )
    return _session


def fetch_live_print(set_code: str, collector_number: str, name_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return runtime_service.fetch_live_print(
        set_code,
        collector_number,
        session_factory=scryfall_session,
        name_hint=name_hint,
    )


def fetch_bulk_index() -> List[Dict[str, Any]]:
    return bulk.fetch_bulk_index(
        session_factory=scryfall_session,
        bulk_meta_url=BULK_META_URL,
    )


def get_bulk_metadata(kind: str) -> Optional[Dict[str, Any]]:
    return bulk.get_bulk_metadata(kind, fetch_bulk_index_fn=fetch_bulk_index)


def get_bulk_download_uri(kind: str) -> Optional[str]:
    return bulk.get_bulk_download_uri(kind, get_bulk_metadata_fn=get_bulk_metadata)


def get_default_cards_download_uri() -> Optional[str]:
    return bulk.get_default_cards_download_uri(get_bulk_download_uri_fn=get_bulk_download_uri)


def stream_download_to(
    path: str,
    url: str,
    *,
    chunk_size: int = 1 << 20,
    etag_path: Optional[str] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    timeout: int = 600,
    force_download: bool = False,
) -> Dict[str, Any]:
    return bulk.stream_download_to(
        path,
        url,
        session_factory=scryfall_session,
        chunk_size=chunk_size,
        etag_path=etag_path,
        progress_cb=progress_cb,
        timeout=timeout,
        force_download=force_download,
    )


__all__ = [
    "BULK_META_URL",
    "SCRYFALL_STATUS_FORCELIST",
    "fetch_bulk_index",
    "fetch_live_print",
    "get_bulk_download_uri",
    "get_bulk_metadata",
    "get_default_cards_download_uri",
    "scryfall_session",
    "stream_download_to",
]
