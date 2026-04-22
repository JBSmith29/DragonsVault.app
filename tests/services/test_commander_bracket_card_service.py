from core.domains.decks.services import commander_bracket_card_service as cards


def test_normalize_face_names_splits_double_faced_names():
    names = cards._normalize_face_names("Boom // Bust")

    assert "Boom" in names
    assert "Bust" in names


def test_nonland_tutor_excludes_land_only_searches():
    card = cards.BracketCard(
        name="Rampant Growth",
        type_line="Sorcery",
        oracle_text="Search your library for a basic land card, put that card onto the battlefield tapped, then shuffle.",
        mana_value=2,
    )

    assert cards._is_nonland_tutor(card) is False
    assert cards._is_land_tutor(card) is True
