"""HTTP and payload mapping helpers for EDHREC ingestion."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

import requests

from core.domains.decks.services.edhrec import edhrec_payload_service
from core.domains.decks.services.edhrec_client import slugify_theme

_LOG = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(r'__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)
_REQUEST_HEADERS = {
    "User-Agent": "DragonsVault/6 (+https://dragonsvault.app)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def build_edhrec_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_REQUEST_HEADERS)
    return session


def rate_limit(last_request_at: float, *, interval_seconds: float) -> float:
    now = time.monotonic()
    wait_for = interval_seconds - (now - last_request_at)
    if wait_for > 0:
        time.sleep(wait_for)
    return time.monotonic()


def fetch_commander_json(session: requests.Session, url: str) -> tuple[dict | None, dict | None, str | None]:
    try:
        response = session.get(url, timeout=30)
    except Exception as exc:
        return None, None, f"Request failed: {exc}"
    if response.status_code == 404:
        return None, None, "Commander page not found."
    if response.status_code == 429:
        return None, None, "Rate limited by EDHREC."
    if response.status_code >= 400:
        return None, None, f"EDHREC HTTP {response.status_code}."
    html = response.text
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None, None, "Missing embedded JSON payload."
    try:
        raw = json.loads(match.group(1))
    except Exception as exc:
        return None, None, f"Invalid JSON payload: {exc}"
    data = raw.get("props", {}).get("pageProps", {}).get("data", {})
    if isinstance(data, dict):
        container = data.get("container")
        if isinstance(container, dict):
            payload = container.get("json_dict") if isinstance(container.get("json_dict"), dict) else container
        else:
            payload = data
    else:
        payload = None
    if not isinstance(payload, dict):
        return None, raw, "Embedded payload missing."
    return payload, raw, None


def filter_slug_candidates(
    slug_candidates: list[str],
    *,
    missing_slugs: dict[str, dict[str, Any]] | None = None,
    retry_missing: bool = False,
) -> list[str]:
    if retry_missing:
        return list(slug_candidates)
    missing_slugs = missing_slugs or {}
    return [slug for slug in slug_candidates if slug not in missing_slugs]


def fetch_primary_commander_payload(
    session: requests.Session,
    *,
    target_name: str,
    target_oracle_id: str,
    candidates_to_try: list[str],
    last_request_at: float,
    interval_seconds: float,
    missing_slugs: dict[str, dict[str, Any]] | None = None,
    now_iso_fn: Callable[[], str] | None = None,
    fetch_commander_json_fn: Callable[[requests.Session, str], tuple[dict | None, dict | None, str | None]] = fetch_commander_json,
) -> dict[str, Any]:
    payload = raw_json = None
    slug_used = ""
    fetch_error = None
    missing_slugs = missing_slugs if missing_slugs is not None else {}
    for slug in candidates_to_try:
        last_request_at = rate_limit(last_request_at, interval_seconds=interval_seconds)
        url = f"https://edhrec.com/commanders/{slug}"
        payload, raw_json, fetch_error = fetch_commander_json_fn(session, url)
        if fetch_error == "Commander page not found.":
            if now_iso_fn is not None:
                missing_slugs[slug] = {
                    "name": target_name,
                    "oracle_id": target_oracle_id,
                    "last_seen": now_iso_fn(),
                }
            continue
        if fetch_error:
            break
        slug_used = slug
        break
    return {
        "payload": payload,
        "raw_json": raw_json,
        "slug_used": slug_used,
        "fetch_error": fetch_error,
        "last_request_at": last_request_at,
    }


def fetch_commander_bundle(
    session: requests.Session,
    *,
    target_name: str,
    target_oracle_id: str,
    candidates_to_try: list[str],
    last_request_at: float,
    interval_seconds: float,
    lookup_oracle_id_fn: Callable[[str], str | None],
    max_synergy_cards: int | None,
    missing_slugs: dict[str, dict[str, Any]] | None = None,
    now_iso_fn: Callable[[], str] | None = None,
) -> dict[str, Any]:
    primary = fetch_primary_commander_payload(
        session,
        target_name=target_name,
        target_oracle_id=target_oracle_id,
        candidates_to_try=candidates_to_try,
        last_request_at=last_request_at,
        interval_seconds=interval_seconds,
        missing_slugs=missing_slugs,
        now_iso_fn=now_iso_fn,
    )
    primary["commander_rows"] = {
        "synergy_rows": [],
        "category_rows": [],
        "tags": [],
        "commander_type_rows": [],
    }
    if primary["payload"] and primary["raw_json"]:
        primary["commander_rows"] = build_commander_rows(
            primary["payload"],
            primary["raw_json"],
            lookup_oracle_id_fn=lookup_oracle_id_fn,
            max_synergy_cards=max_synergy_cards,
        )
    return primary


def build_commander_rows(
    payload: dict[str, Any],
    raw_json: dict[str, Any],
    *,
    lookup_oracle_id_fn: Callable[[str], str | None],
    max_synergy_cards: int | None,
    upsert_tags_fn: Callable[[list[str]], list[str]] | None = None,
) -> dict[str, Any]:
    upsert_tags_fn = upsert_tags_fn or edhrec_payload_service.upsert_edhrec_tags
    views = edhrec_payload_service.extract_cardviews(payload)
    synergy_rows = edhrec_payload_service.map_synergy_cards(
        views,
        lookup_oracle_id_fn=lookup_oracle_id_fn,
        max_synergy_cards=max_synergy_cards,
    )
    cardlists = edhrec_payload_service.extract_cardlists(payload)
    category_rows = edhrec_payload_service.map_category_cards(
        cardlists,
        lookup_oracle_id_fn=lookup_oracle_id_fn,
        max_synergy_cards=max_synergy_cards,
    )
    tags = upsert_tags_fn(edhrec_payload_service.normalize_tag_candidates(raw_json))
    commander_type_rows = edhrec_payload_service.extract_type_distribution_from_sources(payload, raw_json)
    return {
        "synergy_rows": synergy_rows,
        "category_rows": category_rows,
        "tags": tags,
        "commander_type_rows": commander_type_rows,
    }


def fetch_tag_rows(
    session: requests.Session,
    *,
    target_name: str,
    tag_names: list[str],
    slug_base: str,
    last_request_at: float,
    interval_seconds: float,
    lookup_oracle_id_fn: Callable[[str], str | None],
    max_synergy_cards: int | None,
    fetch_commander_json_fn: Callable[[requests.Session, str], tuple[dict | None, dict | None, str | None]] = fetch_commander_json,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    logger = logger or _LOG
    tag_card_rows: dict[str, list[dict]] = {}
    tag_category_rows: dict[str, list[dict]] = {}
    tag_type_rows: dict[str, list[dict]] = {}
    tag_cards_added = 0

    for tag in tag_names:
        tag_slug = slugify_theme(tag)
        if not tag_slug:
            continue
        last_request_at = rate_limit(last_request_at, interval_seconds=interval_seconds)
        tag_url = f"https://edhrec.com/commanders/{slug_base}/{tag_slug}"
        tag_payload, tag_raw_json, tag_error = fetch_commander_json_fn(session, tag_url)
        if tag_error:
            if tag_error == "Commander page not found.":
                logger.info("EDHREC tag page not found for %s (%s).", target_name, tag)
            else:
                logger.warning("EDHREC tag fetch failed for %s (%s): %s", target_name, tag, tag_error)
            continue
        if not tag_payload:
            logger.warning("EDHREC tag payload missing for %s (%s).", target_name, tag)
            continue
        tag_views = edhrec_payload_service.extract_cardviews(tag_payload)
        tag_rows = edhrec_payload_service.map_synergy_cards(
            tag_views,
            lookup_oracle_id_fn=lookup_oracle_id_fn,
            max_synergy_cards=max_synergy_cards,
        )
        tag_cardlists = edhrec_payload_service.extract_cardlists(tag_payload)
        tag_category = edhrec_payload_service.map_category_cards(
            tag_cardlists,
            lookup_oracle_id_fn=lookup_oracle_id_fn,
            max_synergy_cards=max_synergy_cards,
        )
        tag_type_dist = edhrec_payload_service.extract_type_distribution_from_sources(tag_payload, tag_raw_json)
        if tag_rows:
            tag_card_rows[tag] = tag_rows
            tag_cards_added += len(tag_rows)
        if tag_category:
            tag_category_rows[tag] = tag_category
        if tag_type_dist:
            tag_type_rows[tag] = tag_type_dist

    return {
        "tag_card_rows": tag_card_rows,
        "tag_category_rows": tag_category_rows,
        "tag_type_rows": tag_type_rows,
        "tag_cards_added": tag_cards_added,
        "last_request_at": last_request_at,
    }


__all__ = [
    "build_commander_rows",
    "build_edhrec_session",
    "fetch_commander_bundle",
    "fetch_commander_json",
    "fetch_primary_commander_payload",
    "fetch_tag_rows",
    "filter_slug_candidates",
    "rate_limit",
]
