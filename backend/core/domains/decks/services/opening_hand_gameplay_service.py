"""Pure deck and hand state transitions for opening-hand gameplay."""

from __future__ import annotations

import random

HAND_SIZE = 7
_BASIC_LAND_NAMES = {
    "plains",
    "island",
    "swamp",
    "mountain",
    "forest",
    "wastes",
    "snow-covered plains",
    "snow-covered island",
    "snow-covered swamp",
    "snow-covered mountain",
    "snow-covered forest",
    "snow-covered wastes",
}


def shuffle_deck(
    entries: list[dict],
    *,
    hand_size: int = HAND_SIZE,
    expand_entries_fn,
) -> dict:
    deck_pool = expand_entries_fn(entries)
    if len(deck_pool) < hand_size:
        raise ValueError(f"Deck needs at least {hand_size} drawable cards.")
    random.shuffle(deck_pool)
    hand_cards = deck_pool[:hand_size]
    return {
        "deck": deck_pool,
        "index": hand_size,
        "hand_cards": hand_cards,
        "remaining": len(deck_pool) - hand_size,
        "deck_size": len(deck_pool),
    }


def mulligan_state(
    state: dict,
    *,
    raw_bottom_uids,
    count,
    hand_size: int = HAND_SIZE,
) -> dict:
    deck = state["deck"]
    index = int(state["index"])
    if index != hand_size:
        raise ValueError("Mulligan is only available after shuffling a fresh opening hand.")

    try:
        count = int(count) if count is not None else len(raw_bottom_uids or [])
    except (TypeError, ValueError):
        raise ValueError("Invalid mulligan count.") from None
    if count < 0 or count > hand_size:
        raise ValueError("Invalid mulligan count.")

    hand_cards = deck[:index]
    if count > len(hand_cards):
        raise ValueError("Not enough cards in hand for this mulligan.")

    bottom_uids = [str(item or "").strip() for item in (raw_bottom_uids or []) if str(item or "").strip()]
    if len(bottom_uids) != count:
        raise ValueError("Select exactly the number of cards required for mulligan.")
    if len(set(bottom_uids)) != len(bottom_uids):
        raise ValueError("Duplicate mulligan selections are not allowed.")

    by_uid = {str(entry.get("uid") or "").strip(): entry for entry in hand_cards if str(entry.get("uid") or "").strip()}
    if any(uid not in by_uid for uid in bottom_uids):
        raise ValueError("One or more selected cards are not in the opening hand.")

    bottom_uid_set = set(bottom_uids)
    bottom_cards = [by_uid[uid] for uid in bottom_uids]
    keep_cards = [entry for entry in hand_cards if str(entry.get("uid") or "").strip() not in bottom_uid_set]
    new_deck = keep_cards + deck[index:] + bottom_cards
    return {
        "state": {
            **state,
            "deck": new_deck,
            "index": len(keep_cards),
        },
        "hand_cards": keep_cards,
        "remaining": len(new_deck) - len(keep_cards),
        "bottomed": len(bottom_cards),
        "hand_size": len(keep_cards),
    }


def draw_state(state: dict) -> dict:
    deck = state["deck"]
    index = int(state["index"])
    if index >= len(deck):
        raise ValueError("No more cards to draw.")
    next_state = {**state, "index": index + 1}
    return {
        "state": next_state,
        "card": deck[index],
        "remaining": len(deck) - next_state["index"],
    }


def search_state(
    state: dict,
    *,
    action: str,
    criteria: dict,
    pick_uid: str | None = None,
    pick_name: str | None = None,
) -> dict:
    kind = (criteria.get("kind") or "").lower()
    names_lower = {str(name).strip().lower() for name in (criteria.get("names") or []) if name}
    if kind not in {"basic_land", "land"}:
        raise ValueError("Unsupported search request.")

    deck = state.get("deck") or []
    index = int(state.get("index") or 0)
    remaining = deck[index:]

    def _matches(entry: dict) -> bool:
        name = (entry.get("name") or "").strip().lower()
        type_line = (entry.get("type_line") or "").lower()
        if names_lower:
            return name in names_lower
        if kind == "basic_land":
            return "basic land" in type_line or name in _BASIC_LAND_NAMES
        return "land" in type_line

    if action == "list":
        grouped: dict[str, dict] = {}
        for entry in remaining:
            if not _matches(entry):
                continue
            name = entry.get("name") or "Card"
            bucket = grouped.setdefault(name, {"count": 0, "entry": entry})
            bucket["count"] += 1
        results = [
            {
                "name": name,
                "count": data["count"],
                "card": data["entry"],
            }
            for name, data in grouped.items()
        ]
        results.sort(key=lambda item: (item.get("name") or "").lower())
        return {"matches": results, "remaining": len(deck) - index}

    if action != "take":
        raise ValueError("Unsupported search request.")

    selected_index = None
    pick_uid = (pick_uid or "").strip() or None
    pick_name = (pick_name or "").strip() or None
    for offset, entry in enumerate(remaining):
        if not _matches(entry):
            continue
        if pick_uid and entry.get("uid") == pick_uid:
            selected_index = index + offset
            break
        if pick_name and (entry.get("name") or "").strip().lower() == pick_name.lower():
            selected_index = index + offset
            break
    if selected_index is None:
        raise LookupError("No matching card found.")

    deck = list(deck)
    selected = deck.pop(selected_index)
    if index < len(deck):
        random.shuffle(deck[index:])
    return {
        "state": {
            **state,
            "deck": deck,
            "index": index,
        },
        "card": selected,
        "remaining": len(deck) - index,
    }


