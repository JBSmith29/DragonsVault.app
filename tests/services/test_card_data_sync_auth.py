import sys
from pathlib import Path

import pytest

CARD_DATA_SRC = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "microservices"
    / "card-data"
    / "src"
)
if str(CARD_DATA_SRC) not in sys.path:
    sys.path.insert(0, str(CARD_DATA_SRC))

import card_data.app as card_data_app  # noqa: E402
import card_data.db as card_data_db  # noqa: E402


@pytest.fixture
def make_sync_client(monkeypatch):
    def _make(*, token: str | None = None, allowlist: str | None = None):
        monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
        if token is None:
            monkeypatch.delenv("CARD_DATA_SYNC_TOKEN", raising=False)
        else:
            monkeypatch.setenv("CARD_DATA_SYNC_TOKEN", token)
        if allowlist is None:
            monkeypatch.delenv("CARD_DATA_SYNC_ALLOWLIST", raising=False)
        else:
            monkeypatch.setenv("CARD_DATA_SYNC_ALLOWLIST", allowlist)

        card_data_db._ENGINE = None
        card_data_db._SESSION_FACTORY = None
        calls = []

        def fake_sync(engine, config, force=False):
            calls.append(force)
            return {"status": "ok", "forced": force, "service": config.service_name}

        monkeypatch.setattr(card_data_app, "sync_scryfall", fake_sync)
        app = card_data_app.create_app()
        return app.test_client(), calls

    return _make


def test_sync_forbidden_without_required_token(make_sync_client):
    client, calls = make_sync_client(token="top-secret")

    response = client.post("/v1/scryfall/sync")

    assert response.status_code == 403
    assert response.get_json() == {"status": "error", "error": "forbidden"}
    assert calls == []


def test_sync_allows_valid_token_and_force_query(make_sync_client):
    client, calls = make_sync_client(token="top-secret")

    response = client.post(
        "/v1/scryfall/sync?force=1",
        headers={"X-Card-Data-Token": "top-secret"},
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert calls == [True]


def test_sync_forbidden_when_allowlist_does_not_match(make_sync_client):
    client, calls = make_sync_client(allowlist="10.0.0.0/8")

    response = client.post(
        "/v1/scryfall/sync",
        environ_overrides={"REMOTE_ADDR": "203.0.113.21"},
    )

    assert response.status_code == 403
    assert response.get_json() == {"status": "error", "error": "forbidden"}
    assert calls == []


def test_sync_allows_request_from_allowlisted_ip(make_sync_client):
    client, calls = make_sync_client(allowlist="203.0.113.0/24")

    response = client.post(
        "/v1/scryfall/sync",
        environ_overrides={"REMOTE_ADDR": "203.0.113.21"},
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert calls == [False]
