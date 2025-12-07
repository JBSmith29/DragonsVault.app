from datetime import datetime

from extensions import db

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
        db.Index("ix_cards_created_at", "created_at"),
        db.Index("ix_cards_updated_at", "updated_at"),
        db.Index("ix_cards_archived_at", "archived_at"),
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
    is_proxy  = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.text("0"),
        index=True,
    )

    # Derived metadata hydrated during sync jobs / migrations
    type_line = db.Column(db.Text, nullable=True)
    rarity = db.Column(db.String(16), nullable=True)
    color_identity_mask = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    archived_at = db.Column(db.DateTime, nullable=True, index=True)

    # Relationship side
    folder = db.relationship("Folder", back_populates="cards")
    roles = db.relationship("Role", secondary="card_roles", backref="cards")
    subroles = db.relationship("SubRole", secondary="card_subroles", backref="cards")

    @property
    def is_owned(self) -> bool:
        return not self.is_proxy

    def __repr__(self):
        return f"<Card {self.name} [{self.set_code} #{self.collector_number}] x{self.quantity}>"
