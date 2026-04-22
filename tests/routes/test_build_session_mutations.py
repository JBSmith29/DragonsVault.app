from extensions import db
from models import BuildSession, BuildSessionCard


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_build_session(
    app,
    *,
    owner_user_id: int,
    commander_oracle_id: str = "oid-commander",
    commander_name: str = "Commander",
    tags_json=None,
) -> int:
    with app.app_context():
        session = BuildSession(
            owner_user_id=owner_user_id,
            commander_oracle_id=commander_oracle_id,
            commander_name=commander_name,
            build_name="Test Build",
            tags_json=tags_json,
        )
        db.session.add(session)
        db.session.commit()
        return session.id


def test_build_session_start_creates_session_and_commander_card(client, create_user, app):
    user, password = create_user(email="build_start@example.com", username="buildstart")
    _login(client, user.email, password)

    response = client.post(
        "/decks/build/start",
        data={
            "commander_oracle_id": "oid-atraxa",
            "commander_name": "Atraxa",
            "build_name": "Counters",
            "deck_tags": ["Counters", "Counters", "Proliferate"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        session = BuildSession.query.filter_by(owner_user_id=user.id, commander_oracle_id="oid-atraxa").first()
        assert session is not None
        assert session.build_name == "Counters"
        assert session.tags_json == ["Counters", "Proliferate"]
        commander_entry = BuildSessionCard.query.filter_by(
            session_id=session.id,
            card_oracle_id="oid-atraxa",
        ).first()
        assert commander_entry is not None
        assert commander_entry.quantity == 1


def test_build_session_add_bulk_deduplicates_oracle_ids(client, create_user, app):
    user, password = create_user(email="build_bulk@example.com", username="buildbulk")
    session_id = _create_build_session(app, owner_user_id=user.id)
    _login(client, user.email, password)

    response = client.post(
        f"/decks/build/{session_id}/cards/add-bulk",
        data={"card_oracle_id": ["oid-alpha", "OID-ALPHA", "oid-beta", "oid-beta"]},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        alpha_entries = BuildSessionCard.query.filter_by(session_id=session_id, card_oracle_id="oid-alpha").all()
        beta_entries = BuildSessionCard.query.filter_by(session_id=session_id, card_oracle_id="oid-beta").all()
        assert len(alpha_entries) == 1
        assert alpha_entries[0].quantity == 1
        assert len(beta_entries) == 1
        assert beta_entries[0].quantity == 1


def test_build_session_manual_add_aggregates_resolved_cards(client, create_user, app, monkeypatch):
    from core.domains.decks.services import build_session_mutation_service as mutation_service

    user, password = create_user(email="build_manual@example.com", username="buildmanual")
    session_id = _create_build_session(app, owner_user_id=user.id)
    _login(client, user.email, password)

    monkeypatch.setattr(
        mutation_service,
        "parse_decklist",
        lambda lines: [("Alpha", 2), ("Unknown", 1), ("Alpha", 1), ("Beta", 3)],
    )
    monkeypatch.setattr(mutation_service.sc, "ensure_cache_loaded", lambda: None)
    monkeypatch.setattr(
        mutation_service.sc,
        "unique_oracle_by_name",
        lambda name: {"Alpha": "oid-alpha", "Beta": "oid-beta"}.get(name),
    )

    response = client.post(
        f"/decks/build/{session_id}/cards/manual-add",
        data={"card_list": "Alpha\nUnknown\nBeta"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        alpha_entry = BuildSessionCard.query.filter_by(session_id=session_id, card_oracle_id="oid-alpha").first()
        beta_entry = BuildSessionCard.query.filter_by(session_id=session_id, card_oracle_id="oid-beta").first()
        assert alpha_entry is not None
        assert alpha_entry.quantity == 3
        assert beta_entry is not None
        assert beta_entry.quantity == 3


def test_build_session_refresh_edhrec_requires_tag_for_json(client, create_user, app):
    user, password = create_user(email="build_edhrec@example.com", username="buildedhrec")
    session_id = _create_build_session(
        app,
        owner_user_id=user.id,
        commander_oracle_id="oid-commander",
        commander_name="Commander",
        tags_json=None,
    )
    _login(client, user.email, password)

    response = client.post(
        f"/decks/build/{session_id}/edhrec",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "ok": False,
        "error": "Set at least one deck tag before loading EDHREC data.",
    }
