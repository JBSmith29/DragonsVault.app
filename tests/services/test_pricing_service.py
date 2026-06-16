import requests

from core.domains.cards.services import pricing


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_prices_for_print_prefers_embedded_without_calling_service(monkeypatch):
    """Embedded catalog prices win and the price-service is never contacted.

    Avoids a per-card HTTP round-trip (an N+1 on large deck/collection pages).
    """
    monkeypatch.setenv("PRICE_SERVICE_URL", "http://price-service")
    monkeypatch.setenv("PRICE_SERVICE_CACHE_TTL", "0")
    monkeypatch.setenv("PRICE_SERVICE_HTTP_TIMEOUT", "1")
    pricing._PRICE_SERVICE_CACHE.clear()

    calls = {"n": 0}

    def fake_get(url, timeout):
        calls["n"] += 1
        return _FakeResponse(200, {"status": "ok", "prices": {"usd": "1.23"}})

    monkeypatch.setattr(pricing.requests, "get", fake_get)

    pr = {"id": "abc", "prices": {"usd": "9.99"}}
    out = pricing.prices_for_print(pr)
    assert out["usd"] == "9.99"
    assert calls["n"] == 0, "price-service should not be called when embedded prices exist"


def test_prices_for_print_uses_service_when_no_embedded(monkeypatch):
    """When the print carries no usable embedded price, fall back to the service."""
    monkeypatch.setenv("PRICE_SERVICE_URL", "http://price-service")
    monkeypatch.setenv("PRICE_SERVICE_CACHE_TTL", "0")
    monkeypatch.setenv("PRICE_SERVICE_HTTP_TIMEOUT", "1")
    pricing._PRICE_SERVICE_CACHE.clear()

    def fake_get(url, timeout):
        return _FakeResponse(200, {"status": "ok", "prices": {"usd": "1.23"}})

    monkeypatch.setattr(pricing.requests, "get", fake_get)

    pr = {"id": "abc", "prices": {}}
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
