def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def test_deck_from_collection_page_renders(client, create_user):
    user, password = create_user(email="deck-from-collection@example.com", username="deck_from_collection")

    _login(client, user.email, password)
    response = client.get("/decks/from-collection")

    assert response.status_code == 200
    assert b"Create Deck from Collection" in response.data


def test_deck_from_collection_post_requires_deck_name(client, create_user):
    user, password = create_user(email="deck-from-collection-post@example.com", username="deck_from_collection_post")

    _login(client, user.email, password)
    response = client.post(
        "/decks/from-collection",
        data={"deck_name": "", "deck_lines": "1 Sol Ring", "stage": "input"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Deck name is required." in response.data
