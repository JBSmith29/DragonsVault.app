from services import commander_brackets as cb


def test_bracket_one_allows_two_nonland_tutors(monkeypatch):
    """Two nonland tutors should still qualify for Bracket 1 when other signals are absent."""

    monkeypatch.setattr(cb, "ensure_cache_loaded", lambda: None)

    tutors = [
        cb.BracketCard(
            name="Demonic Tutor",
            type_line="Sorcery",
            oracle_text="Search your library for a card, put that card into your hand, then shuffle.",
            mana_value=2,
            quantity=1,
        ),
        cb.BracketCard(
            name="Vampiric Tutor",
            type_line="Instant",
            oracle_text="Search your library for a card, then shuffle.",
            mana_value=1,
            quantity=1,
        ),
    ]
    fillers = [
        cb.BracketCard(
            name=f"Vanilla {idx}",
            type_line="Creature",
            oracle_text="",
            mana_value=4,
            quantity=1,
        )
        for idx in range(58)
    ]

    result = cb.evaluate_commander_bracket(tutors + fillers)

    assert result["level"] == 1
    assert result["bracket1_eligible"] is True


def test_creature_tutors_excluded_from_nonland_tutor_count(monkeypatch):
    """Creature-based tutors (e.g., Imperial Recruiter) should not inflate the nonland tutor metric."""

    monkeypatch.setattr(cb, "ensure_cache_loaded", lambda: None)

    cards = [
        cb.BracketCard(
            name="Imperial Recruiter",
            type_line="Creature — Human Advisor",
            oracle_text="When Imperial Recruiter enters the battlefield, search your library for a creature card with power 2 or less, reveal it, put it into your hand, then shuffle.",
            mana_value=3,
            quantity=1,
        ),
        cb.BracketCard(
            name="Recruiter of the Guard",
            type_line="Creature — Human Soldier",
            oracle_text="When Recruiter of the Guard enters the battlefield, search your library for a creature card with toughness 2 or less, reveal it, put it into your hand, then shuffle.",
            mana_value=3,
            quantity=1,
        ),
    ]

    # Pad the deck to keep other metrics quiet.
    for idx in range(58):
        cards.append(
            cb.BracketCard(
                name=f"Filler {idx}",
                type_line="Sorcery",
                oracle_text="",
                mana_value=4,
                quantity=1,
            )
        )

    result = cb.evaluate_commander_bracket(cards)

    assert result["metrics"]["nonland_tutors"] == 0
