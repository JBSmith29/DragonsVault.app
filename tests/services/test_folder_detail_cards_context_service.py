from core.domains.decks.viewmodels.folder_vm import FolderCardVM
from models import Folder, FolderRole, db


def test_build_folder_detail_cards_context_merges_state_and_presentation(app, create_user, monkeypatch):
    from core.domains.decks.services import folder_detail_cards_context_service
    from core.domains.decks.services.folder_detail_card_presentation_service import FolderDetailCardPresentation
    from core.domains.decks.services.folder_detail_card_state_service import FolderDetailCardState

    user, _password = create_user(email="detail-cards@example.com", username="detail_cards")

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
        folder = db.session.get(Folder, folder.id)

    fake_card_vm = FolderCardVM(
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
    monkeypatch.setattr(
        folder_detail_cards_context_service,
        "build_folder_detail_card_state",
        lambda *args, **kwargs: FolderDetailCardState(
            deck_rows=[],
            print_map={1: {"name": "Arcane Signet"}},
            image_map={1: "small.png"},
            color_icons_map={1: ["C"]},
            cmc_map={1: 2.0},
            cmc_bucket_map={1: "2"},
            resolved_type_line_map={1: "Artifact"},
            resolved_rarity_map={1: "rare"},
            folder_tag_category="Core Archetypes",
            total_value_usd=2.5,
        ),
    )
    monkeypatch.setattr(
        folder_detail_cards_context_service,
        "build_folder_detail_card_presentation",
        lambda *args, **kwargs: FolderDetailCardPresentation(
            deck_cards=[fake_card_vm],
            card_groups=[{"label": "Artifacts", "cards": [], "count": 1}],
            card_image_lookup={1: "small.png"},
            curve_missing=0,
            curve_rows=[{"label": "2", "count": 1, "pct": 100}],
        ),
    )

    with app.app_context():
        context = folder_detail_cards_context_service.build_folder_detail_cards_context(
            folder,
            folder_id=folder.id,
            sort="cmc",
            reverse=False,
        )

    assert [card.name for card in context.deck_cards] == ["Arcane Signet"]
    assert context.total_value_usd == 2.5
    assert context.card_groups == [{"label": "Artifacts", "cards": [], "count": 1}]
    assert context.card_image_lookup == {1: "small.png"}
    assert context.curve_rows == [{"label": "2", "count": 1, "pct": 100}]
    assert context.folder_tag_category == "Core Archetypes"
