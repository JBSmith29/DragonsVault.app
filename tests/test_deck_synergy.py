from services.deck_synergy import calculate_tag_synergy, classify_roles, detect_themes_for_text


def test_classify_roles_detects_ramp_and_draw():
    text = (
        "Search your library for up to two basic land cards, reveal those cards, "
        "put one onto the battlefield tapped and the other into your hand. Then shuffle."
        "Draw a card."
    )
    roles = classify_roles(text, "Sorcery")
    assert "ramp" in roles
    assert "card_draw" in roles


def test_classify_roles_identifies_mass_bounce_board_wipe():
    text = "Return each nonland permanent you don't control to its owner's hand."
    roles = classify_roles(text, "Instant")
    assert roles == {"board_wipe"}


def test_classify_roles_does_not_treat_basic_land_as_ramp():
    text = "({T}: Add {G}.)"
    roles = classify_roles(text, "Land")
    assert roles == set()


def test_classify_roles_detects_nonland_tutor():
    text = "Search your library for an artifact card, reveal it, put it into your hand, then shuffle."
    roles = classify_roles(text, "Sorcery")
    assert "tutor" in roles
    assert "ramp" not in roles


def test_classify_roles_detects_recursion_and_protection():
    text = (
        "Return target creature card from your graveyard to your hand. "
        "Until end of turn, permanents you control gain hexproof and indestructible."
    )
    roles = classify_roles(text, "Instant")
    assert "recursion" in roles
    assert "protection" in roles


def test_classify_roles_flags_finishers():
    text = "Each opponent loses 2 life. If this spell was cast from your hand, take an extra turn after this one."
    roles = classify_roles(text, "Sorcery")
    assert "finisher" in roles


def test_detect_themes_finds_tokens_and_sacrifice():
    text = (
        "Whenever another creature you control dies, create a Treasure token. "
        "Sacrifice another creature: Target opponent loses 1 life and you gain 1 life."
    )
    themes = detect_themes_for_text(text, "Creature")
    assert "tokens" in themes
    assert "sacrifice" in themes


def test_detect_themes_finds_spellslinger():
    text = "Whenever you cast an instant or sorcery spell, draw a card."
    themes = detect_themes_for_text(text, "Enchantment")
    assert "spellslinger" in themes


def test_detect_themes_finds_treasure_and_lands_matter():
    text = (
        "Create two Treasure tokens. Whenever a land enters the battlefield under your control, investigate. "
        "You may play an additional land this turn."
    )
    themes = detect_themes_for_text(text, "Enchantment")
    assert "treasure" in themes
    assert "lands_matter" in themes


def test_calculate_tag_synergy_tokens_scoring():
    present = {"skullclamp", "anointed procession"}
    synergy = calculate_tag_synergy(
        "Tokens",
        {"W", "G"},
        present,
        {"tokens": 4},
        core_limit=10,
        support_limit=10,
    )
    assert synergy is not None
    assert synergy["score"] > 0
    present_names = {card["name"] for card in synergy["core"]["present"]}
    missing_names = {card["name"] for card in synergy["core"]["missing"]}
    assert "Skullclamp" in present_names
    assert "Parallel Lives" in missing_names
    assert synergy["core"]["eligible_count"] >= len(present_names | missing_names)


def test_calculate_tag_synergy_returns_placeholder_for_unknown_tag():
    result = calculate_tag_synergy("Blink Stuff", {"W"}, set(), {})
    assert result is not None
    assert result["grade"] == "N/A"
    assert result["core"]["present"] == []
