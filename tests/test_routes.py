def _login(client, create_user):
    user, password = create_user()
    client.post("/login", data={"identifier": user.email, "password": password}, follow_redirects=True)
    return user


def test_home_page(client):
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    assert b"DragonsVault" in response.data
    assert b"Create an account" in response.data


def test_cards_page(client, create_user):
    _login(client, create_user)
    response = client.get("/cards")
    assert response.status_code == 200


def test_dashboard_page(client, create_user):
    _login(client, create_user)
    response = client.get("/dashboard")
    assert response.status_code == 200


def test_collection_page(client, create_user):
    _login(client, create_user)
    response = client.get("/collection")
    assert response.status_code == 200


def test_sets_page(client, create_user):
    _login(client, create_user)
    response = client.get("/sets")
    assert response.status_code == 200


def test_search_route(client, create_user):
    _login(client, create_user)
    response = client.get("/cards?q=Sol+Ring")
    assert response.status_code == 200
