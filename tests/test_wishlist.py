from models import Card, Folder, FolderRole, WishlistItem, db


def test_wishlist_page(client, create_user):
    user, password = create_user(email="wishlist@example.com")
    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )
    response = client.get("/wishlist")
    assert response.status_code == 200


def test_add_to_wishlist(client, create_user):
    user, password = create_user(email="wishlist2@example.com")
    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )
    response = client.post("/wishlist/add", data={"name": "Wheel of Fortune"}, follow_redirects=True)
    assert response.status_code == 200


def test_update_order_ref(client, create_user):
    user, password = create_user(email="wishlist3@example.com")
    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )
    item = WishlistItem(name="Order Test", requested_qty=1, missing_qty=1, status="ordered")
    db.session.add(item)
    db.session.commit()

    response = client.post(
        f"/wishlist/order/{item.id}",
        data={"order_ref": "https://example.com/orders/123"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    refreshed = db.session.get(WishlistItem, item.id)
    assert refreshed.order_ref == "https://example.com/orders/123"


def test_wishlist_prefers_collection_print_for_thumb_and_rarity(client, create_user):
    user, password = create_user(email="wishlist4@example.com")
    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )

    folder = Folder(name="Trade Binder", category=Folder.CATEGORY_COLLECTION, owner_user_id=user.id)
    db.session.add(folder)
    db.session.flush()

    db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_COLLECTION))

    card = Card(
        name="Lightning Bolt",
        set_code="m11",
        collector_number="146",
        folder_id=folder.id,
        quantity=1,
        rarity="rare",
    )
    db.session.add(card)
    db.session.flush()

    item = WishlistItem(
        name="Lightning Bolt",
        requested_qty=1,
        missing_qty=1,
        status="to_fetch",
        card_id=card.id,
    )
    db.session.add(item)
    db.session.commit()

    response = client.get("/wishlist")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert f'data-card-id="{card.id}"' in html
    assert f'data-rarity-key="{item.id}"' in html
    assert ">Rare<" in html

