import os
import sys
from pathlib import Path

import pytest
import extensions as ext
from extensions import db
from models import User

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Isolate all tests to a throwaway instance + SQLite database
TEST_INSTANCE_DIR = ROOT_DIR / ".pytest-instance"
TEST_INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("INSTANCE_DIR", str(TEST_INSTANCE_DIR))
os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(TEST_INSTANCE_DIR / 'test.sqlite').as_posix()}",
)
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
    )
    return flask_app


@pytest.fixture
def db_session(app):
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield db
        db.session.remove()
        db.drop_all()


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
