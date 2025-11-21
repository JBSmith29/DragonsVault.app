# models/wishlist.py
import json
from datetime import datetime

from extensions import db
from sqlalchemy.ext.hybrid import hybrid_property


class WishlistItem(db.Model):
    __tablename__ = "wishlist_items"

    id = db.Column(db.Integer, primary_key=True)

    # Optional linkage to a known Card row (nullable)
    card_id = db.Column(
        db.Integer,
        db.ForeignKey("cards.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Group wishlist rows by a card's oracle id (all printings)
    oracle_id = db.Column(db.String(64), nullable=True, index=True)

    # Specific printing fallback if you store it
    scryfall_id = db.Column(db.String(64), nullable=True, index=True)

    # Display name
    name = db.Column(db.String(200), nullable=False, index=True)

    requested_qty = db.Column(db.Integer, nullable=False, default=0)
    missing_qty = db.Column(db.Integer, nullable=False, default=0)

    # "open" | "to_fetch" | "ordered" | "acquired" | "removed"
    status = db.Column(db.String(16), nullable=False, default="open", index=True)

    # Optional JSON payload describing where to retrieve the card(s)
    source_folders = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Route compatibility: we expose added_at as an alias to created_at
    @hybrid_property
    def added_at(self):
        return self.created_at

    @added_at.expression
    def added_at(cls):
        return cls.created_at

    @property
    def source_folders_list(self):
        """Return parsed folders payload as list[{'name': str, 'qty': int|None}]."""
        if not self.source_folders:
            return []
        try:
            data = json.loads(self.source_folders)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        cleaned = []
        for entry in data:
            if isinstance(entry, dict):
                name = str(entry.get("name", "")).strip()
                if not name:
                    continue
                qty = entry.get("qty")
                try:
                    qty = int(qty)
                except Exception:
                    qty = None
                cleaned.append({"name": name, "qty": qty})
            elif isinstance(entry, str):
                name = entry.strip()
                if name:
                    cleaned.append({"name": name, "qty": None})
        return cleaned
