from models import BuildSession, Card, Folder, FolderRole, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_build_session(app, *, owner_user_id: int) -> int:
    with app.app_context():
        session = BuildSession(
            owner_user_id=owner_user_id,
            commander_oracle_id="oid-commander",
            commander_name="Commander",
            build_name="Test Build",
        )
        db.session.add(session)
        db.session.commit()
        return session.id


def _create_folder_with_card(
    app,
    *,
    owner_user_id: int,
    name: str,
    category: str,
    role: str,
    oracle_id: str,
    card_name: str = "Test Card",
) -> int:
    with app.app_context():
        folder = Folder(
            name=name,
            category=category,
            owner_user_id=owner_user_id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=role))
        db.session.add(
            Card(
                name=card_name,
                set_code="TST",
                collector_number="1",
                folder_id=folder.id,
                quantity=1,
                oracle_id=oracle_id,
                lang="en",
            )
        )
        db.session.commit()
        return folder.id


def _patch_build_session_page(monkeypatch):
    from core.domains.decks.services import build_session_service

    monkeypatch.setattr(
        build_session_service,
        "_oracle_payload",
        lambda oracle_id, fallback=None: {
            "oracle_id": oracle_id,
            "name": fallback or oracle_id or "",
            "image": None,
            "colors": [],
        },
    )
    monkeypatch.setattr(build_session_service, "_session_cards", lambda entries: [])
    monkeypatch.setattr(build_session_service, "_build_oracle_ids", lambda entries: set())
    monkeypatch.setattr(build_session_service, "_group_session_cards_by_type", lambda cards: [])
    monkeypatch.setattr(
        build_session_service,
        "_deck_metrics",
        lambda entries: {
            "total_cards": 0,
            "land_count": 0,
            "non_land_count": 0,
            "mana_pip_dist": [],
            "land_mana_sources": [],
            "curve_rows": [],
            "missing_cmc": 0,
            "deck_health": [],
            "role_needs": set(),
            "phase": "exploration",
        },
    )
    monkeypatch.setattr(build_session_service, "_type_breakdown_for_entries", lambda entries: [])
    monkeypatch.setattr(build_session_service, "_distribution_breakdown_for_entries", lambda entries: [])
    monkeypatch.setattr(build_session_service, "_edhrec_type_breakdown", lambda commander_oracle_id, tags: [])
    monkeypatch.setattr(build_session_service, "_build_session_bracket_context", lambda session, entries: {})
    monkeypatch.setattr(build_session_service, "get_deck_tag_groups", lambda: {})
    monkeypatch.setattr(
        build_session_service,
        "build_recommendation_sections",
        lambda *args, **kwargs: [
            {
                "key": "top-cards",
                "label": "Top Cards",
                "description": "",
                "default_open": True,
                "count": 1,
                "cards": [
                    {
                        "oracle_id": "oid-test-card",
                        "name": "Test Card",
                        "image": None,
                        "synergy_percent": None,
                        "inclusion_percent": None,
                        "price_text": None,
                        "reasons": [],
                        "in_build": False,
                        "is_basic_land": False,
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(build_session_service, "_collection_recommendation_sections", lambda *args, **kwargs: [])
    monkeypatch.setattr(build_session_service, "_mark_build_cards", lambda *args, **kwargs: None)


def test_build_session_only_marks_collection_cards_available(client, create_user, app, monkeypatch):
    user, password = create_user(
        email="build_session_deck_only@example.com",
        username="build_session_deck_only",
    )
    session_id = _create_build_session(app, owner_user_id=user.id)
    _create_folder_with_card(
        app,
        owner_user_id=user.id,
        name="Active Deck",
        category=Folder.CATEGORY_DECK,
        role=FolderRole.ROLE_DECK,
        oracle_id="oid-test-card",
    )
    _patch_build_session_page(monkeypatch)

    _login(client, user.email, password)
    response = client.get(f"/decks/build/{session_id}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'badge rounded-pill bg-success-subtle text-success-emphasis text-nowrap">Available<' not in body


def test_build_session_marks_collection_cards_available(client, create_user, app, monkeypatch):
    user, password = create_user(
        email="build_session_collection@example.com",
        username="build_session_collection",
    )
    session_id = _create_build_session(app, owner_user_id=user.id)
    _create_folder_with_card(
        app,
        owner_user_id=user.id,
        name="Binder",
        category=Folder.CATEGORY_COLLECTION,
        role=FolderRole.ROLE_COLLECTION,
        oracle_id="oid-test-card",
    )
    _patch_build_session_page(monkeypatch)

    _login(client, user.email, password)
    response = client.get(f"/decks/build/{session_id}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'badge rounded-pill bg-success-subtle text-success-emphasis text-nowrap">Available<' in body
