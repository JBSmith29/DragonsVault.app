from models import Card, Folder, FolderRole, db


def test_build_folder_detail_card_state_sorts_rows_and_computes_price(app, create_user, monkeypatch):
    from core.domains.decks.services import folder_detail_card_state_service

    user, _password = create_user(email="detail-card-state@example.com", username="detail_card_state")

    with app.app_context():
        folder = Folder(
            name="Context Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_DECK))
        db.session.add_all(
            [
                Card(
                    name="Late Dragon",
                    set_code="TST",
                    collector_number="10",
                    folder_id=folder.id,
                    quantity=1,
                    oracle_id="oid-late-dragon",
                    type_line="Creature — Dragon",
                    mana_value=5,
                    lang="en",
                ),
                Card(
                    name="Arcane Signet",
                    set_code="TST",
                    collector_number="20",
                    folder_id=folder.id,
                    quantity=2,
                    oracle_id="oid-arcane-signet",
                    type_line="Artifact",
                    mana_value=2,
                    lang="en",
                    is_foil=True,
                ),
            ]
        )
        db.session.commit()
        folder = db.session.get(Folder, folder.id)

    monkeypatch.setattr(folder_detail_card_state_service.sc, "cache_ready", lambda: True)
    monkeypatch.setattr(folder_detail_card_state_service.sc, "image_for_print", lambda payload: {})
    monkeypatch.setattr(folder_detail_card_state_service, "_bulk_print_lookup", lambda cards, **kwargs: {})
    monkeypatch.setattr(folder_detail_card_state_service, "_prices_for_print", lambda payload: {"usd": "1.5", "usd_foil": "2.5"})

    with app.app_context():
        state = folder_detail_card_state_service.build_folder_detail_card_state(
            folder,
            folder_id=folder.id,
            sort="cmc",
            reverse=False,
        )

    assert [card.name for card in state.deck_rows] == ["Arcane Signet", "Late Dragon"]
    assert state.total_value_usd == 6.5
    assert state.cmc_bucket_map[state.deck_rows[0].id] == "2"
    assert state.cmc_bucket_map[state.deck_rows[1].id] == "5"
    assert state.resolved_type_line_map[state.deck_rows[0].id] == "Artifact"
    assert state.resolved_type_line_map[state.deck_rows[1].id] == "Creature — Dragon"

