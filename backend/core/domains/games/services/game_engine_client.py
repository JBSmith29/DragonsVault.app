"""Client helpers for the Game Engine microservice."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Dict, Optional

import requests

__all__ = [
    "GameEngineError",
    "engine_service_enabled",
    "ping",
    "create_game",
    "join_game",
    "get_game",
    "submit_action",
    "list_events",
    "sync_deck_from_folder",
]


class GameEngineError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class EngineConfig:
    base_urls: list[str]
    shared_secret: str
    timeout: float


def _parse_base_urls(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [part.strip().rstrip("/") for part in raw.split(",") if part.strip()]
    return [part for part in parts if part]


def _shared_secret() -> str:
    for key in ("GAME_ENGINE_SHARED_SECRET", "ENGINE_SHARED_SECRET", "GAME_ENGINE_SECRET"):
        value = os.getenv(key)
        if value:
            return value.strip()
    return ""


def _engine_config() -> EngineConfig:
    raw_urls = (os.getenv("GAME_ENGINE_URL") or "").strip()
    base_urls = _parse_base_urls(raw_urls)
    shared_secret = _shared_secret()
    raw_timeout = os.getenv("GAME_ENGINE_HTTP_TIMEOUT", "6")
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = 6.0
    return EngineConfig(base_urls=base_urls, shared_secret=shared_secret, timeout=timeout)


def engine_service_enabled() -> bool:
    config = _engine_config()
    return bool(config.base_urls and config.shared_secret)


def _headers(user_id: int) -> Dict[str, str]:
    config = _engine_config()
    if not config.base_urls:
        raise GameEngineError("Game engine service URL is not configured.")
    if not config.shared_secret:
        raise GameEngineError("Game engine shared secret is not configured.")
    return {
        "X-Engine-Secret": config.shared_secret,
        "X-User-Id": str(user_id),
    }


def _request(
    method: str,
    path: str,
    *,
    user_id: int,
    params: Optional[dict] = None,
    json_payload: Optional[dict] = None,
) -> Dict[str, Any]:
    config = _engine_config()
    headers = _headers(user_id)
    errors: list[str] = []
    for base_url in config.base_urls:
        url = f"{base_url}{path}"
        try:
            if method.upper() == "POST":
                response = requests.post(
                    url,
                    json=json_payload or {},
                    params=params or {},
                    headers=headers,
                    timeout=config.timeout,
                )
            else:
                response = requests.get(
                    url,
                    params=params or {},
                    headers=headers,
                    timeout=config.timeout,
                )
        except requests.RequestException as exc:
            errors.append(f"{base_url} -> {exc}")
            continue

        try:
            payload = response.json()
        except ValueError as exc:
            raise GameEngineError(
                f"Game engine returned invalid JSON ({response.status_code}) from {base_url}."
            ) from exc

        if response.status_code >= 400:
            message = payload.get("error") or payload.get("status") or f"HTTP {response.status_code}"
            raise GameEngineError(message, status_code=response.status_code)

        return payload

    if errors:
        raise GameEngineError("Game engine unavailable: " + "; ".join(errors))
    raise GameEngineError("Game engine service URL is not configured.")


def create_game(user_id: int, *, format_name: str = "commander", players: Optional[list[int]] = None) -> Dict[str, Any]:
    payload = {"format": format_name}
    if players:
        payload["players"] = players
    return _request("POST", "/v1/games", user_id=user_id, json_payload=payload)


def ping(user_id: int) -> Dict[str, Any]:
    return _request("GET", "/v1/ping", user_id=user_id)


def join_game(user_id: int, game_id: str) -> Dict[str, Any]:
    return _request("POST", f"/v1/games/{game_id}/join", user_id=user_id, json_payload={})


def get_game(user_id: int, game_id: str) -> Dict[str, Any]:
    return _request("GET", f"/v1/games/{game_id}", user_id=user_id)


def submit_action(user_id: int, game_id: str, *, action_type: str, payload: Optional[dict] = None) -> Dict[str, Any]:
    body = {"player_id": user_id, "action_type": action_type, "payload": payload or {}}
    return _request("POST", f"/v1/games/{game_id}/actions", user_id=user_id, json_payload=body)


def list_events(user_id: int, game_id: str, *, since: Optional[int] = None) -> Dict[str, Any]:
    params = {}
    if since is not None:
        params["since"] = since
    return _request("GET", f"/v1/games/{game_id}/events", user_id=user_id, params=params)


def sync_deck_from_folder(user_id: int, folder_id: int) -> Dict[str, Any]:
    body = {"folder_id": folder_id}
    return _request("POST", "/v1/decks/from-folder", user_id=user_id, json_payload=body)
