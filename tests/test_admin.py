def _login_admin(client, create_user):
    user, password = create_user(email="admin@example.com", username="admin", is_admin=True)
    client.post("/login", data={"identifier": user.email, "password": password}, follow_redirects=True)


def test_admin_page_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin")
    assert response.status_code == 200

def test_refresh_rulings_post(client, create_user):
    _login_admin(client, create_user)
    response = client.post("/admin", data={"action": "refresh_rulings"})
    assert response.status_code in (200, 302)

def test_clear_cache_post(client, create_user):
    _login_admin(client, create_user)
    response = client.post("/admin", data={"action": "clear_cache"})
    assert response.status_code in (200, 302)


def test_create_user_via_admin(client, create_user, app):
    from models import User

    _login_admin(client, create_user)
    resp = client.post(
        "/admin",
        data={
            "action": "create_user",
            "user_username": "new_user",
            "user_email": "new_user@example.com",
            "user_password": "hunter2",
            "user_display_name": "New User",
            "user_is_admin": "1",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        new_user = User.query.filter_by(email="new_user@example.com").first()
        assert new_user is not None
        assert new_user.is_admin
        assert new_user.username == "new_user"
