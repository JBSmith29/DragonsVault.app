from models.card import Card

def test_card_model_repr():
    card = Card(name="Sol Ring", set_code="CMM", quantity=2)
    assert "Sol Ring" in repr(card)
