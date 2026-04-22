from __future__ import annotations

from pathlib import Path

from core.domains.cards.services import scryfall_bulk_service as bulk


class _Response:
    def __init__(self, *, status_code=200, payload=None, chunks=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self._chunks = chunks or []
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=0):  # noqa: ARG002
        for chunk in self._chunks:
            yield chunk


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_fetch_bulk_metadata_and_default_cards_uri():
    session = _Session(
        [
            _Response(
                payload={
                    "data": [
                        {"type": "oracle_cards", "download_uri": "https://example.com/oracle.json"},
                        {"type": "default_cards", "download_uri": "https://example.com/default.json"},
                    ]
                }
            )
        ]
    )

    index = bulk.fetch_bulk_index(session_factory=lambda: session, bulk_meta_url="https://api.example.com/bulk")
    metadata = bulk.get_bulk_metadata("default_cards", fetch_bulk_index_fn=lambda: index)
    uri = bulk.get_default_cards_download_uri(
        get_bulk_download_uri_fn=lambda kind: bulk.get_bulk_download_uri(
            kind,
            get_bulk_metadata_fn=lambda target: bulk.get_bulk_metadata(target, fetch_bulk_index_fn=lambda: index),
        )
    )

    assert index[0]["type"] == "oracle_cards"
    assert metadata["download_uri"] == "https://example.com/default.json"
    assert uri == "https://example.com/default.json"
    assert session.calls[0][0] == "https://api.example.com/bulk"


def test_stream_download_to_writes_payload_and_etag(tmp_path):
    session = _Session(
        [
            _Response(
                chunks=[b"hello ", b"world"],
                headers={"Content-Length": "11", "ETag": "abc123"},
            )
        ]
    )
    dest = tmp_path / "cards.json"
    progress = []

    result = bulk.stream_download_to(
        str(dest),
        "https://example.com/cards.json",
        session_factory=lambda: session,
        progress_cb=lambda written, total: progress.append((written, total)),
    )

    assert result["status"] == "downloaded"
    assert dest.read_bytes() == b"hello world"
    assert dest.with_suffix(".json.etag").read_text(encoding="utf-8") == "abc123"
    assert progress[-1] == (11, 11)


def test_stream_download_to_returns_not_modified_when_etag_matches(tmp_path):
    session = _Session([_Response(status_code=304)])
    dest = tmp_path / "cards.json"
    etag_path = dest.with_suffix(".json.etag")
    etag_path.write_text("known-etag", encoding="utf-8")

    result = bulk.stream_download_to(
        str(dest),
        "https://example.com/cards.json",
        session_factory=lambda: session,
    )

    assert result["status"] == "not_modified"
    headers = session.calls[0][1]["headers"]
    assert headers["If-None-Match"] == "known-etag"


def test_cache_stats_reports_prints_and_rulings_files(tmp_path):
    prints_path = tmp_path / "prints.json"
    prints_path.write_text("[]", encoding="utf-8")
    rulings_path = tmp_path / "rulings.json"
    rulings_path.write_text("[]", encoding="utf-8")

    stats = bulk.cache_stats(
        default_cards_path_fn=lambda _path=None: str(prints_path),
        rulings_bulk_path_fn=lambda _path=None: str(rulings_path),
        default_is_stale_fn=lambda _path=None: False,
        rulings_is_stale_fn=lambda _path=None: True,
        prints_record_count=12,
        unique_set_count=4,
        unique_oracle_count=6,
        by_set_cn_count=12,
        by_set_num_count=9,
        by_name_count=8,
        by_front_count=7,
        rulings_oracle_key_count=3,
        rulings_entry_count=10,
    )

    assert stats["prints"]["exists"] is True
    assert stats["prints"]["records"] == 12
    assert stats["prints"]["index_sizes"]["by_front"] == 7
    assert stats["rulings"]["exists"] is True
    assert stats["rulings"]["oracle_keys"] == 3
    assert stats["rulings"]["entries"] == 10
