from extensions import db

class Card(db.Model):
    __tablename__ = "cards"

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

    # Relationship side
    folder = db.relationship("Folder", back_populates="cards")

    @property
    def is_owned(self) -> bool:
        return not self.is_proxy

    def __repr__(self):
        return f"<Card {self.name} [{self.set_code} #{self.collector_number}] x{self.quantity}>"
