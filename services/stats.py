# services/stats.py
import json
from sqlalchemy import func
from extensions import db, cache

# Import your models. If Folder isn't re-exported at models/__init__.py,
# change these to: from models.card import Card; from models.folder import Folder
from models import Card, Folder


def _filters_to_key(filters: dict | None) -> str:
    """
    Build a stable cache key. We sort folder ids so [3,1] and [1,3] cache the same.
    """
    f = dict(filters or {})
    if "folder_ids" in f and isinstance(f["folder_ids"], list):
        f["folder_ids"] = sorted(int(x) for x in f["folder_ids"])
    return json.dumps(f, sort_keys=True, separators=(",", ":"))


@cache.memoize(timeout=300)
def folder_stats_cached(filters_key: str):
    """
    Return per-folder counts and quantities, grouped by folder_id.
    Output shape: [{"folder_id": int, "folder": "Name", "rows": int, "qty": int}, ...]
    """
    filters = json.loads(filters_key)
    include_proxies = bool(filters.pop("include_proxies", False))

    q = (
        db.session.query(
            Card.folder_id.label("folder_id"),
            func.count(Card.id).label("rows"),
            func.coalesce(func.sum(Card.quantity), 0).label("qty"),
            Folder.name.label("folder_name"),
        )
        .join(Folder, Folder.id == Card.folder_id)
    )

    if not include_proxies:
        q = q.filter(Card.is_proxy.is_(False))
    if "lang" in filters:
        q = q.filter(Card.lang == filters["lang"])
    if "foil" in filters:
        # filters["foil"] is expected to be a boolean
        q = q.filter(Card.is_foil.is_(bool(filters["foil"])))
    if filters.get("folder_ids"):
        q = q.filter(Card.folder_id.in_(filters["folder_ids"]))

    q = q.group_by(Card.folder_id, Folder.name)
    rows = q.all()

    return [
        {
            "folder_id": folder_id,
            "folder": folder_name,
            "rows": int(rows_count),
            "qty": int(qty_sum),
        }
        for (folder_id, rows_count, qty_sum, folder_name) in rows
    ]


def get_folder_stats(filters: dict | None = None):
    return folder_stats_cached(_filters_to_key(filters))
