"""Client helpers for the EDHREC microservice."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import time
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

import requests

__all__ = [
    "CardView",
    "EdhrecError",
    "slugify_commander",
    "slugify_theme",
    "normalize_card_key",
    "ensure_commander_data",
    "ensure_theme_data",
    "commander_cardviews",
    "theme_cardviews",
    "merge_cardviews",
    "edhrec_index",
    "edhrec_cache_snapshot",
    "edhrec_service_enabled",
    "refresh_edhrec_cache",
]


class EdhrecError(RuntimeError):
    """Raised when EDHREC service requests fail."""


@dataclass(frozen=True)
class CardView:
    name: str
    slug: str
    category: str
    rank: int
    source_kind: str
    synergy: Optional[float]
    inclusion: Optional[float]
    num_decks: Optional[int]
    potential_decks: Optional[int]
    url: Optional[str]
    label: Optional[str]
    trend_zscore: Optional[float]
    source: str
    source_label: str
    tag: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "slug": self.slug,
            "category": self.category,
            "rank": self.rank,
            "source": self.source,
            "source_label": self.source_label,
            "source_kind": self.source_kind,
        }
        if self.tag:
            payload["tag"] = self.tag
        if self.synergy is not None:
            payload["synergy"] = self.synergy
        if self.inclusion is not None:
            payload["inclusion"] = self.inclusion
        if self.num_decks is not None:
            payload["num_decks"] = self.num_decks
        if self.potential_decks is not None:
            payload["potential_decks"] = self.potential_decks
        if self.url:
            payload["url"] = self.url
        if self.label:
            payload["label"] = self.label
        if self.trend_zscore is not None:
            payload["trend_zscore"] = self.trend_zscore
        return payload


_EDHREC_SERVICE_CACHE: dict[tuple, tuple[float, Dict[str, Any]]] = {}


def _edhrec_service_url() -> str:
    return (os.getenv("EDHREC_SERVICE_URL") or "").strip().rstrip("/")


def _edhrec_service_timeout() -> float:
    raw = os.getenv("EDHREC_SERVICE_HTTP_TIMEOUT", "5")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 5.0


def _edhrec_service_cache_ttl() -> int:
    raw = os.getenv("EDHREC_SERVICE_CACHE_TTL", "600")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 600


def edhrec_service_enabled() -> bool:
    return bool(_edhrec_service_url())


def _cache_get(key: tuple) -> Optional[Dict[str, Any]]:
    ttl = _edhrec_service_cache_ttl()
    if ttl <= 0:
        return None
    entry = _EDHREC_SERVICE_CACHE.get(key)
    if not entry:
        return None
    ts, payload = entry
    if (time.time() - ts) > ttl:
        _EDHREC_SERVICE_CACHE.pop(key, None)
        return None
    return payload


def _cache_set(key: tuple, payload: Dict[str, Any]) -> None:
    ttl = _edhrec_service_cache_ttl()
    if ttl <= 0:
        return
    _EDHREC_SERVICE_CACHE[key] = (time.time(), payload)


def _slugify(value: str, *, plus_as_word: bool = False) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.replace("//", " ")
    text = text.replace("/", " ")
    text = text.replace("&", " and ")
    text = text.replace("@", " at ")
    if plus_as_word:
        text = text.replace("+", " plus ")
    else:
        text = text.replace("+", " ")
    text = text.replace("'", "")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def slugify_commander(name: str) -> str:
    return _slugify(name, plus_as_word=True)


def slugify_theme(tag: str) -> str:
    return _slugify(tag, plus_as_word=True)


def normalize_card_key(name: str) -> str:
    return _slugify(name, plus_as_word=True)


def _service_request(method: str, path: str, *, params: dict | None = None, json_payload: dict | None = None) -> dict:
    base_url = _edhrec_service_url()
    if not base_url:
        raise EdhrecError("EDHREC service is not configured.")
    url = f"{base_url}{path}"
    try:
        if method == "POST":
            response = requests.post(url, json=json_payload or {}, timeout=_edhrec_service_timeout())
        else:
            response = requests.get(url, params=params or {}, timeout=_edhrec_service_timeout())
    except requests.RequestException as exc:
        raise EdhrecError(f"EDHREC service unavailable: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise EdhrecError(f"EDHREC service returned invalid JSON ({response.status_code}).") from exc

    if response.status_code >= 400:
        raise EdhrecError(payload.get("error") or f"EDHREC service HTTP {response.status_code}.")

    return payload


def ensure_commander_data(
    commander_name: str,
    *,
    theme_slug: Optional[str] = None,
    force_refresh: bool = False,
    max_age_hours: Optional[int] = None,
    slug_override: Optional[str] = None,
) -> tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    if not commander_name:
        return None, None, "Commander name is missing."
    slug_override = (slug_override or "").strip()
    if slug_override:
        slug = slug_override.lower()
    else:
        slug_source = commander_name.partition("//")[0].strip()
        slug = slugify_commander(slug_source or commander_name)
    if not slug:
        return None, None, f"Unable to derive EDHREC slug for '{commander_name}'."

    cache_key = ("commander", slug, theme_slug or "")
    if not force_refresh:
        cached = _cache_get(cache_key)
        if cached:
            return slug, cached, None

    payload = {
        "name": commander_name,
        "slug": slug,
        "theme": theme_slug,
        "force": bool(force_refresh),
    }
    if isinstance(max_age_hours, int):
        payload["max_age_hours"] = max_age_hours

    try:
        response = _service_request("POST", "/v1/edhrec/commanders", json_payload=payload)
    except EdhrecError as exc:
        return slug, None, str(exc)

    if response.get("status") != "ok":
        return slug, None, response.get("error") or "EDHREC service error."

    data = response.get("payload") or {}
    if data:
        _cache_set(cache_key, data)
    warning = response.get("warning") if response.get("stale") else None
    return slug, data, warning


def ensure_theme_data(
    theme_name: str,
    *,
    force_refresh: bool = False,
    max_age_hours: Optional[int] = None,
) -> tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    if not theme_name:
        return None, None, "Theme name is missing."
    slug = slugify_theme(theme_name)
    if not slug:
        return None, None, f"Unable to derive EDHREC slug for '{theme_name}'."

    cache_key = ("theme", slug)
    if not force_refresh:
        cached = _cache_get(cache_key)
        if cached:
            return slug, cached, None

    payload = {
        "name": theme_name,
        "slug": slug,
        "force": bool(force_refresh),
    }
    if isinstance(max_age_hours, int):
        payload["max_age_hours"] = max_age_hours

    try:
        response = _service_request("POST", "/v1/edhrec/themes", json_payload=payload)
    except EdhrecError as exc:
        return slug, None, str(exc)

    if response.get("status") != "ok":
        return slug, None, response.get("error") or "EDHREC service error."

    data = response.get("payload") or {}
    if data:
        _cache_set(cache_key, data)
    warning = response.get("warning") if response.get("stale") else None
    return slug, data, warning


def _cardviews_from_payload(
    payload: Optional[Dict[str, Any]],
    *,
    categories: Optional[Iterable[str]] = None,
) -> List[CardView]:
    if not payload:
        return []
    cardlists = payload.get("cardlists") or []
    if not isinstance(cardlists, list):
        return []
    category_filter = None
    if categories:
        category_filter = {c.lower() for c in categories}
    collected: List[CardView] = []
    for entry in cardlists:
        if not isinstance(entry, dict):
            continue
        header = entry.get("header")
        if not isinstance(header, str):
            continue
        if category_filter and header.lower() not in category_filter:
            continue
        tag = entry.get("tag")
        for raw in entry.get("cardviews") or []:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            slug = raw.get("slug") or normalize_card_key(name or "")
            if not name or not slug:
                continue
            collected.append(
                CardView(
                    name=name,
                    slug=slug,
                    category=header,
                    rank=int(raw.get("rank") or len(collected) + 1),
                    source_kind=str(payload.get("kind") or ""),
                    synergy=_safe_float(raw.get("synergy")),
                    inclusion=_inclusion_percent(
                        raw.get("inclusion"),
                        raw.get("num_decks"),
                        raw.get("potential_decks"),
                    ),
                    num_decks=raw.get("num_decks"),
                    potential_decks=raw.get("potential_decks"),
                    url=_full_edhrec_url(raw.get("url")),
                    label=raw.get("label"),
                    trend_zscore=_safe_float(raw.get("trend_zscore")),
                    source=str(payload.get("slug") or ""),
                    source_label=str(payload.get("name") or payload.get("slug") or ""),
                    tag=tag if isinstance(tag, str) else None,
                )
            )
    return collected


def commander_cardviews(
    payload: Optional[Dict[str, Any]],
    *,
    categories: Optional[Iterable[str]] = None,
) -> List[CardView]:
    return _cardviews_from_payload(payload, categories=categories)


def theme_cardviews(
    payload: Optional[Dict[str, Any]],
    *,
    categories: Optional[Iterable[str]] = None,
) -> List[CardView]:
    return _cardviews_from_payload(payload, categories=categories)


def merge_cardviews(*collections: Iterable[CardView]) -> Dict[str, CardView]:
    merged: Dict[str, CardView] = {}
    for collection in collections:
        for view in collection:
            existing = merged.get(view.slug)
            if existing is None:
                merged[view.slug] = view
                continue
            if (view.synergy or 0) > (existing.synergy or 0):
                merged[view.slug] = view
            elif view.synergy == existing.synergy and (view.inclusion or 0) > (existing.inclusion or 0):
                merged[view.slug] = view
    return merged


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _inclusion_percent(
    raw_inclusion: Any,
    raw_num_decks: Any,
    raw_potential_decks: Any,
) -> Optional[float]:
    inclusion = _safe_float(raw_inclusion)
    num_decks = _safe_float(raw_num_decks)
    potential_decks = _safe_float(raw_potential_decks)
    if potential_decks and potential_decks > 0:
        numerator = num_decks if num_decks is not None else inclusion
        if numerator is not None:
            pct = (numerator / potential_decks) * 100.0
            return round(min(max(pct, 0.0), 100.0), 1)
    if inclusion is None:
        return None
    if inclusion <= 1:
        return round(min(max(inclusion * 100.0, 0.0), 100.0), 1)
    return round(min(max(inclusion, 0.0), 100.0), 1)


def _full_edhrec_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if not url.startswith("/"):
        url = f"/{url}"
    return f"https://edhrec.com{url}"


def edhrec_index(
    *,
    include_commanders: bool = True,
    include_themes: bool = True,
    max_pages: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    params = {
        "commanders": int(bool(include_commanders)),
        "themes": int(bool(include_themes)),
    }
    if isinstance(max_pages, int) and max_pages > 0:
        params["max_pages"] = max_pages
    if isinstance(limit, int) and limit > 0:
        params["limit"] = limit

    response = _service_request("GET", "/v1/edhrec/index", params=params)
    if response.get("status") != "ok":
        raise EdhrecError(response.get("error") or "EDHREC index lookup failed.")
    return response


def edhrec_cache_snapshot() -> Dict[str, Any]:
    try:
        response = _service_request("GET", "/v1/edhrec/stats")
    except EdhrecError as exc:
        return {"status": "error", "error": str(exc)}
    if response.get("status") != "ok":
        return {"status": "error", "error": response.get("error") or "EDHREC service error."}
    return response


def refresh_edhrec_cache(
    *,
    commanders: List[str],
    themes: List[str],
    force_refresh: bool = False,
) -> Dict[str, Any]:
    payload = {
        "commanders": commanders,
        "themes": themes,
        "force": bool(force_refresh),
    }
    try:
        response = _service_request("POST", "/v1/edhrec/refresh", json_payload=payload)
    except EdhrecError as exc:
        return {"status": "error", "error": str(exc)}
    return response
