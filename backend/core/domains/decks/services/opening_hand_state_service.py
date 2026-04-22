"""State serialization and deck expansion helpers for opening-hand flows."""

from __future__ import annotations

from typing import Optional

from itsdangerous import BadSignature, URLSafeTimedSerializer

OPENING_HAND_STATE_SALT = "opening-hand-state-v1"
OPENING_HAND_STATE_MAX_AGE_SECONDS = 6 * 60 * 60


def opening_hand_state_serializer(
    secret_key: str | None,
    *,
    salt: str = OPENING_HAND_STATE_SALT,
) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key or "dev", salt=salt)


def encode_state(
    payload: dict,
    *,
    secret_key: str | None,
    salt: str = OPENING_HAND_STATE_SALT,
) -> str:
    return opening_hand_state_serializer(secret_key, salt=salt).dumps(payload)


def normalize_opening_hand_state(
    payload: object,
    *,
    current_user_id: int | None,
) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    deck = payload.get("deck")
    if not isinstance(deck, list) or any(not isinstance(entry, dict) for entry in deck):
        return None
    try:
        index = int(payload.get("index") or 0)
    except Exception:
        return None
    if index < 0 or index > len(deck):
        return None
    deck_name = payload.get("deck_name") or "Deck"
    if not isinstance(deck_name, str):
        deck_name = str(deck_name)
    user_id = payload.get("user_id")
    try:
        user_id = int(user_id) if user_id is not None else None
    except Exception:
        return None
    if current_user_id is None or user_id != current_user_id:
        return None
    return {
        "deck": deck,
        "index": index,
        "deck_name": deck_name,
        "user_id": user_id,
    }


def decode_state(
    token: str,
    *,
    secret_key: str | None,
    current_user_id: int | None,
    max_age: int = OPENING_HAND_STATE_MAX_AGE_SECONDS,
    salt: str = OPENING_HAND_STATE_SALT,
) -> Optional[dict]:
    if not token:
        return None
    try:
        payload = opening_hand_state_serializer(secret_key, salt=salt).loads(token, max_age=max_age)
    except BadSignature:
        return None
    return normalize_opening_hand_state(payload, current_user_id=current_user_id)


def expanded_deck_entries(entries: list[dict]) -> list[dict]:
    expanded: list[dict] = []
    counter = 0
    for entry in entries:
        qty = int(entry.get("qty") or 0) or 1
        base = entry.copy()
        base.pop("qty", None)
        for _ in range(qty):
            clone = base.copy()
            uid_seed = (base.get("card_id") or "") or (base.get("oracle_id") or "") or (base.get("name") or "")
            clone["uid"] = f"{uid_seed}-{counter}"
            expanded.append(clone)
            counter += 1
    return expanded


__all__ = [
    "OPENING_HAND_STATE_MAX_AGE_SECONDS",
    "OPENING_HAND_STATE_SALT",
    "decode_state",
    "encode_state",
    "expanded_deck_entries",
    "normalize_opening_hand_state",
    "opening_hand_state_serializer",
]
