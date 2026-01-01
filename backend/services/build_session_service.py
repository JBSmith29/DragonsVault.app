"""Build session workflow helpers (proxy-only)."""

from __future__ import annotations

import logging
import re
from typing import Iterable

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from sqlalchemy import inspect, text

from extensions import db
from models import BuildSession, BuildSessionCard, OracleCoreRoleTag
from services import scryfall_cache as sc
from services.build_recommendation_service import build_recommendation_sections
from services.deck_tags import get_deck_tag_groups
from services.edhrec_cache_service import get_commander_category_groups, get_commander_synergy
from services.edhrec.edhrec_ingestion_service import ingest_commander_tag_data
from services.symbols_cache import colors_to_icons

_LOG = logging.getLogger(__name__)


def build_session_page(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)

    tags = _normalized_tags(session.tags_json)
    commander = _oracle_payload(session.commander_oracle_id, fallback=session.commander_name)
    cards = _session_cards(session.cards or [])
    metrics = _deck_metrics(session.cards or [])
    deck_type_breakdown = _type_breakdown_for_entries(session.cards or [])
    edhrec_type_breakdown = _edhrec_type_breakdown(
        session.commander_oracle_id or "",
        tags,
    )
    sort_mode = _normalize_sort_mode(request.args.get("sort"))
    tag_groups = get_deck_tag_groups()
    recommendations = build_recommendation_sections(
        session.commander_oracle_id or "",
        tags,
        role_needs=metrics["role_needs"],
        sort_mode=sort_mode,
    )

    return render_template(
        "decks/build_session.html",
        build_session=session,
        commander=commander,
        tags=tags,
        tag_groups=tag_groups,
        recommendations=recommendations,
        deck_metrics=metrics,
        deck_type_breakdown=deck_type_breakdown,
        edhrec_type_breakdown=edhrec_type_breakdown,
        mana_pip_dist=metrics["mana_pip_dist"],
        land_mana_sources=metrics["land_mana_sources"],
        sort_mode=sort_mode,
        phase=metrics["phase"],
        session_cards=cards,
    )


def start_build_session():
    commander_oracle_id = (request.form.get("commander_oracle_id") or "").strip()
    commander_name = (request.form.get("commander_name") or "").strip()
    build_name = (request.form.get("build_name") or "").strip()
    tags = _normalized_tags(_collect_tags_from_request())
    if not commander_oracle_id and commander_name:
        try:
            sc.ensure_cache_loaded()
            commander_oracle_id = sc.unique_oracle_by_name(commander_name) or ""
        except Exception:
            commander_oracle_id = ""
        if commander_oracle_id:
            commander_name = _oracle_name(commander_oracle_id) or commander_name
    if not commander_oracle_id:
        flash("Commander not found. Please check the name and try again.", "warning")
        return redirect(url_for("views.build_landing"))

    _ensure_build_session_tables()

    commander_name = commander_name or _oracle_name(commander_oracle_id)
    session = BuildSession(
        owner_user_id=current_user.id,
        commander_oracle_id=commander_oracle_id,
        commander_name=commander_name,
        build_name=build_name or None,
        tags_json=tags or None,
    )
    db.session.add(session)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _LOG.error("Failed to create build session: %s", exc)
        flash("Unable to start build session. Please try again.", "danger")
        return redirect(url_for("views.build_landing"))

    return redirect(url_for("views.build_session", session_id=session.id))


