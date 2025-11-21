"""List checker utilities for comparing pasted card lists against the collection."""

from __future__ import annotations

import csv
import re
from collections import OrderedDict, defaultdict
from io import StringIO

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from extensions import db
from models import Card, Folder, FolderShare
from services.scryfall_cache import ensure_cache_loaded

from .base import (
    _collection_metadata,
    _normalize_name,
    views,
)


def _parse_card_list(text: str) -> "OrderedDict[str, dict]":
    """Parse lines like '2x Name' or '2 Name' into OrderedDict of normalized entries."""
    want = OrderedDict()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = line.strip('"').strip("'")
        qty = 1
        name = line

        match = re.match(r"^\s*(\d+)\s*x?\s+(.+?)\s*$", line, flags=re.IGNORECASE)
        if not match:
            match = re.match(r"^\s*(.+?)\s*x\s*(\d+)\s*$", line, flags=re.IGNORECASE)
            if match:
                name, qty = match.group(1), int(match.group(2))
        else:
            qty, name = int(match.group(1)), match.group(2)

        nkey = _normalize_name(name)
        if not nkey:
            continue
        if nkey in want:
            want[nkey]["qty"] += qty
        else:
            want[nkey] = {"display": name.strip(), "qty": qty}
    return want


def _accessible_folder_ids() -> set[int]:
    ids: set[int] = set()
    if not current_user.is_authenticated:
        return ids
    owned = db.session.query(Folder.id).filter(Folder.owner_user_id == current_user.id).all()
    ids.update(fid for fid, in owned)
    shared = (
        db.session.query(FolderShare.folder_id)
        .filter(FolderShare.shared_user_id == current_user.id)
        .all()
    )
    ids.update(fid for fid, in shared)
    public = db.session.query(Folder.id).filter(Folder.is_public.is_(True)).all()
    ids.update(fid for fid, in public)
    return ids


def _face_like_patterns(name: str):
    """
    Build SQL ILIKE patterns that tolerate optional spaces around '//' and
    allow matching either face: 'n // %', 'n//%', '% // n', '%//n'.
    """
    cleaned = " ".join((name or "").split()).strip()
    if not cleaned:
        return []
    return [
        f"{cleaned} // %",
        f"{cleaned}//%",
        f"% // {cleaned}",
        f"%//{cleaned}",
    ]


def find_card_by_name_or_face(name: str):
    """
    Try exact (case-insensitive). If not found, try to match either face
    of a card stored as 'Face A // Face B' (or 'A//B').
    Returns: Card | None
    """
    if not name:
        return None

    cleaned = " ".join(name.split()).strip()
    if not cleaned:
        return None

    exact = Card.query.filter(func.lower(Card.name) == cleaned.lower()).first()
    if exact:
        return exact

    patterns = _face_like_patterns(cleaned)
    if patterns:
        face_match = (
            Card.query.filter(or_(*[Card.name.ilike(p) for p in patterns]))
            .order_by(func.length(Card.name))
            .first()
        )
        if face_match:
            return face_match

    return (
        Card.query.filter(Card.name.ilike(f"%{cleaned}%"))
        .order_by(func.length(Card.name))
        .first()
    )


