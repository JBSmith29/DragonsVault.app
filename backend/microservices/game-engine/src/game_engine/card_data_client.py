from __future__ import annotations

import json
from urllib import request, error

from .config import load_config


class CardDataClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._cache: dict[str, dict] = {}

    def fetch_oracle(self, oracle_id: str) -> dict | None:
        if not oracle_id:
            return None
        if oracle_id in self._cache:
            return self._cache[oracle_id]
        url = f"{self.base_url}/oracles/{oracle_id}"
        try:
            with request.urlopen(url, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (error.URLError, json.JSONDecodeError):
            return None
        if not payload or payload.get("status") != "ok":
            return None
        oracle = payload.get("oracle") or {}
        self._cache[oracle_id] = oracle
        return oracle


def get_card_data_client() -> CardDataClient:
    config = load_config()
    return CardDataClient(config.card_data_base_url)
