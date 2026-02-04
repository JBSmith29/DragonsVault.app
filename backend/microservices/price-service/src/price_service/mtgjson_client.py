from __future__ import annotations

import json
from typing import Any

import requests

from .config import ServiceConfig


class MtgJsonError(RuntimeError):
    pass


class MtgJsonClient:
    def __init__(self, config: ServiceConfig) -> None:
        self._url = config.mtgjson_graphql_url
        self._token = config.mtgjson_api_token
        self._timeout = config.request_timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        if self._token:
            self._session.headers.update(
                {
                    "Authorization": f"Bearer {self._token}",
                    "X-Auth-Token": self._token,
                }
            )

    def has_token(self) -> bool:
        return bool(self._token)

    def _execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = {"query": query, "variables": variables}
        try:
            response = self._session.post(
                self._url,
                data=json.dumps(payload),
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise MtgJsonError(f"network_error: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise MtgJsonError(f"invalid_json: {response.status_code}") from exc

        if response.status_code != 200:
            raise MtgJsonError(f"http_{response.status_code}")

        if data.get("errors"):
            message = data["errors"][0].get("message") or "graphql_error"
            raise MtgJsonError(message)

        return data.get("data") or {}

    def fetch_card_by_scryfall_id(self, scryfall_id: str) -> dict[str, Any] | None:
        query = """
        query($filter: CardEntityFilterInput!, $order: ListOrderInput!, $page: PaginationInput!) {
          cards(order: $order, page: $page, filter: $filter) {
            uuid
            name
            setCode
            number
            identifiers { scryfallId }
          }
        }
        """
        variables = {
            "filter": {"identifiers": {"scryfallId_eq": scryfall_id}},
            "order": {"order": "ASC"},
            "page": {"take": 1, "skip": 0},
        }
        data = self._execute(query, variables)
        cards = data.get("cards") or []
        return cards[0] if cards else None

    def fetch_prices_for_uuid(self, uuid: str) -> list[dict[str, Any]]:
        query = """
        query($input: PriceGetInput!, $order: ListOrderInput!, $page: PaginationInput!) {
          prices(input: $input, order: $order, page: $page) {
            uuid
            date
            provider
            listType
            cardType
            currency
            price
          }
        }
        """
        variables = {
            "input": {"uuid": uuid},
            "order": {"order": "DESC"},
            "page": {"take": 200, "skip": 0},
        }
        data = self._execute(query, variables)
        return data.get("prices") or []