def _compute_list_checker(pasted: str):
    """
    Core logic to compute rows + folder breakdowns.

    Enhancements:
    - When the exact name doesn't hit, we run a face-aware rescue pass that
      counts copies and picks a representative card using either face.
    - Scryfall id resolution also understands face names.
    """
    want = _parse_card_list(pasted)
    if not want:
        return [], {"have_all": 0, "partial": 0, "missing": 0, "total_rows": 0}, "No card names found."

    keys = list(want.keys())
    display_by_nkey = {nkey: spec["display"] for nkey, spec in want.items()}

    rows = (
        db.session.query(
            Card,
            Folder.id.label("folder_id"),
            Folder.name.label("folder_name"),
            Folder.category.label("folder_category"),
        )
        .join(Folder, Folder.id == Card.folder_id, isouter=True)
        .filter(func.lower(Card.name).in_(keys))
        .all()
    )

    per_folder_counts = defaultdict(lambda: defaultdict(int))  # all folders
    collection_counts = defaultdict(lambda: defaultdict(int))
    deck_counts = defaultdict(lambda: defaultdict(int))
    available_per_folder_counts = defaultdict(lambda: defaultdict(int))
    _, _, collection_lower = _collection_metadata()

    def _rank_folder(fname):
        lower = (fname or "").strip().lower()
        return (
            0 if (lower in collection_lower or "collection" in lower) else 1,
            lower,
        )

    avail_ids = _accessible_folder_ids()

    best_card_for_name = {}
    for card, folder_id, folder_name, folder_category in rows:
        if folder_name:
            nkey = _normalize_name(card.name)
            per_folder_counts[nkey][folder_name] += 1
            is_collection_folder = (folder_category or Folder.CATEGORY_DECK) == Folder.CATEGORY_COLLECTION
            if is_collection_folder:
                collection_counts[nkey][folder_name] += 1
                if folder_id:
                    available_per_folder_counts[nkey][folder_name] += 1
            else:
                deck_counts[nkey][folder_name] += 1
            cand = (_rank_folder(folder_name), card)
            prev = best_card_for_name.get(nkey)
            if prev is None or cand[0] < prev[0]:
                best_card_for_name[nkey] = cand
    rows2 = (
        db.session.query(Card.name, Card.folder_id, Folder.category)
        .join(Folder, Folder.id == Card.folder_id, isouter=True)
        .filter(func.lower(Card.name).in_(keys))
        .all()
    )
    available_count = defaultdict(int)
    for name, fid, folder_category in rows2:
        is_collection_folder = (folder_category or Folder.CATEGORY_DECK) == Folder.CATEGORY_COLLECTION
        if fid and is_collection_folder:
            available_count[_normalize_name(name)] += 1

    rep_card_map = {nkey: best_card_for_name[nkey][1] for nkey in best_card_for_name}
    for nkey in keys:
        if per_folder_counts[nkey] or available_count[nkey]:
            continue

        display_name = display_by_nkey.get(nkey, "")
        patterns = _face_like_patterns(display_name)
        canonical_lower = None
        face_card = None

        if not patterns:
            face_card = find_card_by_name_or_face(display_name)
            if not face_card:
                continue
            canonical_lower = face_card.name.lower()

        # If we have an exact canonical match, use it and skip pattern fallback.
        if canonical_lower:
            add_rows = (
                db.session.query(
                    Card,
                    Folder.id.label("folder_id"),
                    Folder.name.label("folder_name"),
                    Folder.category.label("folder_category"),
                )
                .join(Folder, Folder.id == Card.folder_id, isouter=True)
                .filter(func.lower(Card.name) == canonical_lower)
                .all()
            )
            for card, folder_id, folder_name, folder_category in add_rows:
                if folder_name:
                    per_folder_counts[nkey][folder_name] += 1
                    is_collection_folder = (folder_category or Folder.CATEGORY_DECK) == Folder.CATEGORY_COLLECTION
                    if is_collection_folder:
                        collection_counts[nkey][folder_name] += 1
                        if folder_id:
                            available_per_folder_counts[nkey][folder_name] += 1
                    else:
                        deck_counts[nkey][folder_name] += 1
            add_rows2 = (
                db.session.query(Card.folder_id, Folder.category)
                .join(Folder, Folder.id == Card.folder_id, isouter=True)
                .filter(func.lower(Card.name) == canonical_lower)
                .all()
            )
            for fid, folder_category in add_rows2:
                is_collection_folder = (folder_category or Folder.CATEGORY_DECK) == Folder.CATEGORY_COLLECTION
                if fid and is_collection_folder:
                    available_count[nkey] += 1
            rep_card_map[nkey] = face_card
            continue

        # Fallback: attempt pattern-based matching (e.g., double-faced names).
        if not patterns:
            continue

        add_rows = (
            db.session.query(
                Card,
                Folder.id.label("folder_id"),
                Folder.name.label("folder_name"),
                Folder.category.label("folder_category"),
            )
            .join(Folder, Folder.id == Card.folder_id, isouter=True)
            .filter(or_(*[Card.name.ilike(p) for p in patterns]))
            .all()
        )
        for card, folder_id, folder_name, folder_category in add_rows:
            if folder_name:
                per_folder_counts[nkey][folder_name] += 1
                is_collection_folder = (folder_category or Folder.CATEGORY_DECK) == Folder.CATEGORY_COLLECTION
                if is_collection_folder:
                    collection_counts[nkey][folder_name] += 1
                    if folder_id:
                        available_per_folder_counts[nkey][folder_name] += 1
                else:
                    deck_counts[nkey][folder_name] += 1

        add_rows2 = (
            db.session.query(Card.folder_id, Folder.category)
            .join(Folder, Folder.id == Card.folder_id, isouter=True)
            .filter(or_(*[Card.name.ilike(p) for p in patterns]))
            .all()
        )
        for fid, folder_category in add_rows2:
            is_collection_folder = (folder_category or Folder.CATEGORY_DECK) == Folder.CATEGORY_COLLECTION
            if fid and is_collection_folder:
                available_count[nkey] += 1

        rep = (
            Card.query.filter(or_(*[Card.name.ilike(p) for p in patterns]))
            .order_by(func.length(Card.name))
            .first()
        )
        if rep:
            rep_card_map[nkey] = rep

    name_to_sid = {}
    face_to_sid = {}
    try:
        if ensure_cache_loaded():
            from services.scryfall_cache import get_all_prints

            for pr in get_all_prints():
                nm = _normalize_name(pr.get("name") or "")
                if not nm:
                    continue
                sid = pr.get("id")
                lang = (pr.get("lang") or "en").lower()
                oracle = pr.get("oracle_id")

                prev = name_to_sid.get(nm)
                if prev is None or (prev[1] != "en" and lang == "en"):
                    name_to_sid[nm] = (sid, lang, oracle)

                raw = (pr.get("name") or "")
                if "//" in raw:
                    a, b = [s.strip() for s in raw.split("//", 1)]
                    na = _normalize_name(a)
                    nb = _normalize_name(b)
                    if na:
                        prev_a = face_to_sid.get(na)
                        if prev_a is None or (prev_a[1] != "en" and lang == "en"):
                            face_to_sid[na] = (sid, lang, oracle)
                    if nb:
                        prev_b = face_to_sid.get(nb)
                        if prev_b is None or (prev_b[1] != "en" and lang == "en"):
                            face_to_sid[nb] = (sid, lang, oracle)
    except Exception:
        name_to_sid = {}
        face_to_sid = {}

    results = []
    have_all = partial = missing = 0
    for nkey, spec in want.items():
        requested = spec["qty"]
        display = spec["display"]

        total_owned = sum(per_folder_counts[nkey].values())
        available = available_count[nkey]
        missing_qty = max(0, requested - available)

        if available >= requested:
            status = "have_all"
            have_all += 1
        elif available == 0:
            status = "missing"
            missing += 1
        else:
            status = "partial"
            partial += 1

        folder_breakdown = sorted(per_folder_counts[nkey].items(), key=lambda kv: _rank_folder(kv[0]))
        collection_breakdown = sorted(collection_counts[nkey].items(), key=lambda kv: _rank_folder(kv[0]))
        deck_breakdown = sorted(deck_counts[nkey].items(), key=lambda kv: _rank_folder(kv[0]))
        available_breakdown = sorted(
            available_per_folder_counts[nkey].items(), key=lambda kv: _rank_folder(kv[0])
        )

        rep_card = rep_card_map.get(nkey)
        rep_card_id = int(rep_card.id) if rep_card else None
        oracle_id = getattr(rep_card, "oracle_id", None)

        scry_id = None
        tup = name_to_sid.get(nkey) or face_to_sid.get(nkey)
        if tup:
            scry_id = tup[0]
            oracle_id = oracle_id or tup[2]

        if rep_card and not oracle_id:
            oracle_id = rep_card.oracle_id

        results.append(
            {
                "name": display,
                "requested": requested,
                "available_in_collection": available,
                "missing_qty": missing_qty,
                "status": status,
                "folders": collection_breakdown,
                "collection_folders": collection_breakdown,
                "deck_folders": deck_breakdown,
                "available_folders": available_breakdown,
                "total_owned": total_owned,
                "card_id": rep_card_id,
                "scry_id": scry_id,
                "oracle_id": oracle_id,
            }
        )

    results.sort(key=lambda rec: {"missing": 0, "partial": 1, "have_all": 2}.get(rec["status"], 3))
    summary = {
        "have_all": have_all,
        "partial": partial,
        "missing": missing,
        "total_rows": len(results),
    }
    return results, summary, None


