from core.domains.decks.viewmodels.folder_vm import FolderCardVM, FolderOptionVM, FolderVM
from models import Folder, FolderRole, db


def test_build_folder_detail_page_context_merges_context_builders(app, create_user, monkeypatch):
    from core.domains.decks.services import folder_detail_page_context_service

    user, _password = create_user(email="detail-context@example.com", username="detail_context")

    with app.app_context():
        folder = Folder(
            name="Context Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_DECK))
        db.session.commit()
        folder_id = folder.id

    fake_folder_vm = FolderVM(
        id=folder_id,
        name="Context Deck",
        category=Folder.CATEGORY_DECK,
        category_label="Deck",
        owner=None,
        owner_label=None,
        owner_user_id=user.id,
        is_collection=False,
        is_deck=True,
        is_proxy=False,
        is_public=False,
        deck_tag=None,
        deck_tag_label=None,
        commander_name=None,
        commander_oracle_id=None,
        commander_slot_count=0,
    )
    monkeypatch.setattr(
        folder_detail_page_context_service,
        "build_folder_detail_cards_context",
        lambda *args, **kwargs: type(
            "FakeCardsContext",
            (),
            {
                "deck_rows": [],
                "print_map": {},
                "deck_cards": [
                    FolderCardVM(
                        id=1,
                        name="Arcane Signet",
                        display_name="Arcane Signet",
                        set_code="TST",
                        collector_number="20",
                        lang="en",
                        is_foil=False,
                        quantity=1,
                        type_line="Artifact",
                    )
                ],
                "card_groups": [{"label": "Artifacts", "cards": [], "count": 1}],
                "card_image_lookup": {1: "small.png"},
                "curve_missing": 0,
                "curve_rows": [{"label": "2", "count": 1, "pct": 100}],
                "folder_tag_category": "Core Archetypes",
                "total_value_usd": 2.5,
            },
        )(),
    )
    monkeypatch.setattr(
        folder_detail_page_context_service,
        "build_folder_detail_folder_shell",
        lambda *args, **kwargs: type(
            "FakeFolderShell",
            (),
            {
                "folder": fake_folder_vm,
                "move_targets": [FolderOptionVM(id=2, name="Move Target")],
            },
        )(),
    )
    monkeypatch.setattr(
        folder_detail_page_context_service,
        "build_folder_detail_commander_context",
        lambda *args, **kwargs: {
            "bracket_card_links": {"arcane signet": 1},
            "commander_bracket": {"summary": "Bracket"},
            "commander_media": {"name": "Commander"},
            "commander_media_list": [{"name": "Commander"}],
            "edhrec_commander_ready": True,
            "edhrec_ready": True,
            "edhrec_sections": [{"label": "High Synergy", "cards": []}],
            "edhrec_tag_label": "Artifacts",
            "is_deck_folder": True,
        },
    )
    monkeypatch.setattr(folder_detail_page_context_service, "get_deck_tag_groups", lambda: {"Core": ["Artifacts"]})

    with app.test_request_context(f"/folders/{folder_id}?sort=cmc"):
        with app.app_context():
            folder = db.session.get(Folder, folder_id)
            context = folder_detail_page_context_service.build_folder_detail_page_context(
                folder,
                folder_id=folder_id,
                sort="cmc",
                reverse=False,
                bracket_cards=[],
            )

    assert [card.name for card in context["deck_cards"]] == ["Arcane Signet"]
    assert context["total_value_usd"] == 2.5
    assert context["curve_rows"] == [{"label": "2", "count": 1, "pct": 100}]
    assert context["card_groups"] == [{"label": "Artifacts", "cards": [], "count": 1}]
    assert context["folder"].name == "Context Deck"
    assert [option.name for option in context["move_targets"]] == ["Move Target"]
    assert context["deck_tag_groups"] == {"Core": ["Artifacts"]}
    assert context["bracket_card_links"] == {"arcane signet": 1}
    assert context["cards_link"].endswith(f"/cards?folder={folder_id}")
