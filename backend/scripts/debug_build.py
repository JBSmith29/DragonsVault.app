from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app
from extensions import db
from models import Folder
from routes.build import _folder_name_exists, _generate_unique_folder_name

app = create_app()

with app.app_context():
    print("folder count", Folder.query.count())
    print("exists 'Lightning Strikes Twice'?", _folder_name_exists("Lightning Strikes Twice"))
    print("suggested name", _generate_unique_folder_name("Lightning Strikes Twice"))