def add_card(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    oracle_id = (request.form.get("card_oracle_id") or "").strip()
    if not oracle_id:
        return redirect(url_for("views.build_session", session_id=session_id))

    entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
    if entry:
        entry.quantity = int(entry.quantity or 0) + 1
    else:
        entry = BuildSessionCard(session_id=session.id, card_oracle_id=oracle_id, quantity=1)
        db.session.add(entry)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to add card to the build session.", "danger")
    return redirect(url_for("views.build_session", session_id=session_id))


def add_cards_bulk(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    oracle_ids = [oid.strip() for oid in request.form.getlist("card_oracle_id") if oid]
    if not oracle_ids:
        flash("No cards selected to add.", "warning")
        return redirect(url_for("views.build_session", session_id=session_id))

    unique_oracles = []
    seen: set[str] = set()
    for oracle_id in oracle_ids:
        key = oracle_id.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_oracles.append(oracle_id)

    for oracle_id in unique_oracles:
        entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
        if entry:
            entry.quantity = int(entry.quantity or 0) + 1
        else:
            db.session.add(BuildSessionCard(session_id=session.id, card_oracle_id=oracle_id, quantity=1))
    try:
        db.session.commit()
        flash(f"Added {len(unique_oracles)} cards to the build.", "success")
    except Exception:
        db.session.rollback()
        flash("Unable to add cards to the build session.", "danger")
    return redirect(url_for("views.build_session", session_id=session_id))


def remove_card(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    oracle_id = (request.form.get("card_oracle_id") or "").strip()
    if not oracle_id:
        return redirect(url_for("views.build_session", session_id=session_id))

    entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
    if not entry:
        return redirect(url_for("views.build_session", session_id=session_id))
    if (entry.quantity or 0) > 1:
        entry.quantity = int(entry.quantity or 0) - 1
    else:
        db.session.delete(entry)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to remove card from the build session.", "danger")
    return redirect(url_for("views.build_session", session_id=session_id))


def update_quantity(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    oracle_id = (request.form.get("card_oracle_id") or "").strip()
    if not oracle_id:
        return redirect(url_for("views.build_session", session_id=session_id))
    try:
        quantity = int(request.form.get("quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0

    entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
    if not entry:
        return redirect(url_for("views.build_session", session_id=session_id))

    if quantity <= 0:
        db.session.delete(entry)
    else:
        entry.quantity = quantity
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to update card quantity.", "danger")
    return redirect(url_for("views.build_session", session_id=session_id))


def update_tags(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    tags = _normalized_tags(_collect_tags_from_request())
    session.tags_json = tags or None
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to update tags for this build session.", "danger")
    return redirect(url_for("views.build_session", session_id=session_id))


def update_name(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    build_name = (request.form.get("build_name") or "").strip()
    session.build_name = build_name or None
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _LOG.error("Failed to update build session name: %s", exc)
        flash("Unable to update build name. Please try again.", "danger")
    return redirect(url_for("views.build_session", session_id=session_id))


def delete_session(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    db.session.delete(session)
    try:
        db.session.commit()
        flash("Build session deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        _LOG.error("Failed to delete build session: %s", exc)
        flash("Unable to delete build session. Please try again.", "danger")
    return redirect(url_for("views.build_landing"))


def refresh_edhrec(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    commander_oracle_id = (session.commander_oracle_id or "").strip()
    commander_name = (session.commander_name or "").strip()
    if not commander_oracle_id and not commander_name:
        flash("Set a commander before loading EDHREC data.", "warning")
        return redirect(url_for("views.build_session", session_id=session_id))

    tags = _normalized_tags(session.tags_json)
    requested_tag = (request.form.get("deck_tag") or "").strip()
    if requested_tag:
        tags = [requested_tag]
    if not tags:
        flash("Set at least one deck tag before loading EDHREC data.", "warning")
        return redirect(url_for("views.build_session", session_id=session_id))

    result = ingest_commander_tag_data(
        commander_oracle_id,
        commander_name or None,
        tags[:1],
        force_refresh=True,
    )
    status = result.get("status")
    message = result.get("message") or "EDHREC refresh completed."
    if status == "ok":
        flash(message, "success")
    else:
        flash(message, "danger")
    return redirect(url_for("views.build_session", session_id=session_id))


def _get_session(session_id: int) -> BuildSession | None:
    if not current_user.is_authenticated:
        return None
    return (
        BuildSession.query.filter_by(id=session_id, owner_user_id=current_user.id)
        .first()
    )


def _ensure_build_session_tables() -> None:
    try:
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        missing = []
        if "build_sessions" not in tables:
            missing.append(BuildSession.__table__)
        if "build_session_cards" not in tables:
            missing.append(BuildSessionCard.__table__)
        if missing:
            db.metadata.create_all(db.engine, tables=missing)
        if "build_sessions" in tables:
            columns = {col["name"] for col in inspector.get_columns("build_sessions")}
            if "build_name" not in columns:
                db.session.execute(text("ALTER TABLE build_sessions ADD COLUMN build_name VARCHAR(200)"))
                db.session.commit()
    except Exception as exc:
        _LOG.error("Failed to ensure build session tables: %s", exc)


def ensure_build_session_tables() -> None:
    _ensure_build_session_tables()


def _collect_tags_from_request() -> list[str]:
    tags = []
    for value in request.form.getlist("deck_tags"):
        if value:
            tags.append(value)
    raw_list = (request.form.get("deck_tags_csv") or "").strip()
    if raw_list:
        for entry in raw_list.split(","):
            if entry.strip():
                tags.append(entry.strip())
    single = (request.form.get("deck_tag") or "").strip()
    if single:
        tags.append(single)
    return tags


def _normalized_tags(tags: Iterable[str] | None) -> list[str]:
    if isinstance(tags, str):
        tags = [tags]
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        label = (tag or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(label)
    return normalized


def _oracle_name(oracle_id: str) -> str | None:
    if not oracle_id:
        return None
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return None
    if not prints:
        return None
    return (prints[0].get("name") or "").strip() or None


def _oracle_payload(oracle_id: str | None, *, fallback: str | None = None) -> dict:
    if not oracle_id:
        return {"oracle_id": None, "name": fallback or "", "image": None, "colors": []}
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    name = fallback or oracle_id
    image = None
    colors: list[str] = []
    if prints:
        pr = prints[0]
        name = (pr.get("name") or "").strip() or name
        colors = pr.get("color_identity") or pr.get("colors") or []
        image_uris = pr.get("image_uris") or {}
        if not image_uris:
            faces = pr.get("card_faces") or []
            if faces:
                image_uris = (faces[0] or {}).get("image_uris") or {}
        image = image_uris.get("normal") or image_uris.get("large") or image_uris.get("small")
    return {"oracle_id": oracle_id, "name": name, "image": image, "colors": colors}


def _session_cards(entries: Iterable[BuildSessionCard]) -> list[dict]:
    cards: list[dict] = []
    detail_cache: dict[str, dict] = {}
    for entry in entries:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        payload = _oracle_payload(oracle_id)
        detail = _oracle_detail(oracle_id, detail_cache)
        cmc_raw = detail.get("cmc")
        cmc_val = None
        if cmc_raw is not None:
            try:
                cmc_val = float(cmc_raw)
            except (TypeError, ValueError):
                cmc_val = None
        if cmc_val is None:
            cmc_bucket = ""
        else:
            bucket_val = int(round(cmc_val))
            if bucket_val < 0:
                bucket_val = 0
            cmc_bucket = str(bucket_val) if bucket_val <= 6 else "7+"
        cards.append(
            {
                "oracle_id": oracle_id,
                "name": payload["name"],
                "image": payload["image"],
                "quantity": int(entry.quantity or 0),
                "type_line": detail.get("type_line") or "",
                "cmc_bucket": cmc_bucket,
            }
        )
    cards.sort(key=lambda item: (item["name"].casefold(), item["oracle_id"]))
    return cards


def _deck_metrics(entries: Iterable[BuildSessionCard]) -> dict:
    items = list(entries or [])
    oracle_ids = {entry.card_oracle_id for entry in items if entry.card_oracle_id}
    role_map = _oracle_role_map(oracle_ids)

    total_cards = 0
    land_count = 0
    mana_pip_non_land = {c: 0 for c in ["W", "U", "B", "R", "G"]}
    production_counts = {c: 0 for c in ["W", "U", "B", "R", "G", "C"]}
    curve_buckets = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 0}
    missing_cmc = 0
    role_counts = {key: 0 for key in _ROLE_BUCKETS}
    detail_cache: dict[str, dict] = {}

    for entry in items:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        total_cards += qty
        meta = _oracle_detail(oracle_id, detail_cache)
        type_line = (meta.get("type_line") or "").lower()
        cmc_raw = meta.get("cmc")
        if cmc_raw is None:
            cmc = None
        else:
            try:
                cmc = float(cmc_raw)
            except (TypeError, ValueError):
                cmc = None
        if "land" in type_line:
            land_count += qty
        else:
            for mana_cost in meta.get("mana_costs") or []:
                _add_colored_pips(mana_cost, qty, mana_pip_non_land)
        if meta.get("is_permanent"):
            for ch in _colors_from_oracle_text_add(meta.get("oracle_text") or ""):
                production_counts[ch] += qty
        if "land" not in type_line:
            if cmc is None:
                missing_cmc += qty
            else:
                bucket_val = int(round(cmc))
                if bucket_val < 0:
                    bucket_val = 0
                bucket = str(bucket_val) if bucket_val <= 6 else "7+"
                curve_buckets[bucket] += qty
        roles = role_map.get(oracle_id, set())
        for key, bucket_roles in _ROLE_BUCKETS.items():
            if roles & bucket_roles:
                role_counts[key] += qty

    non_land_count = max(total_cards - land_count, 0)
    deck_health, role_needs = _deck_health(role_counts)
    phase = "exploration" if total_cards < 20 else "refinement"

    curve_buckets["0"] += missing_cmc
    total_curve = sum(curve_buckets.values()) or 1
    curve_rows = []
    for label in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
        count = int(curve_buckets.get(label) or 0)
        pct = int(round(100.0 * count / total_curve)) if total_curve else 0
        curve_rows.append({"label": label, "count": count, "pct": pct})

    return {
        "total_cards": total_cards,
        "land_count": land_count,
        "non_land_count": non_land_count,
        "mana_pip_dist": _mana_pip_dist(mana_pip_non_land),
        "land_mana_sources": _mana_source_dist(production_counts),
        "curve_buckets": curve_buckets,
        "curve_rows": curve_rows,
        "role_counts": role_counts,
        "deck_health": deck_health,
        "role_needs": role_needs,
        "phase": phase,
    }


def _oracle_role_map(oracle_ids: set[str]) -> dict[str, set[str]]:
    if not oracle_ids:
        return {}
    rows = (
        db.session.query(OracleCoreRoleTag.oracle_id, OracleCoreRoleTag.role)
        .filter(OracleCoreRoleTag.oracle_id.in_(oracle_ids))
        .all()
    )
    role_map: dict[str, set[str]] = {}
    for oracle_id, role in rows:
        if not oracle_id or not role:
            continue
        role_map.setdefault(oracle_id, set()).add(str(role))
    return role_map


def _oracle_meta(oracle_id: str) -> dict:
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return {}
    if not prints:
        return {}
    pr = prints[0]
    return {
        "type_line": pr.get("type_line") or "",
        "cmc": pr.get("cmc") or 0.0,
    }


def _oracle_detail(oracle_id: str, cache: dict[str, dict]) -> dict:
    cached = cache.get(oracle_id)
    if cached is not None:
        return cached
    payload = {"type_line": "", "cmc": None, "mana_costs": [], "oracle_text": "", "is_permanent": False}
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        cache[oracle_id] = payload
        return payload
    if not prints:
        cache[oracle_id] = payload
        return payload
    pr = prints[0]
    type_line = pr.get("type_line") or ""
    payload["type_line"] = type_line
    payload["cmc"] = pr.get("cmc")
    payload["mana_costs"] = _mana_costs_from_faces(pr)
    payload["oracle_text"] = _oracle_text_from_faces(pr)
    payload["is_permanent"] = _is_permanent_type(type_line)
    cache[oracle_id] = payload
    return payload


def _mana_costs_from_faces(print_obj: dict) -> list[str]:
    costs: list[str] = []
    mana_cost = print_obj.get("mana_cost")
    if mana_cost:
        costs.append(str(mana_cost))
    faces = print_obj.get("card_faces") or []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_cost = face.get("mana_cost")
        if face_cost:
            costs.append(str(face_cost))
    return [cost for cost in costs if cost]


def _oracle_text_from_faces(print_obj: dict) -> str:
    texts: list[str] = []
    oracle_text = print_obj.get("oracle_text")
    if oracle_text:
        texts.append(str(oracle_text))
    faces = print_obj.get("card_faces") or []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_text = face.get("oracle_text")
        if face_text:
            texts.append(str(face_text))
    return " // ".join([t for t in texts if t])


def _is_permanent_type(type_line: str) -> bool:
    lowered = (type_line or "").lower()
    return any(token in lowered for token in ("land", "creature", "artifact", "enchantment", "planeswalker", "battle"))


def _add_colored_pips(mana_cost: str, qty: int, counts: dict[str, int]) -> None:
    for symbol in RE_COST_SYMBOL.findall(mana_cost or ""):
        token = symbol.upper()
        for ch in ("W", "U", "B", "R", "G"):
            if ch in token:
                counts[ch] += qty


def _colors_from_oracle_text_add(text: str) -> set[str]:
    out: set[str] = set()
    if not text:
        return out
    upper = text.upper()
    if "ADD" not in upper:
        return out
    for sym in RE_COST_SYMBOL.findall(text):
        token = sym.upper()
        for ch in ("W", "U", "B", "R", "G", "C"):
            if ch in token:
                out.add(ch)
    if "ANY COLOR" in upper:
        out.update({"W", "U", "B", "R", "G"})
    return out


def _mana_pip_dist(counts: dict[str, int]) -> list[tuple[str, str | None, int]]:
    dist: list[tuple[str, str | None, int]] = []
    for c in ["W", "U", "B", "R", "G"]:
        value = int(counts.get(c) or 0)
        if value <= 0:
            continue
        icons = colors_to_icons([c], use_local=True)
        dist.append((c, icons[0] if icons else None, value))
    return dist


def _mana_source_dist(counts: dict[str, int]) -> list[tuple[str, str | None, int]]:
    dist: list[tuple[str, str | None, int]] = []
    for c in ["W", "U", "B", "R", "G", "C"]:
        value = int(counts.get(c) or 0)
        if value <= 0:
            continue
        icon = None
        if c in {"W", "U", "B", "R", "G"}:
            icons = colors_to_icons([c], use_local=True)
            icon = icons[0] if icons else None
        dist.append((c, icon, value))
    return dist


def _color_identity_set(oracle_id: str) -> set[str]:
    if not oracle_id:
        return set()
    try:
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return set()
    if not prints:
        return set()
    identity_raw = prints[0].get("color_identity") or prints[0].get("colors") or []
    letters, _ = sc.normalize_color_identity(identity_raw)
    return set(letters)


def _type_breakdown_for_entries(entries: Iterable[BuildSessionCard]) -> list[tuple[str, int]]:
    type_counts = {t: 0 for t in _BASE_TYPES}
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        type_line = (_oracle_meta(oracle_id).get("type_line") or "").lower()
        if not type_line:
            continue
        for t in _BASE_TYPES:
            if t.lower() in type_line:
                type_counts[t] += qty
    return [(t, int(type_counts.get(t, 0))) for t in _BASE_TYPES]


def _edhrec_type_breakdown(
    commander_oracle_id: str,
    tags: list[str] | None,
) -> list[tuple[str, int]]:
    if not commander_oracle_id:
        return []
    tag_label = None
    if tags:
        tag_label = (tags[0] or "").strip() or None
    groups = get_commander_category_groups(
        commander_oracle_id,
        tag=tag_label,
        limit=None,
    )
    if tag_label and not groups:
        groups = get_commander_category_groups(commander_oracle_id, tag=None, limit=None)
    recs = []
    seen_cards: set[str] = set()
    for group in groups or []:
        for card in group.get("cards") or []:
            oracle_id = (card.get("oracle_id") or "").strip()
            if not oracle_id or oracle_id in seen_cards:
                continue
            seen_cards.add(oracle_id)
            recs.append({"oracle_id": oracle_id})
    if not recs:
        recs = get_commander_synergy(
            commander_oracle_id,
            tags,
            prefer_tag_specific=True,
            limit=None,
        )
    if not recs:
        return []
    commander_identity = _color_identity_set(commander_oracle_id)
    type_counts = {t: 0 for t in _BASE_TYPES}
    seen: set[str] = set()
    for rec in recs:
        oracle_id = (rec.get("oracle_id") or "").strip()
        if not oracle_id or oracle_id in seen:
            continue
        seen.add(oracle_id)
        card_identity = _color_identity_set(oracle_id)
        if card_identity and not card_identity.issubset(commander_identity):
            continue
        type_line = (_oracle_meta(oracle_id).get("type_line") or "").lower()
        if not type_line:
            continue
        for t in _BASE_TYPES:
            if t.lower() in type_line:
                type_counts[t] += 1
    return [(t, int(type_counts.get(t, 0))) for t in _BASE_TYPES]


def _curve_bucket(cmc: float) -> str:
    if cmc <= 2:
        return "0-2"
    if cmc <= 4:
        return "3-4"
    if cmc <= 6:
        return "5-6"
    return "7+"


def _deck_health(role_counts: dict) -> tuple[list[dict], set[str]]:
    health: list[dict] = []
    role_needs: set[str] = set()
    for key, config in _ROLE_TARGETS.items():
        count = int(role_counts.get(key, 0))
        target = config["target"]
        status = _status_label(count, target)
        if status == "low":
            role_needs |= config["roles"]
        health.append(
            {
                "key": key,
                "label": config["label"],
                "count": count,
                "target": target,
                "status": status,
            }
        )
    return health, role_needs


def _status_label(count: int, target: int) -> str:
    if target <= 0:
        return "ok"
    if count < max(int(target * 0.7), 1):
        return "low"
    if count > int(target * 1.4):
        return "high"
    return "ok"


def _normalize_sort_mode(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value in {"role", "need"}:
        return value
    return "synergy"


_ROLE_BUCKETS = {
    "ramp": {"Ramp", "Fixing", "Treasure"},
    "draw": {"Draw", "Selection", "Advantage"},
    "interaction": {"Removal", "Wipe", "Counter", "Bounce", "Tax", "Stax", "Hate"},
    "wincon": {"Finisher", "Payoff", "Go Wide", "Go Tall", "Voltron", "Engine"},
}

_ROLE_TARGETS = {
    "ramp": {"label": "ramp", "target": 10, "roles": _ROLE_BUCKETS["ramp"]},
    "draw": {"label": "card draw", "target": 8, "roles": _ROLE_BUCKETS["draw"]},
    "interaction": {"label": "interaction", "target": 8, "roles": _ROLE_BUCKETS["interaction"]},
    "wincon": {"label": "win conditions", "target": 2, "roles": _ROLE_BUCKETS["wincon"]},
}

_BASE_TYPES = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]

RE_COST_SYMBOL = re.compile(r"\{([^}]+)\}")


__all__ = [
    "add_card",
    "add_cards_bulk",
    "build_session_page",
    "remove_card",
    "start_build_session",
    "ensure_build_session_tables",
    "update_tags",
    "update_name",
    "update_quantity",
    "delete_session",
]
