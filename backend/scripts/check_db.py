from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import INSTANCE_DIR

db_path = INSTANCE_DIR / "database.db"
if not db_path.exists():
    raise SystemExit(f"Database not found at {db_path}")

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("tables", cur.fetchall())
cur.execute(
    "SELECT id, name, category, commander_name FROM folder ORDER BY id DESC LIMIT 10"
)
print("folders", cur.fetchall())
conn.close()
