from types import SimpleNamespace

from core.domains.cards.services import list_checker_result_builder_service as builder_service
from core.domains.cards.services import list_checker_result_service


def test_build_results_uses_meta_fallbacks_for_missing_rep_card_details():
    formatter = list_checker_result_service.ListCheckerBreakdownFormatter(
        current_user_id=None,
        friend_ids=set(),
        collection_id_set={10},
        folder_meta={10: {"name": "Collection", "owner_user_id": None, "owner": ""}},
        owner_label_map={},
    )

    results, summary = builder_service.build_results(
        want={"arcane signet": {"display": "Arcane Signet", "qty": 1}},
        basic_land_slugs=set(),
        per_folder_counts={"arcane signet": {10: 1}},
        collection_counts={"arcane signet": {10: 1}},
        deck_counts={"arcane signet": {}},
        available_per_folder_counts={"arcane signet": {10: 1}},
        available_count={"arcane signet": 1},
        rep_card_map={
            "arcane signet": SimpleNamespace(
                id=9,
                oracle_id="oid-arcane-signet",
                rarity="",
                type_line="",
                color_identity=None,
            )
        },
        name_to_sid={"arcane signet": ("scryfall-1", None, "oid-arcane-signet")},
        face_to_sid={},
        name_to_meta={"arcane signet": {"rarity": "uncommon", "type": "Artifact", "color_identity": "WU"}},
        face_to_meta={},
        formatter=formatter,
    )

    assert results == [
        {
            "name": "Arcane Signet",
            "requested": 1,
            "available_in_collection": 1,
            "missing_qty": 0,
            "status": "have_all",
            "folders": [("Collection", 1)],
            "collection_folders": [("Collection", 1)],
            "deck_folders": [],
            "available_folders": [("Collection", 1)],
            "available_folders_detail": [
                {
                    "folder_id": 10,
                    "label": "Collection",
                    "qty": 1,
                    "owner_user_id": None,
                    "owner_label": "",
                    "owner_rank": 2,
                }
            ],
            "available_user_folders": [("Collection", 1)],
            "available_friend_folders": [],
            "available_user": 1,
            "available_friends": 0,
            "friend_targets": [],
            "total_owned": 1,
            "card_id": 9,
            "scry_id": "scryfall-1",
            "oracle_id": "oid-arcane-signet",
            "color_identity": "WU",
            "color_identity_label": "Azorius",
            "rarity": "uncommon",
            "type": "Artifact",
        }
    ]
    assert summary == {"have_all": 1, "friends": 0, "partial": 0, "missing": 0, "total_rows": 1}
