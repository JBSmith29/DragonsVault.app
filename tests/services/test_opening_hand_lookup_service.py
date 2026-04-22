import json

from models import Card, Folder, FolderRole, db


def test_opening_hand_lookups_resolve_zone_hints_and_dedupe_tokens(app, create_user, monkeypatch):
    from core.domains.decks.services import opening_hand_lookup_service

    user, _password = create_user(
        email="opening-hand-lookups@example.com",
        username="opening-hand-lookups",
    )

    with app.app_context():
        folder = Folder(
            name="Lookup Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_DECK))
        db.session.add_all(
            [
                Card(
                    name="Card 1",
                    set_code="TST",
                    collector_number="1",
                    folder_id=folder.id,
                    quantity=1,
                    lang="en",
                    oracle_id="oid-card-1",
                ),
                Card(
                    name="Card 2",
                    set_code="TST",
                    collector_number="2",
                    folder_id=folder.id,
                    quantity=1,
                    lang="en",
                    oracle_id="oid-card-2",
                ),
            ]
        )
        db.session.commit()
        folder_id = folder.id

    def _fake_lookup_print_data(set_code, collector_number, name, oracle_id):
        if str(collector_number) == "1":
            return {
                "type_line": "Basic Land - Island",
                "oracle_text": "{T}: Add {U}.",
            }
        return {
            "type_line": "Creature - Human Wizard",
            "oracle_text": "When this enters, create a 1/1 Soldier token.",
        }

    def _fake_tokens_from_oracle(_oracle_id):
        return [
            {
                "id": "token-soldier-a",
                "name": "Soldier",
                "type_line": "Token Creature - Soldier",
                "images": {"normal": "https://example.com/soldier-a.png"},
            },
            {
                "id": "token-soldier-b",
                "name": "Soldier",
                "type_line": "Token Creature - Soldier",
                "images": {"normal": "https://example.com/soldier-b.png"},
            },
        ]

    monkeypatch.setattr(opening_hand_lookup_service, "_ensure_cache_ready", lambda: True)
    monkeypatch.setattr(opening_hand_lookup_service, "_lookup_print_data", _fake_lookup_print_data)
    monkeypatch.setattr(opening_hand_lookup_service.sc, "tokens_from_oracle", _fake_tokens_from_oracle)

    with app.app_context():
        cards_json, tokens_json = opening_hand_lookup_service._opening_hand_lookups([str(folder_id)])

    cards = json.loads(cards_json)[str(folder_id)]
    tokens = json.loads(tokens_json)[str(folder_id)]
    by_name = {card["name"]: card for card in cards}

    assert by_name["Card 1"]["zone_hint"] == "lands"
    assert by_name["Card 1"]["is_land"] is True
    assert by_name["Card 2"]["zone_hint"] == "creatures"
    assert by_name["Card 2"]["is_creature"] is True
    assert len(tokens) == 1
    assert tokens[0]["name"] == "Soldier"
    assert tokens[0]["zone_hint"] == "creatures"
