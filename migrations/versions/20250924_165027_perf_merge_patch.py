
"""perf merge patch: derived fields, indexes, and FTS5

Revision ID: perf_merge_20250924_165027
Revises: d6781ca1696b
Create Date: 2025-09-24 16:50:27
"""
from alembic import op
import sqlalchemy as sa

revision = 'perf_merge_20250924_165027'
down_revision = 'd6781ca1696b'
branch_labels = None
depends_on = None

def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    insp = sa.inspect(conn)

    # Existing columns / indexes
    existing_cols = {c["name"] for c in insp.get_columns("cards")}
    existing_indexes = {ix["name"] for ix in insp.get_indexes("cards")}

    # Add columns only if missing (works cleanly on SQLite with batch mode)
    with op.batch_alter_table("cards") as batch:
        if "type_line" not in existing_cols:
            batch.add_column(sa.Column("type_line", sa.Text(), nullable=True))
        if "rarity" not in existing_cols:
            batch.add_column(sa.Column("rarity", sa.String(length=16), nullable=True))
        if "color_identity_mask" not in existing_cols:
            batch.add_column(sa.Column("color_identity_mask", sa.Integer(), nullable=True))

    # Create helpful indexes only if they don't already exist
    if "ix_cards_set_cn" not in existing_indexes:
        op.create_index("ix_cards_set_cn", "cards", ["set_code", "collector_number"], unique=False)
    if "ix_cards_lang" not in existing_indexes:
        op.create_index("ix_cards_lang", "cards", ["lang"], unique=False)
    if "ix_cards_is_foil" not in existing_indexes:
        op.create_index("ix_cards_is_foil", "cards", ["is_foil"], unique=False)
    if "ix_cards_folder_name" not in existing_indexes:
        op.create_index("ix_cards_folder_name", "cards", ["folder_id", "name"], unique=False)

    # SQLite FTS5 for fast name search (all guarded with IF NOT EXISTS)
    if conn.dialect.name == "sqlite":
        conn.exec_driver_sql("""
            CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
              name, content='cards', content_rowid='id'
            );
        """)
        conn.exec_driver_sql("""
            INSERT INTO cards_fts(rowid, name)
              SELECT id, lower(name)
              FROM cards
              WHERE name IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM cards_fts WHERE rowid = cards.id);
        """)
        conn.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS cards_ai AFTER INSERT ON cards BEGIN
              INSERT INTO cards_fts(rowid, name) VALUES (new.id, lower(new.name));
            END;
        """)
        conn.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS cards_au AFTER UPDATE ON cards BEGIN
              UPDATE cards_fts SET name = lower(new.name) WHERE rowid = new.id;
            END;
        """)
        conn.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS cards_ad AFTER DELETE ON cards BEGIN
              DELETE FROM cards_fts WHERE rowid = old.id;
            END;
        """)

def downgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'sqlite':
        conn.exec_driver_sql("DROP TABLE IF EXISTS cards_fts;")
    for ix in ['ix_cards_folder_name','ix_cards_is_foil','ix_cards_lang','ix_cards_set_cn']:
        try:
            op.drop_index(ix, table_name='cards')
        except Exception:
            pass
    with op.batch_alter_table('cards') as batch:
        for col in ['color_identity_mask','rarity','type_line']:
            try:
                batch.drop_column(col)
            except Exception:
                pass
