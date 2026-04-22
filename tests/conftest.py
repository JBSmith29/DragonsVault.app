import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
import extensions as ext
from extensions import db
from models import User
from sqlalchemy.engine.url import make_url

# Isolate all tests to a throwaway instance + SQLite database
def _build_test_instance_dir() -> Path:
    preferred_parent = ROOT_DIR / ".pytest-instance"
    fallback_parent = Path(tempfile.gettempdir()) / f"dragonsvault-pytest-{os.getuid()}"
    for parent in (preferred_parent, fallback_parent):
        try:
            parent.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix="run-", dir=parent))
        except OSError:
            continue
    return Path(tempfile.mkdtemp(prefix="dragonsvault-pytest-run-"))


_configured_instance_dir = os.getenv("INSTANCE_DIR")
TEST_INSTANCE_DIR = (
    Path(_configured_instance_dir).resolve()
    if _configured_instance_dir
    else _build_test_instance_dir()
)
DEFAULT_TEST_DB_PATH = TEST_INSTANCE_DIR / "test.sqlite"
TEST_DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DEFAULT_TEST_DB_PATH.as_posix()}"
IS_SQLITE_TEST_DB = TEST_DATABASE_URL.startswith("sqlite:")
TEST_DB_PATH = DEFAULT_TEST_DB_PATH

if IS_SQLITE_TEST_DB:
    try:
        parsed_database = make_url(TEST_DATABASE_URL).database
    except Exception:
        parsed_database = None
    if parsed_database:
        configured_db_path = Path(parsed_database).expanduser()
        if not configured_db_path.is_absolute():
            configured_db_path = (TEST_INSTANCE_DIR / configured_db_path).resolve()
        TEST_DB_PATH = configured_db_path
    TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("FLASK_ENV", "development")
os.environ["INSTANCE_DIR"] = str(TEST_INSTANCE_DIR)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("ENABLE_TALISMAN", "0")
os.environ.setdefault("DISABLE_BACKGROUND_JOBS", "1")

import app as dv_app  # noqa: E402  pylint:disable=wrong-import-position

# Disable runtime rate limiting during tests (old Flask-Limiter builds lack kwargs we use)
dv_app.limiter = None
ext.limiter = None
create_app = dv_app.create_app


@pytest.fixture(scope="session")
def app():
    flask_app = create_app()
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SERVER_NAME="localhost",
        SQLALCHEMY_SESSION_OPTIONS={"expire_on_commit": False},
    )
    with flask_app.app_context():
        db.session.configure(expire_on_commit=False)
    return flask_app


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_instance_dir():
    yield
    if not _configured_instance_dir:
        shutil.rmtree(TEST_INSTANCE_DIR, ignore_errors=True)


def _cleanup_sqlite_files():
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = TEST_DB_PATH.with_name(TEST_DB_PATH.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


@pytest.fixture
def db_session(app):
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
        if IS_SQLITE_TEST_DB:
            _cleanup_sqlite_files()
        else:
            db.drop_all()
        db.create_all()
        yield db
        db.session.remove()
        if not IS_SQLITE_TEST_DB:
            db.drop_all()
        db.engine.dispose()
        if IS_SQLITE_TEST_DB:
            # SQLite teardown: delete the DB file to avoid drop_all issues across runs.
            _cleanup_sqlite_files()


@pytest.fixture
def client(app, db_session):  # noqa: ARG001 - keeps DB initialised for request tests
    return app.test_client()


@pytest.fixture
def create_user(db_session):
    def _create_user(
        *,
        email: str = "user@example.com",
        username: str = "user",
        password: str = "password123",
        is_admin: bool = False,
        display_name: str | None = None,
    ) -> tuple[User, str]:
        user = User(
            email=email.lower().strip(),
            username=username.lower().strip(),
            is_admin=is_admin,
            display_name=display_name,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user, password

    return _create_user
