import io

from models import Card, Folder
from core.domains.cards.services.csv_importer import process_csv


def test_import_page_loads(client, create_user):
    user, password = create_user(email="importer@example.com", is_admin=True)
    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )
    response = client.get("/import")
    assert response.status_code == 200
    assert b"Import / Export Cards" in response.data


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
            "quantity_mode": "new_only",
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
            quantity_mode="new_only",
            owner_user_id=user.id,
            owner_username=user.username,
        )
        assert stats.added == 1
        folder = Folder.query.filter_by(name="Owner Folder").first()
        assert folder is not None
        assert folder.owner_user_id == user.id
        assert folder.owner == user.username


def test_process_csv_allows_duplicate_folder_names_per_user(app, create_user, tmp_path):
    user_one, _ = create_user(email="one@example.com", username="playerone")
    user_two, _ = create_user(email="two@example.com", username="playertwo")
    csv_path = tmp_path / "dupe.csv"
    csv_path.write_text(
        "Folder Name,Card Name,Set Code,Collector Number,Quantity\n"
        "Shared Folder,Lightning Bolt,M11,146,3\n",
        encoding="utf-8",
    )
    with app.app_context():
        stats_one, _ = process_csv(
            str(csv_path),
            default_folder="Unsorted",
            dry_run=False,
            quantity_mode="new_only",
            owner_user_id=user_one.id,
            owner_username=user_one.username,
        )
        assert stats_one.added == 1

        stats_two, _ = process_csv(
            str(csv_path),
            default_folder="Unsorted",
            dry_run=False,
            quantity_mode="new_only",
            owner_user_id=user_two.id,
            owner_username=user_two.username,
        )
        assert stats_two.added == 1

        folder_one = Folder.query.filter_by(name="Shared Folder", owner_user_id=user_one.id).first()
        folder_two = Folder.query.filter_by(name="Shared Folder", owner_user_id=user_two.id).first()
        assert folder_one is not None
        assert folder_two is not None
        assert folder_one.id != folder_two.id