@views.route("/list-checker", methods=["GET", "POST"])
def list_checker():
    if request.method == "GET":
        return render_template("decks/list_checker.html", results=None, pasted="")

    pasted = request.form.get("card_list", "")
    results, summary, error = _compute_list_checker(pasted)
    if error:
        return render_template("decks/list_checker.html", results=None, pasted=pasted, error=error)

    return render_template("decks/list_checker.html", results=results, pasted=pasted, summary=summary)


@views.route("/list-checker/export", methods=["POST"], endpoint="list_checker_export_csv")
def list_checker_export_csv():
    pasted = request.form.get("card_list", "")
    results, summary, error = _compute_list_checker(pasted)

    if error or not results:
        flash("Nothing to export. Paste a list and click Check first.", "warning")
        return redirect(url_for("views.list_checker"))

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Card", "Requested", "Available", "Missing", "Status", "Total Owned", "Folders"])
    for rec in results:
        folders_str = " | ".join(f"{fname} ×{cnt}" for fname, cnt in rec["folders"])
        writer.writerow(
            [
                rec["name"],
                rec["requested"],
                rec["available_in_collection"],
                rec["missing_qty"],
                rec["status"],
                rec["total_owned"],
                folders_str,
            ]
        )

    out = si.getvalue()
    return Response(
        out,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=list_checker_results.csv"},
    )


__all__ = ["list_checker", "list_checker_export_csv"]
