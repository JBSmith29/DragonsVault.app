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
