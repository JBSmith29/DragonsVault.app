from models import Card, Folder, FolderRole, db
from models.role import OracleCoreRoleTag, OracleEvergreenTag


def test_build_collection_card_list_items_formats_roles_pricing_and_owner(app, create_user, monkeypatch):
    from core.domains.cards.services import collection_card_list_view_service

    user, _password = create_user(
        email="collection-list-view@example.com",
        username="collection-list-view",
    )

    with app.app_context():
        folder = Folder(
            name="Main Binder",
            category=Folder.CATEGORY_COLLECTION,
            owner_user_id=user.id,
            owner="Owner Label",
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_COLLECTION))
        card = Card(
            name="Arcane Signet",
            set_code="TST",
            collector_number="20",
            folder_id=folder.id,
            quantity=2,
            oracle_id="oid-arcane-signet",
            type_line="Artifact",
            rarity="rare",
            lang="en",
            is_foil=True,
        )
        db.session.add(card)
        db.session.add(OracleCoreRoleTag(oracle_id="oid-arcane-signet", role="mana_ramp"))
        db.session.add(OracleEvergreenTag(oracle_id="oid-arcane-signet", keyword="flash"))
        db.session.commit()
        card_id = card.id

    monkeypatch.setattr(collection_card_list_view_service.sc, "cache_ready", lambda: True)
    monkeypatch.setattr(
        collection_card_list_view_service.sc,
        "image_for_print",
        lambda payload: {"small": "small.png", "large": "large.png"},
    )
    monkeypatch.setattr(
        collection_card_list_view_service,
        "_bulk_print_lookup",
        lambda cards: {
            card.id: {
                "oracle_text": "Add {U}.",
                "color_identity": ["U"],
                "rarity": "rare",
            }
        },
    )
    monkeypatch.setattr(
        collection_card_list_view_service,
        "_prices_for_print_exact",
        lambda payload: {"usd_foil": "2.50"},
    )

    with app.app_context():
        card = db.session.get(Card, card_id)
        items = collection_card_list_view_service.build_collection_card_list_items(
            [card],
            base_types=["Artifact", "Creature"],
            current_user_id=user.id,
        )

    assert len(items) == 1
    item = items[0]
    assert item.image_small == "small.png"
    assert item.image_large == "large.png"
    assert item.type_badges == ["Artifact"]
    assert item.type_tokens == ["artifact"]
    assert item.core_roles_display == ["Mana Ramp"]
    assert item.evergreen_display == ["Flash"]
    assert item.color_letters == ["U"]
    assert item.rarity_label == "Rare"
    assert item.rarity_badge_class == "warning"
    assert item.price_text == "$2.50"
    assert item.owner_label == "You"
