from core.domains.decks.services import wishlist_display_service


def test_format_color_identity_normalizes_sequences_and_colorless():
    assert wishlist_display_service.format_color_identity(["g", "u", "x"]) == "GU"
    assert wishlist_display_service.format_color_identity(["c"]) == "C"


def test_split_folder_label_preserves_owner_hint():
    assert wishlist_display_service.split_folder_label("Alice: Trade Binder") == ("Alice", "Trade Binder")
    assert wishlist_display_service.split_folder_label("Trade Binder") == (None, "Trade Binder")
