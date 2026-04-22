from models import Card, Folder


def test_build_folder_detail_card_presentation_builds_viewmodels_groups_and_curve(monkeypatch):
    from core.domains.decks.services import folder_detail_card_presentation_service
    from core.domains.decks.services.folder_detail_card_state_service import FolderDetailCardState

    folder = Folder(name="Context Deck", deck_tag="Artifacts", category=Folder.CATEGORY_DECK)
    card_one = Card(
        id=1,
        name="Arcane Signet",
        set_code="TST",
        collector_number="20",
        lang="en",
        is_foil=True,
        quantity=2,
        type_line="Artifact",
        mana_value=2,
    )
    card_two = Card(
        id=2,
        name="Late Dragon",
        set_code="TST",
        collector_number="10",
        lang="en",
        is_foil=False,
        quantity=1,
        type_line="Creature — Dragon",
        mana_value=5,
    )
    state = FolderDetailCardState(
        deck_rows=[card_one, card_two],
        print_map={
            1: {"name": "Arcane Signet"},
            2: {"name": "Late Dragon"},
        },
        image_map={1: "arcane-fallback.png", 2: None},
        color_icons_map={1: ["C"], 2: ["R"]},
        cmc_map={1: 2.0, 2: 5.0},
        cmc_bucket_map={1: "2", 2: "5"},
        resolved_type_line_map={1: "Artifact", 2: "Creature — Dragon"},
        resolved_rarity_map={1: "uncommon", 2: "rare"},
        folder_tag_category="Core Archetypes",
        total_value_usd=6.5,
    )

    monkeypatch.setattr(
        folder_detail_card_presentation_service.sc,
        "image_for_print",
        lambda payload: {
            "small": f"{payload['name']}-small.png",
            "normal": f"{payload['name']}-normal.png",
            "large": f"{payload['name']}-large.png",
        },
    )

    presentation = folder_detail_card_presentation_service.build_folder_detail_card_presentation(folder, state=state)

    assert [card.name for card in presentation.deck_cards] == ["Arcane Signet", "Late Dragon"]
    assert presentation.deck_cards[0].data_tags == "Core Archetypes Artifacts"
    assert presentation.card_image_lookup == {
        1: "Arcane Signet-small.png",
        2: "Late Dragon-small.png",
    }
    curve_by_label = {row["label"]: row["count"] for row in presentation.curve_rows}
    assert curve_by_label["2"] == 2
    assert curve_by_label["5"] == 1
    group_counts = {group["label"]: group["count"] for group in presentation.card_groups}
    assert group_counts["Artifacts"] == 1
    assert group_counts["Creatures"] == 1

