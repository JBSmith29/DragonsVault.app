def test_analyze_oracle_prints_builds_typal_and_role_data():
    from shared.jobs.background import oracle_profile_service

    prints = [
        {
            "name": "Atraxa, Praetors' Voice",
            "lang": "en",
            "set_type": "expansion",
            "games": ["paper"],
            "digital": False,
            "type_line": "Legendary Creature — Angel Horror",
            "oracle_text": "Flying, vigilance, deathtouch, lifelink",
            "keywords": ["Flying", "Vigilance"],
            "color_identity": ["W", "U", "B", "G"],
        }
    ]

    analysis = oracle_profile_service.analyze_oracle_prints(
        prints,
        get_land_tags_for_card_fn=lambda _mock: set(),
        derive_evergreen_keywords_fn=lambda **kwargs: {"flying", "lifelink"},
        derive_core_roles_fn=lambda **kwargs: ["value_engine"],
        core_role_label_fn=lambda role: "Value Engine" if role == "value_engine" else None,
        get_roles_for_card_fn=lambda _mock: ["card_draw"],
        get_subroles_for_card_fn=lambda _mock: ["card_draw:repeatable"],
        get_primary_role_fn=lambda roles: roles[0] if roles else None,
        derive_deck_tags_fn=lambda **kwargs: {"Midrange"},
        ensure_fallback_tag_fn=lambda tags, _evergreen: tags,
    )

    assert analysis is not None
    assert analysis["mock"]["name"] == "Atraxa, Praetors' Voice"
    assert analysis["keywords"] == {"flying", "vigilance"}
    assert analysis["typals"] == {"angel", "horror"}
    assert analysis["core_role_tags"] == {"Value Engine"}
    assert analysis["roles"] == ["card_draw"]
    assert analysis["subroles"] == ["card_draw:repeatable"]
    assert analysis["primary_role"] == "card_draw"
    assert analysis["deck_tags"] == {"Midrange"}


def test_select_best_print_prefers_paper_english_non_digital():
    from shared.jobs.background import oracle_profile_service

    prints = [
        {"name": "Card", "lang": "jp", "set_type": "expansion", "games": ["paper"], "digital": False},
        {"name": "Card", "lang": "en", "set_type": "token", "games": ["paper"], "digital": False},
        {"name": "Card", "lang": "en", "set_type": "expansion", "games": ["paper"], "digital": False},
    ]

    best = oracle_profile_service.select_best_print(prints)

    assert best == prints[2]
