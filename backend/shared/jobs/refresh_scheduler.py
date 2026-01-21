"""Weekly refresh scheduler for Scryfall, Spellbook, and EDHREC datasets."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo may be unavailable in slim images
    ZoneInfo = None  # type: ignore

from shared.jobs import jobs as job_service

_LOG = logging.getLogger("refresh_scheduler")

_WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_weekday(raw: str | None, default: int = 6) -> int:
    if not raw:
        return default
    token = raw.strip().lower()
    if token.isdigit():
        value = int(token)
        if value == 7:
            value = 0
        if 0 <= value <= 6:
            return (value - 1) % 7
    return _WEEKDAY_ALIASES.get(token, default)


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _parse_dt(raw: str | None, tz) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _most_recent_schedule(now: datetime, target_weekday: int, hour: int, minute: int) -> datetime:
    days_since = (now.weekday() - target_weekday) % 7
    scheduled_date = now.date() - timedelta(days=days_since)
    scheduled = datetime.combine(scheduled_date, dt_time(hour, minute), tzinfo=now.tzinfo)
    if scheduled > now:
        scheduled -= timedelta(days=7)
    return scheduled


def _next_schedule(now: datetime, target_weekday: int, hour: int, minute: int) -> datetime:
    days_ahead = (target_weekday - now.weekday()) % 7
    scheduled_date = now.date() + timedelta(days=days_ahead)
    scheduled = datetime.combine(scheduled_date, dt_time(hour, minute), tzinfo=now.tzinfo)
    if scheduled <= now:
        scheduled += timedelta(days=7)
    return scheduled


def _should_run(now: datetime, last_run: Optional[datetime], target_weekday: int, hour: int, minute: int) -> bool:
    scheduled = _most_recent_schedule(now, target_weekday, hour, minute)
    if now < scheduled:
        return False
    if not last_run:
        return True
    return last_run < scheduled


def _queue_enabled(mode: str) -> bool:
    if mode != "rq":
        return False
    jobs_available = bool(getattr(job_service, "_jobs_available", False))
    if not jobs_available:
        _LOG.warning("RQ unavailable; falling back to inline refreshes.")
    return jobs_available


def _safe_call(label: str, func, *args, **kwargs) -> None:
    try:
        func(*args, **kwargs)
    except Exception as exc:
        _LOG.error("%s refresh failed: %s", label, exc, exc_info=True)


def _run_refreshes(
    *,
    mode: str,
    force_refresh: bool,
    edhrec_scope: str,
    refresh_scryfall: bool,
    refresh_rulings: bool,
    refresh_spellbook: bool,
    refresh_edhrec: bool,
) -> None:
    use_queue = _queue_enabled(mode)
    if use_queue:
        if refresh_scryfall:
            _safe_call("Scryfall default_cards", job_service.enqueue_scryfall_refresh, "default_cards", force_download=force_refresh)
        if refresh_rulings:
            _safe_call("Scryfall rulings", job_service.enqueue_scryfall_refresh, "rulings", force_download=force_refresh)
        if refresh_spellbook:
            _safe_call("Spellbook", job_service.enqueue_spellbook_refresh, force_download=force_refresh)
        if refresh_edhrec:
            _safe_call("EDHREC", job_service.enqueue_edhrec_refresh, force_refresh=force_refresh, scope=edhrec_scope)
        return

    if refresh_scryfall:
        _safe_call("Scryfall default_cards", job_service.run_scryfall_refresh_inline, "default_cards", force_download=force_refresh)
    if refresh_rulings:
        _safe_call("Scryfall rulings", job_service.run_scryfall_refresh_inline, "rulings", force_download=force_refresh)
    if refresh_spellbook:
        _safe_call("Spellbook", job_service.run_spellbook_refresh_inline, force_download=force_refresh)
    if refresh_edhrec:
        _safe_call("EDHREC", job_service.run_edhrec_refresh_inline, force_refresh=force_refresh, scope=edhrec_scope)


def _create_app():
    from app import create_app

    return create_app()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    enabled = _parse_bool(os.getenv("SCHEDULE_REFRESH_ENABLED"), True)
    if not enabled:
        _LOG.info("Refresh scheduler disabled (SCHEDULE_REFRESH_ENABLED=0). Exiting.")
        return

    tz_name = os.getenv("SCHEDULE_REFRESH_TZ", "UTC")
    tz = timezone.utc
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            _LOG.warning("Timezone '%s' unavailable; falling back to UTC.", tz_name)

    weekday = _parse_weekday(os.getenv("SCHEDULE_REFRESH_WEEKDAY", "sunday"), default=6)
    hour = int(os.getenv("SCHEDULE_REFRESH_HOUR", "0"))
    minute = int(os.getenv("SCHEDULE_REFRESH_MINUTE", "0"))
    check_interval = max(15, int(os.getenv("SCHEDULE_REFRESH_CHECK_INTERVAL", "60")))

    mode = (os.getenv("SCHEDULE_REFRESH_MODE", "rq") or "rq").strip().lower()
    force_refresh = _parse_bool(os.getenv("SCHEDULE_REFRESH_FORCE"), False)
    edhrec_scope = (os.getenv("SCHEDULE_EDHREC_SCOPE", "full") or "full").strip().lower()

    refresh_scryfall = _parse_bool(os.getenv("SCHEDULE_REFRESH_SCRYFALL", "1"), True)
    refresh_rulings = _parse_bool(os.getenv("SCHEDULE_REFRESH_SCRYFALL_RULINGS", "1"), True)
    refresh_spellbook = _parse_bool(os.getenv("SCHEDULE_REFRESH_SPELLBOOK", "1"), True)
    refresh_edhrec = _parse_bool(os.getenv("SCHEDULE_REFRESH_EDHREC", "1"), True)

    state_path = Path(os.getenv("SCHEDULE_REFRESH_STATE_FILE", "/app/instance/scheduler_state.json"))

    _LOG.info(
        "Weekly refresh scheduler active (tz=%s weekday=%s time=%02d:%02d mode=%s).",
        tz_name,
        weekday,
        hour,
        minute,
        mode,
    )

    app = _create_app()
    while True:
        now = datetime.now(tz)
        state = _load_state(state_path)
        last_run = _parse_dt(state.get("last_run_at"), tz)
        if _should_run(now, last_run, weekday, hour, minute):
            scheduled_for = _most_recent_schedule(now, weekday, hour, minute)
            _LOG.info("Running weekly refresh cycle (scheduled for %s).", scheduled_for.isoformat())
            with app.app_context():
                _run_refreshes(
                    mode=mode,
                    force_refresh=force_refresh,
                    edhrec_scope=edhrec_scope,
                    refresh_scryfall=refresh_scryfall,
                    refresh_rulings=refresh_rulings,
                    refresh_spellbook=refresh_spellbook,
                    refresh_edhrec=refresh_edhrec,
                )
            state["last_run_at"] = datetime.now(tz).isoformat()
            _save_state(state_path, state)
            next_run = _next_schedule(datetime.now(tz), weekday, hour, minute)
            _LOG.info("Next weekly refresh scheduled for %s.", next_run.isoformat())
        time.sleep(check_interval)


if __name__ == "__main__":
    main()
