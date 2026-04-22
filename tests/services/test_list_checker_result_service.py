from types import SimpleNamespace

from core.domains.cards.services import list_checker_result_service


def test_build_results_marks_basic_lands_owned_and_friend_rows_available():
    formatter = list_checker_result_service.ListCheckerBreakdownFormatter(
        current_user_id=1,
        friend_ids={2},
        collection_id_set={10, 20},
        folder_meta={
            20: {"name": "Friend Binder", "owner_user_id": 2, "owner": ""},
        },
        owner_label_map={2: "Friend Collector"},
    )

    results, summary = list_checker_result_service.build_results(
        want={
            "arcane signet": {"display": "Arcane Signet", "qty": 1},
            "island": {"display": "Island", "qty": 2},
        },
        basic_land_slugs={"island"},
        per_folder_counts={
            "arcane signet": {20: 1},
            "island": {},
        },
        collection_counts={
            "arcane signet": {20: 1},
            "island": {},
        },
        deck_counts={
            "arcane signet": {},
            "island": {},
        },
        available_per_folder_counts={
            "arcane signet": {20: 1},
            "island": {},
        },
        available_count={
            "arcane signet": 1,
            "island": 0,
        },
        rep_card_map={
            "arcane signet": SimpleNamespace(
                id=9,
                oracle_id="oid-arcane-signet",
                rarity="uncommon",
                type_line="Artifact",
                color_identity="",
            ),
        },
        name_to_sid={},
        face_to_sid={},
        name_to_meta={},
        face_to_meta={},
        formatter=formatter,
    )

    assert [row["name"] for row in results] == ["Arcane Signet", "Island"]
    assert results[0]["status"] == "friends"
    assert results[0]["friend_targets"] == [
        {
            "user_id": 2,
            "label": "Friend Collector",
            "qty": 1,
            "folders": [{"name": "Friend Collector: Friend Binder", "qty": 1}],
        }
    ]
    assert results[1]["status"] == "have_all"
    assert results[1]["available_in_collection"] == 2
    assert results[1]["missing_qty"] == 0
    assert summary == {"have_all": 1, "friends": 1, "partial": 0, "missing": 0, "total_rows": 2}
