from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import ServiceConfig

_NEXT_DATA_RE = re.compile(
    r'__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


class EdhrecError(RuntimeError):
    """Raised when EDHREC data could not be fetched or parsed."""


@dataclass(frozen=True)
class FetchResult:
    payload: Dict[str, Any]
    url: str


def _get_with_backoff(
    session: requests.Session,
    url: str,
    timeout: int,
    *,
    headers: Optional[Dict[str, str]] = None,
    max_attempts: int = 8,
) -> requests.Response:
    request_delay = getattr(session, "edhrec_request_delay", 0.0) or 0.0
    last_response: Optional[requests.Response] = None
    for attempt in range(max_attempts):
        if request_delay > 0:
            time.sleep(request_delay)
        response = session.get(url, timeout=timeout, headers=headers)
        last_response = response
        if response.status_code != 429:
            return response
        retry_after = response.headers.get("Retry-After")
        try:
            sleep_for = int(retry_after) if retry_after else 0
        except (TypeError, ValueError):
            sleep_for = 0
        if sleep_for <= 0:
            sleep_for = min(60, 5 * (attempt + 1))
        time.sleep(sleep_for)
    if last_response is None:
        raise EdhrecError(f"EDHREC request failed for {url}")
    return last_response


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _full_edhrec_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if not url.startswith("/"):
        url = f"/{url}"
    return f"https://edhrec.com{url}"


def _normalise_header(header: Any) -> Optional[str]:
    if isinstance(header, str):
        return header.strip() or None
    if isinstance(header, dict):
        for key in ("title", "label", "text"):
            v = header.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _extract_cardlists(json_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    cardlists: List[Dict[str, Any]] = []
    for entry in json_dict.get("cardlists", []) or []:
        if not isinstance(entry, dict):
            continue
        header = _normalise_header(entry.get("header"))
        cardviews: List[Dict[str, Any]] = []
        for idx, raw_view in enumerate(entry.get("cardviews") or []):
            if not isinstance(raw_view, dict):
                continue
            name = raw_view.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            slug = normalize_card_key(name)
            cardviews.append(
                {
                    "name": name,
                    "slug": slug,
                    "sanitized": raw_view.get("sanitized"),
                    "sanitized_wo": raw_view.get("sanitized_wo"),
                    "url": _full_edhrec_url(raw_view.get("url")),
                    "synergy": _safe_float(raw_view.get("synergy")),
                    "inclusion": _safe_float(raw_view.get("inclusion")),
                    "label": raw_view.get("label"),
                    "num_decks": raw_view.get("num_decks"),
                    "potential_decks": raw_view.get("potential_decks"),
                    "trend_zscore": _safe_float(raw_view.get("trend_zscore")),
                    "rank": idx + 1,
                }
            )
        cardlists.append(
            {
                "header": header,
                "tag": entry.get("tag"),
                "cardviews": cardviews,
            }
        )
    return cardlists


def _extract_panels(raw_panels: Any) -> Dict[str, Any]:
    panels: Dict[str, Any] = {}
    if not isinstance(raw_panels, dict):
        return panels
    piechart = raw_panels.get("piechart")
    if isinstance(piechart, dict):
        content: List[Dict[str, Any]] = []
        for item in piechart.get("content") or []:
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            value = item.get("value")
            if label is None or value is None:
                continue
            try:
                numeric_value = int(round(float(value)))
            except (TypeError, ValueError):
                continue
            entry: Dict[str, Any] = {"label": label, "value": numeric_value}
            if item.get("color"):
                entry["color"] = item["color"]
            content.append(entry)
        if content:
            panels["piechart"] = {
                "title": piechart.get("title"),
                "content": content,
            }
    return panels


def _fetch_panels_from_json_api(session: requests.Session, url: str, timeout: int) -> Dict[str, Any]:
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if not path_parts:
        return {}
    category = path_parts[0]
    slug_parts = path_parts[1:]
    if not slug_parts or category not in {"commanders", "themes"}:
        return {}
    api_path = "/".join(slug_parts)
    json_url = f"https://json.edhrec.com/pages/{category}/{api_path}.json"
    try:
        response = _get_with_backoff(session, json_url, timeout)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        data = response.json()
    except Exception:
        return {}
    return _extract_panels(data.get("panels"))


def _strip_html_tags(raw_html: str) -> str:
    return re.sub(r"<[^>]+>", "", raw_html)


def _extract_theme_options(html: str, commander_slug: Optional[str]) -> List[Dict[str, str]]:
    if not commander_slug or not html:
        return []
    pattern = re.compile(
        rf'<a[^>]+href="/commanders/{re.escape(commander_slug)}/([a-z0-9-]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    options: List[Dict[str, str]] = []
    seen: set[str] = set()
    exclusions = {
        "exhibition",
        "core",
        "upgraded",
        "optimized",
        "cedh",
        "budget",
        "expensive",
        "primer",
        "cheap",
        "precon",
        "preconstructed",
        "average",
        "average-deck",
        "combo",
        "duel",
        "box",
    }
    for match in pattern.finditer(html):
        theme_slug = match.group(1).strip().lower()
        if not theme_slug or theme_slug in exclusions:
            continue
        if theme_slug in seen:
            continue
        raw_label = match.group(2)
        if not isinstance(raw_label, str):
            continue
        label = unescape(_strip_html_tags(raw_label)).strip()
        label = re.sub(r"\s*\(\s*[\d,]+\s+deck[s]?\)\s*$", "", label, flags=re.IGNORECASE)
        label = re.sub(r"\s*\(\s*[\d,]+\s*\)\s*$", "", label)
        label = re.sub(r"(?<=\D)\s*[\d,]+\s*$", "", label)
        if not label:
            label = theme_slug.replace("-", " ").title()
        options.append({"slug": theme_slug, "label": label})
        seen.add(theme_slug)
    return options


def _download_json_dict(
    session: requests.Session,
    url: str,
    timeout: int,
    *,
    commander_slug: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, str]]]:
    response = _get_with_backoff(session, url, timeout)
    if response.status_code == 404:
        raise EdhrecError(f"EDHREC page not found: {url}")
    response.raise_for_status()
    html = response.text
    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise EdhrecError("EDHREC page did not contain Next.js payload.")
    raw = json.loads(match.group(1))
    try:
        container = raw["props"]["pageProps"]["data"]["container"]
    except KeyError as exc:
        raise EdhrecError(f"Unexpected EDHREC payload structure for {url}") from exc
    json_dict = container.get("json_dict")
    if not isinstance(json_dict, dict):
        raise EdhrecError("EDHREC JSON container was not a dictionary.")
    panels = _extract_panels(container.get("panels"))
    if not panels:
        fallback_panels = _fetch_panels_from_json_api(session, url, timeout)
        if fallback_panels:
            panels.update(fallback_panels)
    theme_options = _extract_theme_options(html, commander_slug)
    return json_dict, panels, theme_options


def _download_index_json_dict(
    session: requests.Session,
    url: str,
    timeout: int,
) -> Dict[str, Any]:
    response = _get_with_backoff(session, url, timeout)
    if response.status_code == 404:
        raise EdhrecError(f"EDHREC page not found: {url}")
    response.raise_for_status()
    html = response.text
    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise EdhrecError("EDHREC index page did not contain Next.js payload.")
    raw = json.loads(match.group(1))
    try:
        container = raw["props"]["pageProps"]["data"]["container"]
    except KeyError as exc:
        raise EdhrecError(f"Unexpected EDHREC index payload structure for {url}") from exc
    json_dict = container.get("json_dict")
    if not isinstance(json_dict, dict):
        raise EdhrecError("EDHREC index JSON container was not a dictionary.")
    return json_dict


def _extract_index_cardviews(json_dict: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    cardlists = json_dict.get("cardlists") or []
    cardviews: List[Dict[str, Any]] = []
    more: Optional[str] = None
    for entry in cardlists:
        if not isinstance(entry, dict):
            continue
        raw_views = entry.get("cardviews") or []
        if isinstance(raw_views, list):
            cardviews.extend([view for view in raw_views if isinstance(view, dict)])
        if not more:
            raw_more = entry.get("more")
            if isinstance(raw_more, str) and raw_more.strip():
                more = raw_more.strip()
    return cardviews, more


def _parse_index_slug(url: Any, *, prefixes: Tuple[str, ...]) -> Optional[str]:
    if not isinstance(url, str) or not url.strip():
        return None
    raw = url.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        raw = urlparse(raw).path
    raw = raw.strip("/")
    for prefix in prefixes:
        prefix = prefix.strip("/")
        if raw.startswith(prefix + "/"):
            return raw[len(prefix) + 1 :].strip("/") or None
    if raw:
        return raw.split("/")[-1].strip() or None
    return None


def _extract_index_entries(
    cardviews: List[Dict[str, Any]],
    *,
    prefixes: Tuple[str, ...],
    slugify_fn,
    seen: set[str],
    allow_sanitized: bool = False,
) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    for view in cardviews:
        name = view.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        raw_url = view.get("url")
        slug = _parse_index_slug(raw_url, prefixes=prefixes)
        if not slug and allow_sanitized:
            sanitized = view.get("sanitized")
            if isinstance(sanitized, str) and sanitized.strip():
                slug = sanitized.strip()
        if not slug:
            slug = slugify_fn(name)
        slug = (slug or "").strip().lower()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        entries.append({"slug": slug, "name": name.strip()})
    return entries


def _download_index_page(
    session: requests.Session,
    url: str,
    timeout: int,
    *,
    referer: str,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    headers = {"Referer": referer, "Accept": "application/json"}
    response = _get_with_backoff(session, url, timeout, headers=headers)
    if response.status_code == 404:
        return [], None
    response.raise_for_status()
    data = response.json()
    raw_views = data.get("cardviews") or []
    cardviews = [view for view in raw_views if isinstance(view, dict)]
    raw_more = data.get("more")
    more = raw_more.strip() if isinstance(raw_more, str) and raw_more.strip() else None
    return cardviews, more


def _payload_from_json_dict(
    *,
    kind: str,
    slug: str,
    name: Optional[str],
    url: str,
    json_dict: Dict[str, Any],
    panels: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "kind": kind,
        "slug": slug,
        "name": name,
        "fetched_at": _now_iso(),
        "source_url": url,
        "cardlists": _extract_cardlists(json_dict),
    }
    if panels:
        payload["panels"] = panels
    meta_keys = ("card", "header", "description", "keywords")
    metadata: Dict[str, Any] = {}
    for key in meta_keys:
        value = json_dict.get(key)
        if isinstance(value, (dict, list, str, int, float)) and value not in (None, "", []):
            metadata[key] = value
    if metadata:
        payload["metadata"] = metadata
    return payload


class EdhrecFetcher:
    def __init__(self, config: ServiceConfig) -> None:
        self._timeout = max(1, int(config.request_timeout))
        self._request_delay = max(0.0, float(config.request_delay))
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
                "Connection": "keep-alive",
            }
        )
        self._session.edhrec_request_delay = self._request_delay
        retries = max(0, int(config.http_retries))
        if retries:
            retry = Retry(
                total=retries,
                connect=retries,
                read=retries,
                backoff_factor=0.3,
                status_forcelist=(429, 500, 502, 503, 504),
                raise_on_status=False,
                respect_retry_after_header=True,
                allowed_methods=("GET",),
            )
            adapter = HTTPAdapter(max_retries=retry)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)

    def fetch_commander(
        self,
        *,
        slug: str,
        name: Optional[str],
        theme_slug: Optional[str] = None,
    ) -> FetchResult:
        base = f"https://edhrec.com/commanders/{slug}"
        url = f"{base}/{theme_slug}" if theme_slug else base
        json_dict, panels, theme_options = _download_json_dict(
            self._session,
            url,
            self._timeout,
            commander_slug=slug,
        )
        payload = _payload_from_json_dict(
            kind="commander",
            slug=slug,
            name=name,
            url=url,
            json_dict=json_dict,
            panels=panels,
        )
        payload["theme_options"] = theme_options
        if theme_slug:
            payload["applied_theme"] = theme_slug
        return FetchResult(payload=payload, url=url)

    def fetch_theme(self, *, slug: str, name: Optional[str]) -> FetchResult:
        url = f"https://edhrec.com/themes/{slug}"
        json_dict, panels, _theme_options = _download_json_dict(
            self._session,
            url,
            self._timeout,
            commander_slug=None,
        )
        payload = _payload_from_json_dict(
            kind="theme",
            slug=slug,
            name=name,
            url=url,
            json_dict=json_dict,
            panels=panels,
        )
        return FetchResult(payload=payload, url=url)

    def fetch_commander_index(self, *, max_pages: Optional[int] = None) -> List[Dict[str, str]]:
        base_url = "https://edhrec.com/commanders"
        json_dict = _download_index_json_dict(self._session, base_url, self._timeout)
        cardviews, more = _extract_index_cardviews(json_dict)

        seen: set[str] = set()
        entries = _extract_index_entries(
            cardviews,
            prefixes=("commanders",),
            slugify_fn=slugify_commander,
            seen=seen,
            allow_sanitized=True,
        )

        page_count = 0
        while more:
            page_count += 1
            if max_pages and page_count > max_pages:
                break
            page_url = more
            if not page_url.startswith("http"):
                page_url = f"https://json.edhrec.com/pages/{page_url.lstrip('/')}"
            cardviews, more = _download_index_page(
                self._session,
                page_url,
                self._timeout,
                referer=base_url,
            )
            entries.extend(
                _extract_index_entries(
                    cardviews,
                    prefixes=("commanders",),
                    slugify_fn=slugify_commander,
                    seen=seen,
                    allow_sanitized=True,
                )
            )
            time.sleep(max(0.2, self._request_delay))
        return entries

    def fetch_theme_index(self, *, max_pages: Optional[int] = None) -> List[Dict[str, str]]:
        base_url = "https://edhrec.com/themes"
        json_dict = _download_index_json_dict(self._session, base_url, self._timeout)
        cardviews, more = _extract_index_cardviews(json_dict)

        seen: set[str] = set()
        entries = _extract_index_entries(
            cardviews,
            prefixes=("themes", "tags"),
            slugify_fn=slugify_theme,
            seen=seen,
            allow_sanitized=False,
        )

        page_count = 0
        while more:
            page_count += 1
            if max_pages and page_count > max_pages:
                break
            page_url = more
            if not page_url.startswith("http"):
                page_url = f"https://json.edhrec.com/pages/{page_url.lstrip('/')}"
            cardviews, more = _download_index_page(
                self._session,
                page_url,
                self._timeout,
                referer=base_url,
            )
            entries.extend(
                _extract_index_entries(
                    cardviews,
                    prefixes=("themes", "tags"),
                    slugify_fn=slugify_theme,
                    seen=seen,
                    allow_sanitized=False,
                )
            )
            time.sleep(max(0.2, self._request_delay))
        return entries
