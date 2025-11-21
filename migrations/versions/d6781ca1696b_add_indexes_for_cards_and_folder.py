"""Add indexes for cards and folder (safe for SQLite)"""

from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = "d6781ca1696b"
down_revision = "20250922_b_enforce_unique_print_in_folder"  # keep your previous rev id here
branch_labels = None
depends_on = None


def _index_exists(bind, table: str, name: str) -> bool:
    """Cross-dialect 'does this index exist?' helper."""
    if bind.dialect.name == "sqlite":
        # SQLite: PRAGMA index_list('<table>')
        rows = bind.execute(sa.text(f"PRAGMA index_list('{table}')")).fetchall()
        # rows columns: seq, name, unique, origin, partial
        names = [r[1] for r in rows] if rows else []
        return name in names
    else:
        insp = sa.inspect(bind)
        existing = [ix["name"] for ix in insp.get_indexes(table)]
        return name in existing


def upgrade():
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # 1) Create compound helper indexes if missing
    for name, cols, unique in [
        ("ix_cards_folder_set_cn", ["folder_id", "set_code", "collector_number"], False),
        ("ix_cards_name_folder", ["name", "folder_id"], False),
    ]:
        if not _index_exists(bind, "cards", name):
            with op.batch_alter_table("cards") as batch_op:
                batch_op.create_index(name, cols, unique=unique)

    # 2) Enforce unique-per-print-in-folder
    #    On SQLite, we implement this as a UNIQUE INDEX (named).
    uniq_name = "uq_cards_print_per_folder"
    uniq_cols = ["name", "folder_id", "set_code", "collector_number", "lang", "is_foil"]

    if is_sqlite:
        # SQLite doesn't keep named constraints the same way; create unique index if missing.
        if not _index_exists(bind, "cards", uniq_name):
            with op.batch_alter_table("cards") as batch_op:
                batch_op.create_index(uniq_name, uniq_cols, unique=True)
    else:
        # Other DBs: create a proper unique constraint if not present.
        # We can't reliably detect named unique constraints cross-dialect, so "try/ignore".
        try:
            with op.batch_alter_table("cards") as batch_op:
                batch_op.create_unique_constraint(uniq_name, uniq_cols)
        except Exception:
            # Likely already exists under this or a prior name; leave as-is.
            pass


def downgrade():
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # Drop helper indexes if they exist
    for name in ["ix_cards_folder_set_cn", "ix_cards_name_folder"]:
        if _index_exists(bind, "cards", name):
            with op.batch_alter_table("cards") as batch_op:
                batch_op.drop_index(name)

    # Drop unique (index/constraint)
    uniq_name = "uq_cards_print_per_folder"
    if is_sqlite:
        if _index_exists(bind, "cards", uniq_name):
            with op.batch_alter_table("cards") as batch_op:
                batch_op.drop_index(uniq_name)
    else:
        try:
            with op.batch_alter_table("cards") as batch_op:
                batch_op.drop_constraint(uniq_name, type_="unique")
        except Exception:
            pass
