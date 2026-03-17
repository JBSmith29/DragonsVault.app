import re

from models import Card, Folder, FolderRole, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def test_folder_detail_curve_bucket_matches_row_bucket(client, create_user, app):
    user, password = create_user(
        email="curve_owner@example.com",
        username="curve_owner",
    )

    with app.app_context():
        deck = Folder(
            name="Curve Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
        )
        db.session.add(deck)
        db.session.flush()
        db.session.add(FolderRole(folder_id=deck.id, role=FolderRole.ROLE_DECK))

        card = Card(
            name="Fractional Dragon",
            set_code="TST",
            collector_number="13",
            folder_id=deck.id,
            quantity=1,
            mana_value=2.6,
            type_line="Creature — Dragon",
            oracle_id="oid-fractional-dragon",
            lang="en",
        )
        db.session.add(card)
        db.session.commit()
        deck_id = deck.id
        card_id = card.id

    _login(client, user.email, password)
    response = client.get(f"/folders/{deck_id}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'title="1 cards at 3 CMC"' in body
    assert re.search(
        rf'<tr class="deck-row"[^>]*data-card-id="{card_id}"[^>]*data-cmc-bucket="3"',
        body,
    )
