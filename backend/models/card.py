from extensions import db
from utils.time import utcnow

class Card(db.Model):
    __tablename__ = "cards"
    __table_args__ = (
        db.CheckConstraint("quantity >= 0", name="ck_cards_quantity_nonneg"),
        db.Index(
            "ix_cards_oracle_print",
            "oracle_id",
            "set_code",
            "collector_number",
            "is_foil",
            "lang",
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
