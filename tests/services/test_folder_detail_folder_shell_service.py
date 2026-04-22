from models import Folder, FolderRole, db


def test_build_folder_detail_folder_shell_builds_folder_vm_and_move_targets(app, create_user):
    from core.domains.decks.services import folder_detail_folder_shell_service

    user, _password = create_user(email="detail-shell@example.com", username="detail_shell")

    with app.app_context():
        active_folder = Folder(
            name="Alpha Deck",
            category=Folder.CATEGORY_DECK,
            owner="detail_shell",
            owner_user_id=user.id,
            commander_name="Alpha // Beta",
            commander_oracle_id="oid-alpha // oid-beta",
            deck_tag="Artifacts",
            notes="shell notes",
            is_public=True,
        )
        target_folder = Folder(
            name="Zulu Binder",
            category=Folder.CATEGORY_COLLECTION,
            owner_user_id=user.id,
        )
        db.session.add_all([active_folder, target_folder])
        db.session.flush()
        db.session.add(FolderRole(folder_id=active_folder.id, role=FolderRole.ROLE_DECK))
        db.session.add(FolderRole(folder_id=target_folder.id, role=FolderRole.ROLE_COLLECTION))
        db.session.commit()

        active_folder = db.session.get(Folder, active_folder.id)
        shell = folder_detail_folder_shell_service.build_folder_detail_folder_shell(active_folder)

    assert shell.folder.name == "Alpha Deck"
    assert shell.folder.category_label == "Deck"
    assert shell.folder.commander_slot_count == 2
    assert shell.folder.role_labels == ["Deck"]
    assert [option.name for option in shell.move_targets] == ["Zulu Binder"]

