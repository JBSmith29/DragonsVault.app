import requests

from core.domains.cards.services import pricing


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_prices_for_print_prefers_price_service(monkeypatch):
    monkeypatch.setenv("PRICE_SERVICE_URL", "http://price-service")
    monkeypatch.setenv("PRICE_SERVICE_CACHE_TTL", "0")
    monkeypatch.setenv("PRICE_SERVICE_HTTP_TIMEOUT", "1")
    pricing._PRICE_SERVICE_CACHE.clear()

    def fake_get(url, timeout):
        return _FakeResponse(200, {"status": "ok", "prices": {"usd": "1.23"}})

    monkeypatch.setattr(pricing.requests, "get", fake_get)

    pr = {"id": "abc", "prices": {"usd": "9.99"}}
    out = pricing.prices_for_print(pr)
    assert out["usd"] == "1.23"


def test_prices_for_print_falls_back_on_error(monkeypatch):
    monkeypatch.setenv("PRICE_SERVICE_URL", "http://price-service")
    monkeypatch.setenv("PRICE_SERVICE_CACHE_TTL", "0")
    pricing._PRICE_SERVICE_CACHE.clear()

    def fake_get(url, timeout):
        raise requests.RequestException("boom")

    monkeypatch.setattr(pricing.requests, "get", fake_get)

    pr = {"id": "abc", "prices": {"usd": "9.99"}}
    out = pricing.prices_for_print(pr)
    assert out["usd"] == "9.99"
