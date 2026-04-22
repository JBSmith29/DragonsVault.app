from core.domains.cards.services import list_checker_export_service


def test_build_list_checker_export_response_uses_owned_sources_then_available_fallback():
    response = list_checker_export_service.build_list_checker_export_response(
        [
            {
                "name": "Owned Card",
                "type": "Artifact",
                "color_identity_label": "Colorless",
                "rarity": "Common",
                "requested": 2,
                "available_in_collection": 2,
                "missing_qty": 0,
                "status": "have_all",
                "total_owned": 2,
                "available_user_folders": [("Main Binder", 2)],
                "available_folders": [("Ignored Fallback", 5)],
            },
            {
                "name": "Fallback Card",
                "type": "Instant",
                "color_identity_label": "Blue",
                "rarity": "Uncommon",
                "requested": 1,
                "available_in_collection": 1,
                "missing_qty": 0,
                "status": "partial",
                "total_owned": 1,
                "available_user_folders": [],
                "available_folders": [("Friend Binder", 1), ("Trade Box", 1)],
            },
        ]
    )

    assert response.headers["Content-Disposition"] == "attachment; filename=list_checker_results.csv"
    body = response.get_data(as_text=True).splitlines()
    assert body[0] == "\ufeffCard,Type,Color Identity,Rarity,Requested,Available,Missing,Status,Total Owned,Collection 1,Collection 2"
    assert "Main Binder ×2" in body[1]
    assert "Ignored Fallback" not in body[1]
    assert "Friend Binder ×1" in body[2]
    assert "Trade Box ×1" in body[2]
