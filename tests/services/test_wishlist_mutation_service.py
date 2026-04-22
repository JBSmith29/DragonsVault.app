import json

from models import WishlistItem, db
from core.domains.decks.services import wishlist_mutation_service


def test_normalize_wishlist_rows_handles_strings_and_mixed_lists(app):
    with app.app_context():
        rows = wishlist_mutation_service.normalize_wishlist_rows(["Sol Ring", {"name": "Arcane Signet"}, ""])

    assert rows == [{"name": "Sol Ring"}, {"name": "Arcane Signet"}]


def test_serialize_source_folders_normalizes_names_and_qty():
    payload = wishlist_mutation_service.serialize_source_folders(
        [{"name": "Trade Binder", "quantity": "2"}, "Sideboard", {"name": " ", "qty": 5}]
    )

    assert json.loads(payload) == [
        {"name": "Trade Binder", "qty": 2},
        {"name": "Sideboard", "qty": None},
    ]


def test_wishlist_upsert_rows_updates_existing_item_requested_qty(app, db_session):
    with app.app_context():
        existing = WishlistItem(name="Cyclonic Rift", requested_qty=1, missing_qty=1, status="open")
        db.session.add(existing)
        db.session.commit()

        created, updated, skipped = wishlist_mutation_service.wishlist_upsert_rows(
            [
                {
                    "name": "Cyclonic Rift",
                    "requested_qty": 3,
                    "status": "ordered",
                    "order_ref": "A-123",
                    "source_folders": [{"name": "Blue Binder", "qty": 1}],
                }
            ]
        )

        refreshed = db.session.get(WishlistItem, existing.id)

    assert (created, updated, skipped) == (0, 1, 0)
    assert refreshed.requested_qty == 3
    assert refreshed.missing_qty == 3
    assert refreshed.status == "ordered"
    assert refreshed.order_ref == "A-123"
    assert json.loads(refreshed.source_folders) == [{"name": "Blue Binder", "qty": 1}]
