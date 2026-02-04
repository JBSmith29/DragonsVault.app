from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import threading
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request
import requests
from sqlalchemy import func, select

from .config import load_config
from .db import ensure_tables, get_engine, get_session_factory, ping_db
from .edhrec_fetcher import EdhrecError, EdhrecFetcher, slugify_commander, slugify_theme
from .models import EdhrecCommanderCache, EdhrecThemeCache

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _get_lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_fresh(fetched_at: Optional[datetime], max_age_hours: int) -> bool:
    if not fetched_at or max_age_hours <= 0:
        return False
    age = _now_utc() - fetched_at
    return age.total_seconds() < max_age_hours * 3600


def _parse_bool(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: Optional[str], fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_slug(raw: Optional[str]) -> str:
    return (raw or "").strip().lower()


def _normalize_name(raw: Optional[str]) -> Optional[str]:
    name = (raw or "").strip()
    return name or None


def _commander_key(slug: str, theme_slug: Optional[str]) -> str:
    suffix = theme_slug or ""
    return f"commander:{slug}:{suffix}"


def _load_commander(session, slug: str, theme_slug: Optional[str]) -> Optional[EdhrecCommanderCache]:
    stmt = select(EdhrecCommanderCache).where(EdhrecCommanderCache.slug == slug)
    if theme_slug is None:
        stmt = stmt.where(EdhrecCommanderCache.theme_slug.is_(None))
    else:
        stmt = stmt.where(EdhrecCommanderCache.theme_slug == theme_slug)
    return session.execute(stmt).scalar_one_or_none()


def _load_theme(session, slug: str) -> Optional[EdhrecThemeCache]:
    return session.execute(
        select(EdhrecThemeCache).where(EdhrecThemeCache.slug == slug)
    ).scalar_one_or_none()


def _save_commander(
    session,
    record: Optional[EdhrecCommanderCache],
    *,
    slug: str,
    theme_slug: Optional[str],
    name: Optional[str],
    payload: Dict[str, Any],
    source_url: Optional[str],
) -> EdhrecCommanderCache:
    fetched_at = _now_utc()
    if record is None:
        record = EdhrecCommanderCache(
            slug=slug,
            theme_slug=theme_slug,
            name=name,
            payload=payload,
            source_url=source_url,
            fetched_at=fetched_at,
        )
        session.add(record)
    else:
        record.name = name or record.name
        record.payload = payload
        record.source_url = source_url or record.source_url
        record.fetched_at = fetched_at
    return record


def _save_theme(
    session,
    record: Optional[EdhrecThemeCache],
    *,
    slug: str,
    name: Optional[str],
    payload: Dict[str, Any],
    source_url: Optional[str],
) -> EdhrecThemeCache:
    fetched_at = _now_utc()
    if record is None:
        record = EdhrecThemeCache(
            slug=slug,
            name=name,
            payload=payload,
            source_url=source_url,
            fetched_at=fetched_at,
        )
        session.add(record)
    else:
        record.name = name or record.name
        record.payload = payload
        record.source_url = source_url or record.source_url
        record.fetched_at = fetched_at
    return record


def _ensure_commander_payload(
    *,
    session,
    fetcher: EdhrecFetcher,
    slug: str,
    name: Optional[str],
    theme_slug: Optional[str],
    force: bool,
    max_age_hours: int,
) -> Tuple[Dict[str, Any], bool, bool, Optional[str]]:
    record = _load_commander(session, slug, theme_slug)
    if record and record.payload and not force and _is_fresh(record.fetched_at, max_age_hours):
        return record.payload, True, False, None

    lock = _get_lock(_commander_key(slug, theme_slug))
    with lock:
        record = _load_commander(session, slug, theme_slug)
        if record and record.payload and not force and _is_fresh(record.fetched_at, max_age_hours):
            return record.payload, True, False, None
        try:
            result = fetcher.fetch_commander(slug=slug, name=name, theme_slug=theme_slug)
            payload = result.payload
            _save_commander(
                session,
                record,
                slug=slug,
                theme_slug=theme_slug,
                name=name,
                payload=payload,
                source_url=result.url,
            )
            session.commit()
            return payload, False, False, None
        except (EdhrecError, requests.RequestException) as exc:
            session.rollback()
            if record and record.payload:
                return record.payload, True, True, str(exc)
            raise


def _ensure_theme_payload(
    *,
    session,
    fetcher: EdhrecFetcher,
    slug: str,
    name: Optional[str],
    force: bool,
    max_age_hours: int,
) -> Tuple[Dict[str, Any], bool, bool, Optional[str]]:
    record = _load_theme(session, slug)
    if record and record.payload and not force and _is_fresh(record.fetched_at, max_age_hours):
        return record.payload, True, False, None

    lock = _get_lock(f"theme:{slug}")
    with lock:
        record = _load_theme(session, slug)
        if record and record.payload and not force and _is_fresh(record.fetched_at, max_age_hours):
            return record.payload, True, False, None
        try:
            result = fetcher.fetch_theme(slug=slug, name=name)
            payload = result.payload
            _save_theme(
                session,
                record,
                slug=slug,
                name=name,
                payload=payload,
                source_url=result.url,
            )
            session.commit()
            return payload, False, False, None
        except (EdhrecError, requests.RequestException) as exc:
            session.rollback()
            if record and record.payload:
                return record.payload, True, True, str(exc)
            raise


def _summarize_cache(session) -> dict:
    commander_count = session.execute(select(func.count(EdhrecCommanderCache.id))).scalar() or 0
    theme_count = session.execute(select(func.count(EdhrecThemeCache.id))).scalar() or 0
    latest_commander = session.execute(
        select(EdhrecCommanderCache.slug, EdhrecCommanderCache.theme_slug, EdhrecCommanderCache.fetched_at)
        .order_by(EdhrecCommanderCache.fetched_at.desc().nullslast())
        .limit(1)
    ).first()
    latest_theme = session.execute(
        select(EdhrecThemeCache.slug, EdhrecThemeCache.fetched_at)
        .order_by(EdhrecThemeCache.fetched_at.desc().nullslast())
        .limit(1)
    ).first()

    def _commander_label(row):
        if not row:
            return None
        slug, theme_slug, _fetched = row
        if theme_slug:
            return f"{slug}/{theme_slug}"
        return slug

    def _format_ts(row, idx):
        if not row:
            return None
        ts = row[idx]
        return ts.isoformat() if ts else None

    return {
        "commanders": {
            "count": commander_count,
            "latest_slug": _commander_label(latest_commander),
            "latest_fetched_at": _format_ts(latest_commander, 2),
        },
        "themes": {
            "count": theme_count,
            "latest_slug": latest_theme[0] if latest_theme else None,
            "latest_fetched_at": _format_ts(latest_theme, 1),
        },
    }


def _refresh_commander_task(
    *,
    session_factory,
    config,
    slug: str,
    theme_slug: Optional[str],
    name: Optional[str],
    force: bool,
    max_age_hours: int,
) -> dict:
    session = session_factory()
    try:
        fetcher = EdhrecFetcher(config)
        _payload, cache_hit, stale, warning = _ensure_commander_payload(
            session=session,
            fetcher=fetcher,
            slug=slug,
            name=name,
            theme_slug=theme_slug,
            force=force,
            max_age_hours=max_age_hours,
        )
        return {
            "kind": "commander",
            "slug": slug,
            "theme_slug": theme_slug,
            "ok": True,
            "cache_hit": cache_hit,
            "stale": stale,
            "warning": warning,
        }
    except Exception as exc:
        return {
            "kind": "commander",
            "slug": slug,
            "theme_slug": theme_slug,
            "ok": False,
            "error": str(exc),
        }
    finally:
        session.close()


def _refresh_theme_task(
    *,
    session_factory,
    config,
    slug: str,
    name: Optional[str],
    force: bool,
    max_age_hours: int,
) -> dict:
    session = session_factory()
    try:
        fetcher = EdhrecFetcher(config)
        _payload, cache_hit, stale, warning = _ensure_theme_payload(
            session=session,
            fetcher=fetcher,
            slug=slug,
            name=name,
            force=force,
            max_age_hours=max_age_hours,
        )
        return {
            "kind": "theme",
            "slug": slug,
            "ok": True,
            "cache_hit": cache_hit,
            "stale": stale,
            "warning": warning,
        }
    except Exception as exc:
        return {
            "kind": "theme",
            "slug": slug,
            "ok": False,
            "error": str(exc),
        }
    finally:
        session.close()


def create_app() -> Flask:
    config = load_config()
    app = Flask(__name__)
    engine = get_engine(config)
    session_factory = get_session_factory(config)
    fetcher = EdhrecFetcher(config)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service=config.service_name, schema=config.database_schema)

    @app.get("/readyz")
    def readyz():
        try:
            ping_db(engine, config.database_schema)
        except Exception:
            return jsonify(status="error", service=config.service_name), 503
        return jsonify(status="ready", service=config.service_name)

    @app.get("/v1/ping")
    def ping():
        return jsonify(status="ok", service=config.service_name)

    @app.get("/v1/edhrec/commanders/<slug>")
    def commander_detail(slug: str):
        slug = _normalize_slug(slug)
        if not slug:
            return jsonify(status="error", error="missing_slug"), 400
        theme_slug = _normalize_slug(request.args.get("theme") or request.args.get("theme_slug")) or None
        name = _normalize_name(request.args.get("name"))
        force = _parse_bool(request.args.get("force"))
        max_age = _parse_int(request.args.get("max_age_hours"), config.cache_ttl_hours)

        session = session_factory()
        try:
            ensure_tables(engine)
            payload, cache_hit, stale, warning = _ensure_commander_payload(
                session=session,
                fetcher=fetcher,
                slug=slug,
                name=name,
                theme_slug=theme_slug,
                force=force,
                max_age_hours=max_age,
            )
            response = {
                "status": "ok",
                "slug": slug,
                "theme_slug": theme_slug,
                "payload": payload,
                "cache_hit": cache_hit,
                "stale": stale,
            }
            if warning:
                response["warning"] = warning
            return jsonify(response)
        except EdhrecError as exc:
            return jsonify(status="error", error=str(exc)), 502
        except Exception as exc:
            return jsonify(status="error", error=str(exc)), 500
        finally:
            session.close()

    @app.post("/v1/edhrec/commanders")
    def commander_by_name():
        payload = request.get_json(silent=True) or {}
        name = _normalize_name(payload.get("name"))
        if not name:
            return jsonify(status="error", error="missing_name"), 400
        theme = _normalize_slug(payload.get("theme") or payload.get("theme_slug")) or None
        slug = _normalize_slug(payload.get("slug")) or slugify_commander(name)
        if not slug:
            return jsonify(status="error", error="unable_to_slugify"), 400
        force = bool(payload.get("force"))
        max_age = payload.get("max_age_hours")
        max_age = max_age if isinstance(max_age, int) else _parse_int(str(max_age), config.cache_ttl_hours)

        session = session_factory()
        try:
            ensure_tables(engine)
            result_payload, cache_hit, stale, warning = _ensure_commander_payload(
                session=session,
                fetcher=fetcher,
                slug=slug,
                name=name,
                theme_slug=theme,
                force=force,
                max_age_hours=max_age,
            )
            response = {
                "status": "ok",
                "slug": slug,
                "theme_slug": theme,
                "payload": result_payload,
                "cache_hit": cache_hit,
                "stale": stale,
            }
            if warning:
                response["warning"] = warning
            return jsonify(response)
        except EdhrecError as exc:
            return jsonify(status="error", error=str(exc)), 502
        except Exception as exc:
            return jsonify(status="error", error=str(exc)), 500
        finally:
            session.close()

    @app.get("/v1/edhrec/themes/<slug>")
    def theme_detail(slug: str):
        slug = _normalize_slug(slug)
        if not slug:
            return jsonify(status="error", error="missing_slug"), 400
        name = _normalize_name(request.args.get("name"))
        force = _parse_bool(request.args.get("force"))
        max_age = _parse_int(request.args.get("max_age_hours"), config.cache_ttl_hours)

        session = session_factory()
        try:
            ensure_tables(engine)
            result_payload, cache_hit, stale, warning = _ensure_theme_payload(
                session=session,
                fetcher=fetcher,
                slug=slug,
                name=name,
                force=force,
                max_age_hours=max_age,
            )
            response = {
                "status": "ok",
                "slug": slug,
                "payload": result_payload,
                "cache_hit": cache_hit,
                "stale": stale,
            }
            if warning:
                response["warning"] = warning
            return jsonify(response)
        except EdhrecError as exc:
            return jsonify(status="error", error=str(exc)), 502
        except Exception as exc:
            return jsonify(status="error", error=str(exc)), 500
        finally:
            session.close()

    @app.post("/v1/edhrec/themes")
    def theme_by_name():
        payload = request.get_json(silent=True) or {}
        name = _normalize_name(payload.get("name"))
        if not name:
            return jsonify(status="error", error="missing_name"), 400
        slug = _normalize_slug(payload.get("slug")) or slugify_theme(name)
        if not slug:
            return jsonify(status="error", error="unable_to_slugify"), 400
        force = bool(payload.get("force"))
        max_age = payload.get("max_age_hours")
        max_age = max_age if isinstance(max_age, int) else _parse_int(str(max_age), config.cache_ttl_hours)

        session = session_factory()
        try:
            ensure_tables(engine)
            result_payload, cache_hit, stale, warning = _ensure_theme_payload(
                session=session,
                fetcher=fetcher,
                slug=slug,
                name=name,
                force=force,
                max_age_hours=max_age,
            )
            response = {
                "status": "ok",
                "slug": slug,
                "payload": result_payload,
                "cache_hit": cache_hit,
                "stale": stale,
            }
            if warning:
                response["warning"] = warning
            return jsonify(response)
        except EdhrecError as exc:
            return jsonify(status="error", error=str(exc)), 502
        except Exception as exc:
            return jsonify(status="error", error=str(exc)), 500
        finally:
            session.close()

    @app.get("/v1/edhrec/stats")
    def edhrec_stats():
        session = session_factory()
        try:
            ensure_tables(engine)
            payload = _summarize_cache(session)
            return jsonify(status="ok", **payload)
        except Exception as exc:
            return jsonify(status="error", error=str(exc)), 500
        finally:
            session.close()

    @app.get("/v1/edhrec/index")
    def edhrec_index():
        include_commanders = _parse_bool(request.args.get("commanders", "1"))
        include_themes = _parse_bool(request.args.get("themes", "1"))
        max_pages = _parse_int(request.args.get("max_pages"), 0)
        max_pages = max_pages if max_pages > 0 else None
        limit = _parse_int(request.args.get("limit"), 0)
        limit = limit if limit > 0 else None

        try:
            commanders = (
                fetcher.fetch_commander_index(max_pages=max_pages)
                if include_commanders
                else []
            )
            themes = (
                fetcher.fetch_theme_index(max_pages=max_pages)
                if include_themes
                else []
            )
        except Exception as exc:
            return jsonify(status="error", error=str(exc)), 500

        if limit:
            commanders = commanders[:limit]
            themes = themes[:limit]

        return jsonify(status="ok", commanders=commanders, themes=themes)

    @app.post("/v1/edhrec/refresh")
    def edhrec_refresh():
        payload = request.get_json(silent=True) or {}
        force = bool(payload.get("force"))
        max_age = payload.get("max_age_hours")
        max_age = max_age if isinstance(max_age, int) else _parse_int(str(max_age), config.cache_ttl_hours)
        commander_entries = payload.get("commanders") or []
        theme_entries = payload.get("themes") or []

        def _parse_commander(entry) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
            if isinstance(entry, str):
                name = _normalize_name(entry)
                if not name:
                    return None
                return slugify_commander(name), None, name
            if isinstance(entry, dict):
                name = _normalize_name(entry.get("name"))
                slug = _normalize_slug(entry.get("slug"))
                if not slug and name:
                    slug = slugify_commander(name)
                if not slug:
                    return None
                theme_slug = _normalize_slug(entry.get("theme") or entry.get("theme_slug")) or None
                return slug, theme_slug, name
            return None

        def _parse_theme(entry) -> Optional[Tuple[str, Optional[str]]]:
            if isinstance(entry, str):
                name = _normalize_name(entry)
                if not name:
                    return None
                return slugify_theme(name), name
            if isinstance(entry, dict):
                name = _normalize_name(entry.get("name"))
                slug = _normalize_slug(entry.get("slug"))
                if not slug and name:
                    slug = slugify_theme(name)
                if not slug:
                    return None
                return slug, name
            return None

        commander_targets: List[Tuple[str, Optional[str], Optional[str]]] = []
        for entry in commander_entries:
            parsed = _parse_commander(entry)
            if parsed:
                commander_targets.append(parsed)

        theme_targets: List[Tuple[str, Optional[str]]] = []
        for entry in theme_entries:
            parsed = _parse_theme(entry)
            if parsed:
                theme_targets.append(parsed)

        commander_seen = set()
        commander_targets = [
            item for item in commander_targets
            if not ((item[0], item[1]) in commander_seen or commander_seen.add((item[0], item[1])))
        ]
        theme_seen = set()
        theme_targets = [
            item for item in theme_targets
            if not (item[0] in theme_seen or theme_seen.add(item[0]))
        ]

        ensure_tables(engine)
        results: dict[str, Any] = {"commanders": [], "themes": []}
        errors: List[str] = []
        max_workers = max(1, int(config.refresh_concurrency))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for slug, theme_slug, name in commander_targets:
                futures.append(
                    executor.submit(
                        _refresh_commander_task,
                        session_factory=session_factory,
                        config=config,
                        slug=slug,
                        theme_slug=theme_slug,
                        name=name,
                        force=force,
                        max_age_hours=max_age,
                    )
                )
            for slug, name in theme_targets:
                futures.append(
                    executor.submit(
                        _refresh_theme_task,
                        session_factory=session_factory,
                        config=config,
                        slug=slug,
                        name=name,
                        force=force,
                        max_age_hours=max_age,
                    )
                )
            for future in as_completed(futures):
                result = future.result()
                if result.get("kind") == "commander":
                    results["commanders"].append(result)
                else:
                    results["themes"].append(result)
                if not result.get("ok"):
                    errors.append(result.get("error") or "unknown_error")

        commanders_ok = sum(1 for item in results["commanders"] if item.get("ok"))
        themes_ok = sum(1 for item in results["themes"] if item.get("ok"))
        status = "success" if not errors else "warning"
        return jsonify(
            status=status,
            commanders={
                "requested": len(commander_targets),
                "ok": commanders_ok,
                "errors": len(results["commanders"]) - commanders_ok,
            },
            themes={
                "requested": len(theme_targets),
                "ok": themes_ok,
                "errors": len(results["themes"]) - themes_ok,
            },
            errors=errors[:10],
        )

    return app
