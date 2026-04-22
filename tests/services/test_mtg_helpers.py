from flask_login import login_user, logout_user

from models import Card, Folder, FolderRole, db
from shared.mtg import (
    _collection_rows_with_fallback,
    _move_folder_choices,
    _normalize_name,
    color_identity_name,
    compute_folder_color_identity,
)


def test_normalize_name_handles_quotes_unicode_and_spacing():
    assert _normalize_name('  "Krenko’s   Command"  ') == "krenko's command"


def test_color_identity_name_maps_known_groups_and_colorless():
    assert color_identity_name("WU") == "Azorius"
    assert color_identity_name([]) == "Colorless"


def test_collection_rows_with_fallback_returns_collection_folder_rows(app, create_user):
    user, _password = create_user(email="collector@example.com", username="collector")

    with app.app_context():
        folder = Folder(
            name="Main Collection",
            category=Folder.CATEGORY_COLLECTION,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_COLLECTION))
        db.session.commit()

        rows = _collection_rows_with_fallback(owner_user_ids=[user.id])

    assert rows == [(folder.id, "Main Collection")]


def test_collection_rows_with_fallback_returns_defaults_for_empty_install(app, db_session):
    with app.app_context():
        rows = _collection_rows_with_fallback()

    assert rows
    assert all(folder_id is None for folder_id, _name in rows)


def test_move_folder_choices_include_owned_and_ownerless_folders(app, create_user):
    user, _password = create_user(email="owner@example.com", username="owner")
    other_user, _other_password = create_user(email="other@example.com", username="other")

    with app.app_context():
        db.session.add_all(
            [
                Folder(name="Owned Deck", category=Folder.CATEGORY_DECK, owner_user_id=user.id),
                Folder(name="Shared Bin", category=Folder.CATEGORY_COLLECTION, owner_user_id=None),
                Folder(name="Other Deck", category=Folder.CATEGORY_DECK, owner_user_id=other_user.id),
            ]
        )
        db.session.commit()

    with app.test_request_context("/folders"):
        login_user(user)
        options = _move_folder_choices()
        logout_user()

    option_names = {option.name for option in options}
    assert "Owned Deck" in option_names
    assert "Shared Bin" in option_names
    assert "Other Deck" not in option_names


def test_compute_folder_color_identity_uses_card_color_fields(app, create_user):
    user, _password = create_user(email="colors@example.com", username="colors")

    with app.app_context():
        folder = Folder(name="Azorius Deck", category=Folder.CATEGORY_DECK, owner_user_id=user.id)
        db.session.add(folder)
        db.session.flush()
        db.session.add_all(
            [
                Card(
                    name="Plains Mage",
                    set_code="TST",
                    collector_number="1",
                    folder_id=folder.id,
                    quantity=1,
                    color_identity="W",
                ),
                Card(
                    name="Island Mage",
                    set_code="TST",
                    collector_number="2",
                    folder_id=folder.id,
                    quantity=1,
                    colors="U",
                ),
            ]
        )
        db.session.commit()

        letters, label = compute_folder_color_identity(folder.id, cache_version=f"test-{folder.id}")

    assert letters == "WU"
    assert label == "Azorius"
