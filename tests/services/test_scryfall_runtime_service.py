from __future__ import annotations

from pathlib import Path

from core.domains.cards.services import scryfall_runtime_service as runtime


class _Response:
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_data_root_prefers_configured_directory():
    class _App:
        config = {"SCRYFALL_DATA_DIR": "/tmp/custom-scryfall"}
        instance_path = "/tmp/instance"

    root = runtime.data_root(
        current_app=_App(),
        guess_instance_data_root_fn=lambda: Path("/tmp/fallback"),
    )

    assert root == Path("/tmp/custom-scryfall")


def test_guess_instance_data_root_uses_instance_dir_env():
    root = runtime.guess_instance_data_root(
        file_path=__file__,
        env={"INSTANCE_DIR": "/tmp/runtime-instance"},
    )

    assert root == Path("/tmp/runtime-instance/data")


def test_fetch_live_print_falls_back_to_search_query():
    session = _Session(
        [
            _Response(status_code=404),
            _Response(status_code=200, payload={"data": [{"id": "print-123"}]}),
        ]
    )

    result = runtime.fetch_live_print(
        "WOE",
        "42",
        session_factory=lambda: session,
        name_hint="Spell Stutter",
    )

    assert result == {"id": "print-123"}
    assert session.calls[0][0] == "https://api.scryfall.com/cards/woe/42"
    assert session.calls[1][0] == "https://api.scryfall.com/cards/search"
    assert session.calls[1][1]["params"]["q"] == '!"Spell Stutter" set:woe cn:42'
