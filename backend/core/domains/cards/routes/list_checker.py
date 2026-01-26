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
from models import Card, Folder, FolderShare, User, UserFriend
from core.domains.cards.services import scryfall_cache
from core.domains.decks.services import deck_utils

from core.routes.base import _collection_rows_with_fallback, _normalize_name, views

BASIC_LAND_SLUGS = {_normalize_name(name) for name in deck_utils.BASIC_LANDS}


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
    friends = (
        db.session.query(Folder.id)
        .join(UserFriend, UserFriend.friend_user_id == Folder.owner_user_id)
        .filter(UserFriend.user_id == current_user.id)
        .all()
    )
    ids.update(fid for fid, in friends)
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
            Folder.owner_user_id.label("owner_user_id"),
            Folder.owner.label("owner_name"),
        )
        .join(Folder, Folder.id == Card.folder_id, isouter=True)
        .filter(func.lower(Card.name).in_(keys))
        .all()
    )

    per_folder_counts = defaultdict(lambda: defaultdict(int))  # all folders
    collection_counts = defaultdict(lambda: defaultdict(int))
    deck_counts = defaultdict(lambda: defaultdict(int))
    available_per_folder_counts = defaultdict(lambda: defaultdict(int))
    current_user_id = current_user.id if current_user.is_authenticated else None
    friend_ids = set()
    if current_user_id:
        friend_rows = (
            db.session.query(UserFriend.friend_user_id)
            .filter(UserFriend.user_id == current_user_id)
            .all()
        )
        friend_ids = {fid for (fid,) in friend_rows if fid}
        owner_ids = [current_user_id] + list(friend_ids)
        collection_rows = _collection_rows_with_fallback(owner_user_ids=owner_ids)
    else:
        collection_rows = _collection_rows_with_fallback()
    collection_ids = [fid for fid, _ in collection_rows if fid is not None]
    collection_id_set = set(collection_ids)

    folder_meta = {}
    owner_user_ids = set()
    for _card, folder_id, folder_name, owner_user_id, owner_name in rows:
        if folder_id is None:
            continue
        if folder_id not in folder_meta:
            folder_meta[folder_id] = {
                "name": folder_name or "",
                "owner_user_id": owner_user_id,
                "owner": owner_name or "",
            }
        if owner_user_id:
            owner_user_ids.add(owner_user_id)

    owner_label_map = {}
    if owner_user_ids:
        owner_rows = (
            db.session.query(User.id, User.display_name, User.username, User.email)
            .filter(User.id.in_(owner_user_ids))
            .all()
        )
        for uid, display_name, username, email in owner_rows:
            label = display_name or username or email
            if label:
                owner_label_map[uid] = label

    def _folder_label(fid):
        meta = folder_meta.get(fid) or {}
        name = (meta.get("name") or "").strip()
        if not name:
            return ""
        owner_id = meta.get("owner_user_id")
        owner_label = owner_label_map.get(owner_id) or (meta.get("owner") or "").strip()
        if owner_id and current_user.is_authenticated and owner_id == current_user.id:
            return name
        if owner_label:
            return f"{owner_label}: {name}"
        return name

    def _label_for_folder(fid):
        label = _folder_label(fid)
        if label:
            return label
        meta = folder_meta.get(fid) or {}
        name = (meta.get("name") or "").strip()
        if name:
            return name
        return str(fid) if fid is not None else ""

    def _rank_folder(fid, label):
        lower = (label or "").strip().lower()
        meta = folder_meta.get(fid) or {}
        owner_id = meta.get("owner_user_id")
        owner_rank = 2
        if current_user_id and owner_id == current_user_id:
            owner_rank = 0
        elif owner_id in friend_ids:
            owner_rank = 1
        return (
            0 if (fid in collection_id_set) else 1,
            owner_rank,
            lower,
        )

    def _format_breakdown(breakdown):
        items = []
        for fid, cnt in breakdown.items():
            label = _label_for_folder(fid)
            if not label:
                continue
            items.append((fid, label, cnt))
        items.sort(key=lambda row: _rank_folder(row[0], row[1]))
        return [(label, cnt) for _, label, cnt in items]

    avail_ids = _accessible_folder_ids()

    best_card_for_name = {}
    for card, folder_id, folder_name, owner_user_id, owner_name in rows:
        if folder_id and folder_name:
            nkey = _normalize_name(card.name)
            per_folder_counts[nkey][folder_id] += 1
            is_collection_folder = bool(folder_id and folder_id in collection_id_set)
            if is_collection_folder:
                collection_counts[nkey][folder_id] += 1
                if folder_id:
                    available_per_folder_counts[nkey][folder_id] += 1
            else:
                deck_counts[nkey][folder_id] += 1
            cand = (_rank_folder(folder_id, folder_name), card)
            prev = best_card_for_name.get(nkey)
            if prev is None or cand[0] < prev[0]:
                best_card_for_name[nkey] = cand
    rows2 = (
        db.session.query(Card.name, Card.folder_id)
        .join(Folder, Folder.id == Card.folder_id, isouter=True)
        .filter(func.lower(Card.name).in_(keys))
        .all()
    )
    available_count = defaultdict(int)
    for name, fid in rows2:
        is_collection_folder = bool(fid and fid in collection_id_set)
        if fid and is_collection_folder:
            available_count[_normalize_name(name)] += 1

    rep_card_map = {nkey: best_card_for_name[nkey][1] for nkey in best_card_for_name}
    for nkey in keys:
        if nkey in BASIC_LAND_SLUGS:
            # Treat basics as infinitely owned/available.
            available_count[nkey] = max(available_count[nkey], 9999)
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
                )
                .join(Folder, Folder.id == Card.folder_id, isouter=True)
                .filter(func.lower(Card.name) == canonical_lower)
                .all()
            )
            for card, folder_id, folder_name in add_rows:
                if folder_id and folder_name:
                    per_folder_counts[nkey][folder_id] += 1
                    is_collection_folder = bool(folder_id and folder_id in collection_id_set)
                    if is_collection_folder:
                        collection_counts[nkey][folder_id] += 1
                        if folder_id:
                            available_per_folder_counts[nkey][folder_id] += 1
                    else:
                        deck_counts[nkey][folder_id] += 1
            add_rows2 = (
                db.session.query(Card.folder_id)
                .join(Folder, Folder.id == Card.folder_id, isouter=True)
                .filter(func.lower(Card.name) == canonical_lower)
                .all()
            )
            for (fid,) in add_rows2:
                is_collection_folder = bool(fid and fid in collection_id_set)
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
            )
            .join(Folder, Folder.id == Card.folder_id, isouter=True)
            .filter(or_(*[Card.name.ilike(p) for p in patterns]))
            .all()
        )
        for card, folder_id, folder_name in add_rows:
            if folder_id and folder_name:
                per_folder_counts[nkey][folder_id] += 1
                is_collection_folder = bool(folder_id and folder_id in collection_id_set)
                if is_collection_folder:
                    collection_counts[nkey][folder_id] += 1
                    if folder_id:
                        available_per_folder_counts[nkey][folder_id] += 1
                else:
                    deck_counts[nkey][folder_id] += 1

        add_rows2 = (
            db.session.query(Card.folder_id)
            .join(Folder, Folder.id == Card.folder_id, isouter=True)
            .filter(or_(*[Card.name.ilike(p) for p in patterns]))
            .all()
        )
        for (fid,) in add_rows2:
            is_collection_folder = bool(fid and fid in collection_id_set)
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
        if scryfall_cache.ensure_cache_loaded():
            from core.domains.cards.services.scryfall_cache import get_all_prints

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

    folder_ids = set()
    for breakdown in (per_folder_counts, collection_counts, deck_counts, available_per_folder_counts):
        for counts in breakdown.values():
            folder_ids.update(fid for fid in counts if fid is not None)

    missing_ids = [fid for fid in folder_ids if fid not in folder_meta]
    if missing_ids:
        folder_rows = (
            db.session.query(
                Folder.id,
                Folder.name,
                Folder.owner_user_id,
                Folder.owner,
                User.display_name,
                User.username,
                User.email,
            )
            .outerjoin(User, User.id == Folder.owner_user_id)
            .filter(Folder.id.in_(missing_ids))
            .all()
        )
        for fid, name, owner_user_id, owner, display_name, username, email in folder_rows:
            if fid not in folder_meta:
                folder_meta[fid] = {
                    "name": name or "",
                    "owner_user_id": owner_user_id,
                    "owner": owner or "",
                }
            if owner_user_id and owner_user_id not in owner_label_map:
                label = display_name or username or email
                if label:
                    owner_label_map[owner_user_id] = label

    results = []
    have_all = partial = missing = 0
    for nkey, spec in want.items():
        requested = spec["qty"]
        display = spec["display"]

        is_basic_land = nkey in BASIC_LAND_SLUGS
        total_owned = sum(per_folder_counts[nkey].values())
        available = available_count[nkey]
        missing_qty = max(0, requested - available)

        if is_basic_land:
            available = max(available, requested)
            total_owned = max(total_owned, requested)
            missing_qty = 0
            status = "have_all"
            have_all += 1
        else:
            if available >= requested:
                status = "have_all"
                have_all += 1
            elif available == 0:
                status = "missing"
                missing += 1
            else:
                status = "partial"
                partial += 1

        folder_breakdown = _format_breakdown(per_folder_counts[nkey])
        collection_breakdown = _format_breakdown(collection_counts[nkey])
        deck_breakdown = _format_breakdown(deck_counts[nkey])
        available_breakdown = _format_breakdown(available_per_folder_counts[nkey])

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
