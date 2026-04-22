"""CSV export helpers for list checker results."""

from __future__ import annotations

import csv
from io import StringIO

from flask import Response


def build_list_checker_export_response(results: list[dict]) -> Response:
    si = StringIO()
    writer = csv.writer(si)
    max_sources = 0
    for rec in results:
        folders_for_export = rec.get("available_user_folders")
        if not folders_for_export:
            folders_for_export = rec.get("available_folders") or []
        max_sources = max(max_sources, len(folders_for_export))
    source_col_count = max(1, max_sources)

    header = ["Card", "Type", "Color Identity", "Rarity", "Requested", "Available", "Missing", "Status", "Total Owned"]
    header.extend([f"Collection {idx}" for idx in range(1, source_col_count + 1)])
    writer.writerow(header)

    for rec in results:
        folders_for_export = rec.get("available_user_folders")
        if not folders_for_export:
            folders_for_export = rec.get("available_folders") or []
        labels = []
        for fname, cnt in folders_for_export:
            if not fname:
                continue
            labels.append(f"{fname} ×{cnt}")
        if len(labels) < source_col_count:
            labels.extend([""] * (source_col_count - len(labels)))
        writer.writerow(
            [
                rec["name"],
                rec.get("type") or "",
                rec.get("color_identity_label") or "",
                rec.get("rarity") or "",
                rec["requested"],
                rec["available_in_collection"],
                rec["missing_qty"],
                rec["status"],
                rec["total_owned"],
            ]
            + labels
        )

    out = "\ufeff" + si.getvalue()
    return Response(
        out,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=list_checker_results.csv"},
    )


__all__ = ["build_list_checker_export_response"]
