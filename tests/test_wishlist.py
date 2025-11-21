def test_wishlist_page(client):
    response = client.get("/wishlist")
    assert response.status_code == 200

def test_add_to_wishlist(client):
    response = client.post("/wishlist/add", data={"name": "Wheel of Fortune"}, follow_redirects=True)
    assert response.status_code == 200
