from models import Card, Folder, FolderRole, db


def test_analyze_folder_rows_uses_token_stub_fallback_and_counts(app, create_user, monkeypatch):
    from core.domains.decks.services import folder_detail_analysis_service

    user, _password = create_user(email="detail-analysis@example.com", username="detail_analysis")

    with app.app_context():
        folder = Folder(
            name="Analysis Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_DECK))
        db.session.add_all(
            [
                Card(
                    name="Treasure Maker",
                    set_code="TST",
                    collector_number="1",
                    folder_id=folder.id,
                    quantity=2,
                    oracle_id="oid-treasure-maker",
                    type_line="Artifact",
                    oracle_text="Create a Treasure token.",
                    mana_value=1,
                    lang="en",
                ),
                Card(
                    name="Sea Sage",
                    set_code="TST",
                    collector_number="2",
                    folder_id=folder.id,
                    quantity=1,
                    oracle_id="oid-sea-sage",
                    type_line="Creature — Merfolk",
                    oracle_text="Draw a card.",
                    mana_value=2,
                    lang="en",
                ),
            ]
        )
        db.session.commit()
        folder_id = folder.id

    monkeypatch.setattr(folder_detail_analysis_service, "cache_ready", lambda: True)
    monkeypatch.setattr(folder_detail_analysis_service.sc, "tokens_from_oracle", lambda oracle_id: [])

    with app.app_context():
        analysis = folder_detail_analysis_service.analyze_folder_rows(folder_id)

    assert analysis.total_rows == 2
    assert analysis.total_qty == 3
    assert ("Artifact", 2) in analysis.type_breakdown
    assert ("Creature", 1) in analysis.type_breakdown
    assert len(analysis.bracket_cards) == 2
    assert analysis.bracket_cards[0]["quantity"] == 2
    assert len(analysis.deck_tokens) == 1
    token = analysis.deck_tokens[0]
    assert token["name"] == "Treasure"
    assert token["count"] == 2
    assert len(token["sources"]) == 1
    assert token["sources"][0]["name"] == "Treasure Maker"
    assert token["sources"][0]["qty"] == 2
    assert token["sources"][0]["img"] is None


def test_analyze_folder_rows_falls_back_to_print_payload(app, create_user, monkeypatch):
    from core.domains.decks.services import folder_detail_analysis_service

    user, _password = create_user(email="detail-print@example.com", username="detail_print")

    with app.app_context():
        folder = Folder(
            name="Print Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_DECK))
        db.session.add(
            Card(
                name="Mysterious Spell",
                set_code="TST",
                collector_number="77",
                folder_id=folder.id,
                quantity=1,
                oracle_id="oid-mysterious-spell",
                type_line=None,
                oracle_text=None,
                mana_value=None,
                lang="en",
            )
        )
        db.session.commit()
        folder_id = folder.id

    monkeypatch.setattr(folder_detail_analysis_service, "cache_ready", lambda: True)
    monkeypatch.setattr(folder_detail_analysis_service.sc, "tokens_from_oracle", lambda oracle_id: [])
    monkeypatch.setattr(
        folder_detail_analysis_service,
        "find_by_set_cn",
        lambda set_code, collector_number, name: {
            "type_line": "Sorcery",
            "oracle_text": "Create a Clue token.",
            "mana_cost": "{3}",
            "cmc": 3,
        },
    )

    with app.app_context():
        analysis = folder_detail_analysis_service.analyze_folder_rows(folder_id)

    assert analysis.total_rows == 1
    assert analysis.total_qty == 1
    assert analysis.type_breakdown == [("Sorcery", 1)]
    assert analysis.bracket_cards[0]["oracle_text"] == "Create a Clue token."
    assert analysis.bracket_cards[0]["mana_cost"] == "{3}"
    assert analysis.bracket_cards[0]["mana_value"] == 3
    assert analysis.deck_tokens[0]["name"] == "Clue"
