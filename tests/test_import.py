import io

from models import Card, Folder
from services.csv_importer import process_csv


def test_import_csv_upload(client, create_user):
    user, password = create_user(email="importer@example.com", is_admin=True)
    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )
    dummy_csv = io.BytesIO(b"Card Name,Set,Quantity\nLightning Bolt,M11,4")
    data = {"file": (dummy_csv, "test.csv"), "action": "preview"}
    response = client.post("/import", data=data, content_type="multipart/form-data")
    assert response.status_code == 200


def test_import_confirm_runs_inline(client, create_user, app):
    app.config["IMPORT_RUN_INLINE"] = True
    user, password = create_user(email="inline@example.com", is_admin=True)
    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )
    csv_bytes = io.BytesIO(b"Card Name,Set,Collector Number,Quantity\nSol Ring,2XM,1,1")
    response = client.post(
        "/import",
        data={
            "action": "confirm",
            "quantity_mode": "absolute",
            "file": (csv_bytes, "inline.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Import applied immediately" in response.data
    with app.app_context():
        card = Card.query.filter_by(name="Sol Ring").first()
        assert card is not None
        assert card.quantity == 1


def test_process_csv_sets_folder_owner_username(app, create_user, tmp_path):
    user, _password = create_user(email="owner@example.com", username="deckowner")
    csv_path = tmp_path / "owner.csv"
    csv_path.write_text(
        "Folder Name,Card Name,Set Code,Collector Number,Quantity\n"
        "Owner Folder,Sol Ring,2XM,1,1\n",
        encoding="utf-8",
    )

    with app.app_context():
        stats, _ = process_csv(
            str(csv_path),
            default_folder="Unsorted",
            dry_run=False,
            quantity_mode="absolute",
            owner_user_id=user.id,
            owner_username=user.username,
        )
        assert stats.added == 1
        folder = Folder.query.filter_by(name="Owner Folder").first()
        assert folder is not None
        assert folder.owner_user_id == user.id
        assert folder.owner == user.username
