"""
Canonical card identity is `oracle_id`.
Other services may read or store aliases, but MUST NOT treat other fields as primary identity.
"""

from extensions import db
from core.shared.utils.time import utcnow

class Card(db.Model):
    __tablename__ = "cards"
    __table_args__ = (
        db.CheckConstraint("quantity >= 0", name="ck_cards_quantity_nonneg"),
        db.CheckConstraint(
            "condition IS NULL OR condition IN ('NM','LP','MP','HP','DMG')",
            name="ck_cards_condition_grade",
        ),
        db.Index(
            "ix_cards_oracle_print",
            "oracle_id",
            "set_code",
            "collector_number",
            "is_foil",
            "lang",
        ),
        db.Index(
            "ix_cards_folder_oracle",
            "folder_id",
            "oracle_id",
        ),
        db.Index(
            "ix_cards_folder_print",
            "folder_id",
            "set_code",
            "collector_number",
            "lang",
            "is_foil",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    # Core identity fields you already had
    name             = db.Column(db.String(255), index=True, nullable=False)
    set_code         = db.Column(db.String(10), nullable=False)
    collector_number = db.Column(db.String(20), nullable=False)
    date_bought      = db.Column(db.Date, nullable=True)

    # ✅ The missing foreign key — MUST reference "folder.id" (singular table)
    folder_id = db.Column(
        db.Integer,
        db.ForeignKey("folder.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # New stabilized fields
    quantity  = db.Column(db.Integer, nullable=False, default=1)
    oracle_id = db.Column(db.String(36), index=True)
    lang      = db.Column(db.String(5), nullable=False, default="en")
    is_foil   = db.Column(db.Boolean, nullable=False, default=False)

    # Condition grade for the physical copy. Uses TCG-standard abbreviations:
    # NM (Near Mint), LP (Lightly Played), MP (Moderately Played),
    # HP (Heavily Played), DMG (Damaged). Nullable so unknown/unset is valid.
    condition = db.Column(db.String(4), nullable=True, index=True)

    # Derived metadata hydrated during sync jobs / migrations
    type_line = db.Column(db.Text, nullable=True)
    rarity = db.Column(db.String(16), nullable=True)
    oracle_text = db.Column(db.Text, nullable=True)
    mana_value = db.Column(db.Float, nullable=True)
    colors = db.Column(db.String(8), nullable=True)
    color_identity = db.Column(db.String(8), nullable=True)
    color_identity_mask = db.Column(db.Integer, nullable=True)
    layout = db.Column(db.String(32), nullable=True)
    faces_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)
    archived_at = db.Column(db.DateTime, nullable=True, index=True)

    # Relationship side
    folder = db.relationship("Folder", back_populates="cards")
    roles = db.relationship("Role", secondary="card_roles", backref="cards")
    subroles = db.relationship("SubRole", secondary="card_subroles", backref="cards")

    def __repr__(self):
        return f"<Card {self.name} [{self.set_code} #{self.collector_number}] x{self.quantity}>"

    # Condition helpers ----------------------------------------------------
    #: Ordered tuple of accepted condition grades (best to worst).
    CONDITION_GRADES: tuple[str, ...] = ("NM", "LP", "MP", "HP", "DMG")

    #: Human-readable label for each grade, used in UI selects and exports.
    CONDITION_LABELS: dict[str, str] = {
        "NM": "Near Mint",
        "LP": "Lightly Played",
        "MP": "Moderately Played",
        "HP": "Heavily Played",
        "DMG": "Damaged",
    }

    #: Common aliases (e.g., from CSV imports) mapped to canonical grades.
    _CONDITION_ALIASES: dict[str, str] = {
        "nm": "NM",
        "near mint": "NM",
        "mint": "NM",
        "m": "NM",
        "lp": "LP",
        "lightly played": "LP",
        "light play": "LP",
        "excellent": "LP",
        "ex": "LP",
        "mp": "MP",
        "moderately played": "MP",
        "moderate play": "MP",
        "good": "MP",
        "gd": "MP",
        "played": "MP",
        "pl": "MP",
        "hp": "HP",
        "heavily played": "HP",
        "heavy play": "HP",
        "poor": "HP",
        "pr": "HP",
        "dmg": "DMG",
        "damaged": "DMG",
        "damage": "DMG",
    }

    @classmethod
    def normalize_condition(cls, value: str | None) -> str | None:
        """Return the canonical grade for ``value`` or ``None`` if unrecognized.

        Blank/None input returns ``None`` so callers can distinguish "unknown"
        from an explicit grade. Callers that want to reject invalid input
        should compare the result against the original.
        """
        if value is None:
            return None
        token = str(value).strip()
        if not token:
            return None
        upper = token.upper()
        if upper in cls.CONDITION_GRADES:
            return upper
        return cls._CONDITION_ALIASES.get(token.lower())

    @property
    def condition_label(self) -> str | None:
        """Human-readable version of :attr:`condition`, if set."""
        if not self.condition:
            return None
        return self.CONDITION_LABELS.get(self.condition, self.condition)

    def set_oracle_id(self, oracle_id: str | None) -> bool:
        """Update the canonical oracle_id, returning True if a change was made."""
        normalized = (oracle_id or "").strip()
        value = normalized or None
        if self.oracle_id != value:
            self.oracle_id = value
            return True
        return False
