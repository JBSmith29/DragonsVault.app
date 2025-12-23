from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app
from extensions import db
from models import Folder

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
ctx = app.app_context()
ctx.push()
try:
    folders = Folder.query.all()
    print("count", len(folders))
    for f in folders[:10]:
        print(f.id, f.name, f.category)
finally:
    ctx.pop()
