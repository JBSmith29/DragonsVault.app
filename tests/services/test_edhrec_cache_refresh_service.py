from __future__ import annotations

from types import SimpleNamespace

from models import EdhrecCommanderCard, EdhrecCommanderTag, EdhrecCommanderTagCard, EdhrecTagCommander, db


def test_refresh_edhrec_cache_returns_info_when_no_targets(monkeypatch):
    from core.domains.decks.services import edhrec_cache_refresh_service as refresh_service

    monkeypatch.setattr(refresh_service, "edhrec_service_enabled", lambda: True)
    monkeypatch.setattr(
        refresh_service.target_service,
        "collect_edhrec_targets",
        lambda: {"commanders": [], "tags": [], "deck_total": 0},
    )

    result = refresh_service.refresh_edhrec_cache(
        scope="owned",
        ensure_tables_fn=lambda: None,
    )

    assert result["status"] == "info"
    assert result["message"] == "No commander data found to refresh."
    assert result["targets"]["deck_total"] == 0


def test_refresh_edhrec_cache_persists_commander_and_tag_rows(app, db_session, monkeypatch):
    from core.domains.decks.services import edhrec_cache_refresh_service as refresh_service

    monkeypatch.setattr(refresh_service, "edhrec_service_enabled", lambda: True)
    monkeypatch.setattr(refresh_service.sc, "ensure_cache_loaded", lambda: True)
    monkeypatch.setattr(
        refresh_service.target_service,
        "collect_edhrec_targets",
        lambda: {
            "commanders": [{"oracle_id": "oid-atraxa", "name": "Atraxa, Praetors' Voice"}],
            "tags": ["Blink"],
        },
    )
    monkeypatch.setattr(
        refresh_service.target_service,
        "extract_commander_tag_entries",
        lambda payload: [{"tag": "Blink", "slug": "blink"}] if payload.get("kind") == "base" else [],
    )

    payloads = {
        ("Atraxa, Praetors' Voice", None): ("atraxa-praetors-voice", {"kind": "base"}, None),
        ("Atraxa, Praetors' Voice", "blink"): ("atraxa-praetors-voice", {"kind": "blink"}, None),
    }

    def _ensure_commander_data(name, force_refresh=False, slug_override=None, theme_slug=None):  # noqa: ARG001
        return payloads[(name, theme_slug)]

    def _commander_cardviews(payload):
        if payload.get("kind") == "blink":
            return [SimpleNamespace(name="Ephemerate", synergy=0.21, inclusion=18.0)]
        return [SimpleNamespace(name="Sol Ring", synergy=0.42, inclusion=65.0)]

    monkeypatch.setattr(refresh_service, "ensure_commander_data", _ensure_commander_data)
    monkeypatch.setattr(refresh_service, "commander_cardviews", _commander_cardviews)
    monkeypatch.setattr(
        refresh_service.sc,
        "unique_oracle_by_name",
        lambda name: {
            "Sol Ring": "oid-sol-ring",
            "Ephemerate": "oid-ephemerate",
        }.get(name),
    )

    with app.app_context():
        result = refresh_service.refresh_edhrec_cache(
            scope="owned",
            ensure_tables_fn=lambda: None,
        )

        commander_rows = EdhrecCommanderCard.query.all()
        tag_rows = EdhrecCommanderTag.query.all()
        tag_card_rows = EdhrecCommanderTagCard.query.all()
        reverse_rows = EdhrecTagCommander.query.all()

    assert result["status"] == "success"
    assert result["commanders"] == {"requested": 1, "ok": 1, "cards": 1}
    assert result["tags"]["links"] == 1
    assert result["tags"]["tag_cards"] == 1

    assert len(commander_rows) == 1
    assert commander_rows[0].commander_oracle_id == "oid-atraxa"
    assert commander_rows[0].card_oracle_id == "oid-sol-ring"

    assert len(tag_rows) == 1
    assert tag_rows[0].tag == "Blink"

    assert len(tag_card_rows) == 1
    assert tag_card_rows[0].tag == "Blink"
    assert tag_card_rows[0].card_oracle_id == "oid-ephemerate"

    assert len(reverse_rows) == 1
    assert reverse_rows[0].tag == "Blink"
    assert reverse_rows[0].commander_oracle_id == "oid-atraxa"
