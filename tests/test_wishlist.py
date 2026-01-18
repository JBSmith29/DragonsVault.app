from models import WishlistItem, db


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