def peek_state(state: dict, *, count) -> dict:
    try:
        count = int(count or 1)
    except (TypeError, ValueError):
        count = 1
    deck = state["deck"]
    index = int(state["index"])
    remaining = len(deck) - index
    if remaining <= 0:
        raise ValueError("No cards left in the deck.")
    count = min(max(1, count), 10, remaining)
    return {
        "cards": deck[index:index + count],
        "count": count,
        "remaining": remaining,
    }


def hideaway_state(
    state: dict,
    *,
    count,
    pick_uid: str | None = None,
    pick_index=None,
) -> dict:
    try:
        count = int(count or 4)
    except (TypeError, ValueError):
        count = 4
    count = max(1, min(count, 20))

    deck = list(state["deck"])
    index = int(state["index"])
    remaining = len(deck) - index
    if remaining <= 0:
        raise ValueError("No cards left in the deck.")

    top_cards = deck[index:index + min(count, remaining)]
    selected_idx = None
    pick_uid = (pick_uid or "").strip() or None
    if pick_uid:
        for idx, entry in enumerate(top_cards):
            if entry.get("uid") == pick_uid:
                selected_idx = idx
                break
    if selected_idx is None and pick_index is not None:
        try:
            pick_index = int(pick_index)
        except (TypeError, ValueError):
            pick_index = None
        if pick_index is not None and 0 <= pick_index < len(top_cards):
            selected_idx = pick_index
    if selected_idx is None:
        raise ValueError("Choose a card to hide away.")

    chosen = top_cards[selected_idx]
    remainder = [entry for idx, entry in enumerate(top_cards) if idx != selected_idx]
    random.shuffle(remainder)
    new_deck = deck[:index] + deck[index + len(top_cards):] + remainder
    return {
        "state": {
            **state,
            "deck": new_deck,
            "index": index,
        },
        "hidden": chosen,
        "remaining": len(new_deck) - index,
        "bottom": len(remainder),
    }


def reorder_state(
    state: dict,
    *,
    action: str,
    count,
    keep_order,
    bottom_order,
    graveyard_order,
    choices,
) -> dict:
    try:
        count = int(count or 1)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(count, 10))

    deck = list(state["deck"])
    index = int(state["index"])
    remaining = len(deck) - index
    if remaining <= 0:
        raise ValueError("No cards left in the deck.")

    top_cards = deck[index:index + min(count, remaining)]
    count = len(top_cards)

    def _normalize_indices(raw_list) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for item in raw_list or []:
            try:
                idx = int(item)
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= count or idx in seen:
                continue
            seen.add(idx)
            out.append(idx)
        return out

    keep_order = _normalize_indices(keep_order)
    bottom_order = _normalize_indices(bottom_order)
    graveyard_order = _normalize_indices(graveyard_order)

    choice_map: dict[int, str] = {}
    for choice in choices or []:
        try:
            idx = int(choice.get("index"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= count:
            continue
        action_value = (choice.get("action") or "").strip().lower()
        if action_value in {"top", "bottom", "graveyard"}:
            choice_map[idx] = action_value

    if not keep_order:
        keep_order = [idx for idx in range(count) if choice_map.get(idx) not in {"bottom", "graveyard"}]
    keep_set = set(keep_order)

    if action == "scry":
        if not bottom_order:
            bottom_order = [idx for idx in range(count) if idx not in keep_set]
        else:
            seen_bottom = set(bottom_order)
            bottom_order = [idx for idx in bottom_order if idx not in keep_set]
            bottom_order.extend(idx for idx in range(count) if idx not in keep_set and idx not in seen_bottom)
        graveyard_order = []
    else:
        if not graveyard_order:
            graveyard_order = [idx for idx in range(count) if idx not in keep_set]
        else:
            seen_graveyard = set(graveyard_order)
            graveyard_order = [idx for idx in graveyard_order if idx not in keep_set]
            graveyard_order.extend(idx for idx in range(count) if idx not in keep_set and idx not in seen_graveyard)
        bottom_order = []

    keep_cards = [top_cards[idx] for idx in keep_order if idx < len(top_cards)]
    bottom_cards = [top_cards[idx] for idx in bottom_order if idx < len(top_cards)]
    graveyard_cards = [top_cards[idx] for idx in graveyard_order if idx < len(top_cards)]
    new_deck = deck[:index] + keep_cards + deck[index + count:]
    if action == "scry":
        new_deck.extend(bottom_cards)

    return {
        "state": {
            **state,
            "deck": new_deck,
            "index": index,
        },
        "remaining": len(new_deck) - index,
        "keep_cards": keep_cards,
        "bottom_cards": bottom_cards,
        "graveyard_cards": graveyard_cards,
    }


__all__ = [
    "HAND_SIZE",
    "draw_state",
    "hideaway_state",
    "mulligan_state",
    "peek_state",
    "reorder_state",
    "search_state",
    "shuffle_deck",
]
