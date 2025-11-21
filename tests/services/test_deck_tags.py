from services.deck_tags import resolve_deck_tag_from_slug


def test_resolve_deck_tag_direct_match():
    assert resolve_deck_tag_from_slug("dinosaurs") == "Dinosaurs"


def test_resolve_deck_tag_with_population_suffix():
    assert resolve_deck_tag_from_slug("lifegain2-6k") == "Lifegain"


def test_resolve_deck_tag_unknown_slug():
    assert resolve_deck_tag_from_slug("custom-archetype") is None
