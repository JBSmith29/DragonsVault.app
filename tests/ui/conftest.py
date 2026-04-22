import threading
import time
from contextlib import closing
import os
import platform
from socket import socket, AF_INET, SOCK_STREAM
from pathlib import Path
from uuid import uuid4

import pytest
from werkzeug.serving import make_server

from app import create_app
from extensions import db
from models import User


def _get_free_port(host: str = "127.0.0.1") -> int:
    """Find an available port for the temporary live server."""
    with closing(socket(AF_INET, SOCK_STREAM)) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    """Spin up the Flask app in a background thread for browser-based tests."""
    app = create_app()
    app.testing = True
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SERVER_NAME="localhost")

    with app.app_context():
      db.session.remove()
      db.create_all()

    host = "127.0.0.1"
    port = _get_free_port(host)
    server = make_server(host, port, app, threaded=True)

    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    base_url = f"http://{host}:{port}"

    # Wait briefly for the server to accept connections
    deadline = time.time() + 5
    healthy = False
    while time.time() < deadline:
        with closing(socket(AF_INET, SOCK_STREAM)) as s:
            try:
                s.connect((host, port))
            except OSError:
                time.sleep(0.05)
            else:
                healthy = True
                break
    if not healthy:
        server.shutdown()
        thread.join(timeout=1)
        raise RuntimeError("Live server failed to start for UI tests.")

    yield base_url

    server.shutdown()
    thread.join(timeout=5)


def _should_skip_playwright() -> bool:
    if (os.getenv("FORCE_PLAYWRIGHT") or "").lower() in {"1", "true", "yes", "on"}:
        return False
    if (os.getenv("SKIP_PLAYWRIGHT") or "").lower() in {"1", "true", "yes", "on"}:
        return True
    model_path = Path("/sys/firmware/devicetree/base/model")
    try:
        if model_path.exists() and "raspberry pi" in model_path.read_text(errors="ignore").lower():
            return True
    except Exception:
        pass
    machine = platform.machine().lower()
    if machine.startswith("arm") or "aarch64" in machine:
        return True
    return False


@pytest.fixture
def browser():
    if _should_skip_playwright():
        pytest.skip(
            "Skipping Playwright UI tests on ARM/Pi or constrained hardware (set FORCE_PLAYWRIGHT=1 to run)."
        )
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:  # pragma: no cover - depends on local tooling
            pytest.skip(f"Playwright browser binaries missing: {exc}")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def mobile_page(browser):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


def _create_ui_user(*, is_admin: bool = False) -> dict[str, str]:
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SERVER_NAME="localhost")
    identifier = f"ui-{uuid4().hex[:10]}@example.com"
    password = "Password123!"
    username_prefix = "uiadmin" if is_admin else "uiuser"
    username = f"{username_prefix}_{uuid4().hex[:8]}"

    with app.app_context():
        db.session.remove()
        db.create_all()
        user = User(
            email=identifier,
            username=username,
            is_admin=is_admin,
            display_name="UI Admin" if is_admin else "UI User",
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

    return {"identifier": identifier, "password": password}


def _login(page, live_server: str, credentials: dict[str, str]) -> None:
    page.goto(f"{live_server}/login", wait_until="networkidle")
    page.locator("#identifier").fill(credentials["identifier"])
    page.locator("#password").fill(credentials["password"])
    page.locator("button[type='submit']").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_function("!window.location.pathname.startsWith('/login')")


@pytest.fixture
def authenticated_mobile_page(live_server, mobile_page):
    credentials = _create_ui_user()
    _login(mobile_page, live_server, credentials)
    return mobile_page


@pytest.fixture
def admin_mobile_page(live_server, mobile_page):
    credentials = _create_ui_user(is_admin=True)
    _login(mobile_page, live_server, credentials)
    return mobile_page
