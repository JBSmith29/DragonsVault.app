from extensions import db


class FolderRole(db.Model):
    __tablename__ = "folder_roles"

    ROLE_COLLECTION = "collection"
    ROLE_DECK = "deck"
    ROLE_BUILD = "build"
    PRIMARY_ROLES = {ROLE_COLLECTION, ROLE_DECK, ROLE_BUILD}
    DECK_ROLES = {ROLE_DECK, ROLE_BUILD}

    folder_id = db.Column(
        db.Integer,
        db.ForeignKey("folder.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role = db.Column(db.String(32), primary_key=True)

    folder = db.relationship("Folder", back_populates="role_entries")

    @classmethod
    def normalize(cls, role: str | None) -> str:
        return (role or "").strip().lower()
