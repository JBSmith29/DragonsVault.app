from sqlalchemy import text
from sqlalchemy import inspect
from extensions import db

def ensure_fts() -> None:
    """Create FTS5 table and triggers if missing."""
    bind = db.engine
    insp = inspect(bind)
    if "cards" not in insp.get_table_names():
        return

    with bind.begin() as conn:
        # 1) External-content FTS tied to cards.id
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts
            USING fts5(
                name, set_code, collector_number, lang, is_foil,
                content='cards', content_rowid='id'
            );
        """))

        # 2) Triggers to keep FTS in sync
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS cards_ai AFTER INSERT ON cards BEGIN
              INSERT INTO cards_fts(rowid, name, set_code, collector_number, lang, is_foil)
              VALUES (new.id, new.name, new.set_code, new.collector_number, new.lang,
                      CASE WHEN new.is_foil THEN '1' ELSE '0' END);
            END;
        """))
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS cards_ad AFTER DELETE ON cards BEGIN
              DELETE FROM cards_fts WHERE rowid = old.id;
            END;
        """))
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS cards_au AFTER UPDATE ON cards BEGIN
              DELETE FROM cards_fts WHERE rowid = old.id;
              INSERT INTO cards_fts(rowid, name, set_code, collector_number, lang, is_foil)
              VALUES (new.id, new.name, new.set_code, new.collector_number, new.lang,
                      CASE WHEN new.is_foil THEN '1' ELSE '0' END);
            END;
        """))

def reindex_fts() -> None:
    """Rebuild/populate the FTS index from current cards content."""
    with db.engine.begin() as conn:
        # Preferred: fts5 'rebuild' command (works with external content)
        try:
            conn.execute(text("INSERT INTO cards_fts(cards_fts) VALUES('rebuild');"))
            return
        except Exception:
            # Fallback: manual populate (idempotent-ish)
            conn.execute(text("DELETE FROM cards_fts;"))
            conn.execute(text("""
                INSERT INTO cards_fts(rowid, name, set_code, collector_number, lang, is_foil)
                SELECT id, name, set_code, collector_number, lang,
                       CASE WHEN is_foil THEN '1' ELSE '0' END
                FROM cards;
            """))
