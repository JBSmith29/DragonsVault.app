"""Admin system status and data-operations helpers."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flask import current_app, render_template
from sqlalchemy import func, text

from extensions import db
from models import Folder, FolderRole, SiteRequest
from models.card import Card
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.deck_tags import get_all_deck_tags
from core.domains.decks.services.edhrec_cache_service import edhrec_cache_snapshot
from core.domains.decks.services.edhrec_client import edhrec_service_enabled
from core.domains.decks.services.spellbook_sync import EARLY_MANA_VALUE_THRESHOLD, LATE_MANA_VALUE_THRESHOLD
from shared.events.live_updates import latest_job_events

__all__ = [
    "build_data_ops_context",
    "job_badge",
    "job_last_run",
    "load_symbols_context",
    "render_admin_data_operations",
    "site_request_counts",
]


def site_request_counts() -> dict[str, int]:
    """Summaries for contact submissions (bugs/features)."""
    counts = {status: 0 for status in SiteRequest.STATUSES}
    try:
        rows = (
            db.session.query(SiteRequest.status, func.count(SiteRequest.id))
            .group_by(SiteRequest.status)
            .all()
        )
        for status, total in rows:
            if status in counts:
                counts[status] = total or 0
    except Exception:
        current_app.logger.exception("Failed to load site request counts")
        db.session.rollback()
    counts["total"] = sum(counts.values())
    return counts


def load_symbols_context() -> dict[str, Optional[object]]:
    symbols_json_path: Optional[Path] = None
    symbols_svg_dir: Optional[Path] = None
    try:
        from core.shared.utils.symbols_cache import SYMBOLS_DIR, SYMBOLS_JSON, ensure_symbols_cache

        return {
            "symbols_enabled": True,
            "ensure_symbols_cache": ensure_symbols_cache,
            "symbols_json_path": SYMBOLS_JSON,
            "symbols_svg_dir": SYMBOLS_DIR,
        }
    except Exception:
        return {
            "symbols_enabled": False,
            "ensure_symbols_cache": None,
            "symbols_json_path": symbols_json_path,
            "symbols_svg_dir": symbols_svg_dir,
        }


def build_data_ops_context(
    *,
    symbols_json_path: Optional[Path],
    symbols_svg_dir: Optional[Path],
    symbols_enabled: bool,
) -> dict[str, object]:
    from core.domains.cards.services.scryfall_cache import DEFAULT_PATH, cache_stats, is_stale, rulings_bulk_path

    data_dir = Path(DEFAULT_PATH).parent
    rulings_path = Path(rulings_bulk_path())
    prints_path = Path(DEFAULT_PATH)
    spellbook_path = data_dir / "spellbook_combos.json"

    prints_exists = prints_path.exists()
    prints_size = prints_path.stat().st_size if prints_exists else 0
    prints_mtime = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(prints_path.stat().st_mtime))
        if prints_exists
        else None
    )
    prints_stale = is_stale(prints_path) if prints_exists else False

    rulings_exists = rulings_path.exists()
    rulings_size = rulings_path.stat().st_size if rulings_exists else 0
    rulings_mtime = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rulings_path.stat().st_mtime))
        if rulings_exists
        else None
    )

    spellbook_exists = spellbook_path.exists()
    spellbook_size = spellbook_path.stat().st_size if spellbook_exists else 0
    spellbook_mtime = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(spellbook_path.stat().st_mtime))
        if spellbook_exists
        else None
    )
    spellbook_counts = {"early": 0, "late": 0, "total": 0}
    spellbook_categories = {}
    if spellbook_exists:
        try:
            payload = json.loads(spellbook_path.read_text(encoding="utf-8"))
            spellbook_counts["early"] = len(payload.get("early_game", []))
            spellbook_counts["late"] = len(payload.get("late_game", []))
            spellbook_counts["total"] = spellbook_counts["early"] + spellbook_counts["late"]
            raw_counts = payload.get("counts") or {}
            spellbook_categories = {
                key.replace("category_", ""): value
                for key, value in raw_counts.items()
                if key.startswith("category_")
            }
        except Exception as exc:
            current_app.logger.warning("Failed to read spellbook dataset info: %s", exc)
            spellbook_counts = {"early": 0, "late": 0, "total": 0}
            spellbook_categories = {}

    fallback_svg_dir = Path(current_app.static_folder or "static") / "symbols"
    symbols_stats = {
        "exists": False,
        "path": str(symbols_json_path) if symbols_json_path else "N/A",
        "size": 0,
        "mtime": None,
        "entries": 0,
        "svg_dir": str(symbols_svg_dir or fallback_svg_dir),
        "svg_exists": False,
        "svg_count": 0,
    }
    if symbols_json_path:
        symbols_stats["path"] = str(symbols_json_path)
        if symbols_json_path.exists():
            symbols_stats["exists"] = True
            symbols_stats["size"] = symbols_json_path.stat().st_size
            symbols_stats["mtime"] = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(symbols_json_path.stat().st_mtime),
            )
            try:
                payload = json.loads(symbols_json_path.read_text(encoding="utf-8"))
                symbols_stats["entries"] = len(payload.get("data") or [])
            except Exception as exc:
                current_app.logger.warning("Failed to read symbols dataset info: %s", exc)
    svg_dir = symbols_svg_dir or fallback_svg_dir
    symbols_stats["svg_dir"] = str(svg_dir)
    if svg_dir.exists():
        symbols_stats["svg_exists"] = True
        symbols_stats["svg_count"] = sum(1 for entry in svg_dir.glob("*.svg") if entry.is_file())

    try:
        stats = cache_stats() if hasattr(sc, "cache_stats") else {}
    except Exception as exc:
        current_app.logger.warning("cache_stats unavailable: %s", exc)
        stats = {"prints": None, "unique_oracle": None, "rulings": None}

    return {
        "prints": {
            "exists": prints_exists,
            "path": str(prints_path),
            "size": prints_size,
            "mtime": prints_mtime,
            "stale": prints_stale,
        },
        "rulings": {
            "exists": rulings_exists,
            "path": str(rulings_path),
            "size": rulings_size,
            "mtime": rulings_mtime,
        },
        "spellbook": {
            "exists": spellbook_exists,
            "path": str(spellbook_path),
            "size": spellbook_size,
            "mtime": spellbook_mtime,
            "counts": spellbook_counts,
            "categories": spellbook_categories,
            "early_threshold": EARLY_MANA_VALUE_THRESHOLD,
            "late_threshold": LATE_MANA_VALUE_THRESHOLD,
        },
        "symbols": symbols_stats,
        "stats": stats,
        "symbols_enabled": symbols_enabled,
    }


def format_job_timestamp(raw: str | None) -> str | None:
    if not raw:
        return None
    token = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def job_badge(event_type: str, status: str | None) -> tuple[str, str]:
    normalized = (status or "").strip().lower()
    if event_type == "completed":
        if normalized == "skipped":
            return "Skipped", "secondary"
        if normalized in {"warning", "warn"}:
            return "Warning", "warning"
        if normalized in {"error", "failed"}:
            return "Error", "danger"
        return "Complete", "success"
    if event_type == "failed":
        return "Failed", "danger"
    if event_type == "started":
        return "Running", "warning"
    if event_type == "queued":
        return "Queued", "info"
    return "Unknown", "secondary"


def job_last_run(scope: str, dataset: str | None, fallback_stamp: str | None = None) -> dict[str, str]:
    events = latest_job_events(scope, dataset)
    if not events:
        if fallback_stamp:
            return {"stamp": fallback_stamp, "label": "Cached", "tone": "secondary"}
        return {"stamp": "Never", "label": "Never", "tone": "secondary"}
    chosen = None
    for event in reversed(events):
        if event.get("type") in {"completed", "failed"}:
            chosen = event
            break
    if chosen is None:
        chosen = events[-1]
    event_type = (chosen.get("type") or "unknown").lower()
    status = chosen.get("status") or chosen.get("download_status")
    label, tone = job_badge(event_type, status)
    stamp = format_job_timestamp(chosen.get("recorded_at")) or "Unknown"
    return {"stamp": stamp, "label": label, "tone": tone}


def render_admin_data_operations():
    symbols_context = load_symbols_context()
    data_ops = build_data_ops_context(
        symbols_json_path=symbols_context.get("symbols_json_path"),
        symbols_svg_dir=symbols_context.get("symbols_svg_dir"),
        symbols_enabled=bool(symbols_context.get("symbols_enabled")),
    )

    db_status = {"label": "OK", "tone": "success"}
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        db.session.rollback()
        db_status = {"label": "Warning", "tone": "warning"}

    card_count = db.session.query(func.count(Card.id)).scalar() or 0
    deck_count = (
        db.session.query(Folder.id)
        .filter(Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES)))
        .count()
    )
    last_import = data_ops.get("prints", {}).get("mtime") or "N/A"
    tag_count = len(get_all_deck_tags())
    tag_status = "Loaded" if tag_count else "Missing"

    edhrec_snapshot = edhrec_cache_snapshot() if edhrec_service_enabled() else {"status": "disabled"}
    edhrec_error = None
    if edhrec_snapshot.get("status") == "error":
        edhrec_error = edhrec_snapshot.get("error") or "EDHREC service unavailable."
    elif edhrec_snapshot.get("status") == "disabled":
        edhrec_error = "EDHREC service is not configured."
    edhrec = {
        "enabled": edhrec_service_enabled(),
        "error": edhrec_error,
        "commanders": edhrec_snapshot.get("commanders", {}),
        "themes": edhrec_snapshot.get("tags", {}),
        "metadata": edhrec_snapshot.get("metadata", {}) or {},
    }
    maintenance_runs = {
        "scryfall": job_last_run("scryfall", "default_cards", data_ops.get("prints", {}).get("mtime")),
        "spellbook": job_last_run("spellbook", "spellbook", data_ops.get("spellbook", {}).get("mtime")),
        "fts": job_last_run("search_index", "cards"),
    }
    return render_template(
        "admin/data_operations.html",
        system_health={
            "database": db_status,
            "card_count": card_count,
            "deck_count": deck_count,
            "last_import": last_import,
            "tag_status": tag_status,
            "tag_count": tag_count,
        },
        edhrec=edhrec,
        maintenance_runs=maintenance_runs,
    )
