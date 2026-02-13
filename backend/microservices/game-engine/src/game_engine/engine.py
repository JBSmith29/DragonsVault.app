from __future__ import annotations

import random
import re
import uuid
from datetime import datetime
from typing import Any


TURN_SEQUENCE = [
    ("beginning", "untap"),
    ("beginning", "upkeep"),
    ("beginning", "draw"),
    ("main", "precombat"),
    ("combat", "begin_combat"),
    ("combat", "declare_attackers"),
    ("combat", "declare_blockers"),
    ("combat", "damage"),
    ("combat", "end_combat"),
    ("main", "postcombat"),
    ("ending", "end_step"),
    ("ending", "cleanup"),
]


def new_game_state(*, format_name: str, player_ids: list[int]) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "format": format_name,
        "status": "waiting",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "players": [
            {
                "user_id": player_id,
                "life": 40 if format_name.lower() == "commander" else 20,
                "commander_damage": {},
                "zones": {
                    "library": [],
                    "hand": [],
                    "battlefield": [],
                    "graveyard": [],
                    "exile": [],
                    "command": [],
                },
            }
            for player_id in player_ids
        ],
        "stack": [],
        "turn": {
            "number": 1,
            "active_player": player_ids[0] if player_ids else None,
            "phase": "beginning",
            "step": "untap",
            "priority_player": player_ids[0] if player_ids else None,
            "passed": [],
        },
        "choices": [],
        "log": [],
        "meta": {
            "lands_played": {str(pid): 0 for pid in player_ids},
        },
        "rules_version": "v1",
    }


def _log(state: dict[str, Any], message: str) -> None:
    state.setdefault("log", []).append(
        {"ts": datetime.utcnow().isoformat() + "Z", "message": message}
    )


def _find_player(state: dict[str, Any], player_id: int) -> dict[str, Any] | None:
    for player in state.get("players", []):
        if player.get("user_id") == player_id:
            return player
    return None


def _make_card_instance(card: dict[str, Any], owner_id: int) -> dict[str, Any]:
    instance = dict(card or {})
    instance.setdefault("name", "Card")
    instance.setdefault("type_line", "")
    instance.setdefault("oracle_text", "")
    instance["instance_id"] = instance.get("instance_id") or uuid.uuid4().hex
    instance["owner_id"] = owner_id
    instance["controller_id"] = owner_id
    instance.setdefault("tapped", False)
    instance.setdefault("damage", 0)
    instance.setdefault("power", None)
    instance.setdefault("toughness", None)
    instance.setdefault("loyalty", None)
    instance.setdefault("defense", None)
    instance.setdefault("counters", {})
    instance.setdefault("is_commander", bool(instance.get("is_commander")))
    if instance.get("is_commander"):
        instance.setdefault("commander_owner_id", instance.get("commander_owner_id") or owner_id)
    type_line = (instance.get("type_line") or "").lower()
    instance.setdefault("summoning_sick", "creature" in type_line)
    return instance


def _find_card_in_zone(player: dict[str, Any], zone: str, instance_id: str) -> tuple[int, dict[str, Any]] | None:
    cards = player.get("zones", {}).get(zone, [])
    for idx, card in enumerate(cards):
        if card.get("instance_id") == instance_id:
            return idx, card
    return None


def _locate_card(state: dict[str, Any], instance_id: str) -> tuple[dict[str, Any], str, dict[str, Any]] | None:
    for player in state.get("players", []):
        for zone_name, cards in player.get("zones", {}).items():
            for card in cards:
                if card.get("instance_id") == instance_id:
                    return player, zone_name, card
    return None


def _move_card(player: dict[str, Any], from_zone: str, to_zone: str, instance_id: str) -> dict[str, Any] | None:
    found = _find_card_in_zone(player, from_zone, instance_id)
    if not found:
        return None
    idx, card = found
    player["zones"][from_zone].pop(idx)
    player["zones"][to_zone].append(card)
    return card


def _move_card_with_triggers(
    state: dict[str, Any],
    player: dict[str, Any],
    from_zone: str,
    to_zone: str,
    instance_id: str,
) -> dict[str, Any] | None:
    card = _move_card(player, from_zone, to_zone, instance_id)
    if card and from_zone == "battlefield" and to_zone == "graveyard":
        controller_id = card.get("controller_id") or player.get("user_id")
        _enqueue_triggers(state, "dies", event_player_id=controller_id, event_card=card)
    return card


def _move_card_between_players(
    state: dict[str, Any],
    from_player: dict[str, Any],
    from_zone: str,
    to_player: dict[str, Any],
    to_zone: str,
    instance_id: str,
) -> dict[str, Any] | None:
    if not from_player or not to_player:
        return None
    found = _find_card_in_zone(from_player, from_zone, instance_id)
    if not found:
        return None
    idx, card = found
    from_player["zones"][from_zone].pop(idx)
    to_player["zones"][to_zone].append(card)
    return card


def _apply_damage_to_card(
    state: dict[str, Any],
    owner: dict[str, Any],
    zone_name: str,
    card_obj: dict[str, Any],
    amount: int,
    *,
    deathtouch: bool = False,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    type_line = (card_obj.get("type_line") or "").lower()
    amount = int(amount or 0)
    if "planeswalker" in type_line:
        loyalty = card_obj.get("loyalty")
        try:
            loyalty_value = int(loyalty)
        except (TypeError, ValueError):
            loyalty_value = None
        if loyalty_value is not None:
            loyalty_value -= amount
            card_obj["loyalty"] = str(loyalty_value)
            if loyalty_value <= 0:
                _move_card(owner, zone_name, "graveyard", card_obj.get("instance_id"))
                events.append({"type": "destroyed", "card": card_obj.get("name", "Card")})
            else:
                events.append({"type": "damage", "card": card_obj.get("name", "Card"), "amount": amount})
        else:
            events.append({"type": "damage", "card": card_obj.get("name", "Card"), "amount": amount})
        return events

    card_obj["damage"] = int(card_obj.get("damage") or 0) + amount
    if deathtouch and amount > 0 and not _is_indestructible(card_obj):
        _move_card_with_triggers(state, owner, zone_name, "graveyard", card_obj.get("instance_id"))
        events.append({"type": "destroyed", "card": card_obj.get("name", "Card")})
        return events
    toughness_value = _toughness_value(card_obj)
    if toughness_value > 0 and card_obj["damage"] >= toughness_value and not _is_indestructible(card_obj):
        _move_card_with_triggers(state, owner, zone_name, "graveyard", card_obj.get("instance_id"))
        events.append({"type": "destroyed", "card": card_obj.get("name", "Card")})
    else:
        events.append({"type": "damage", "card": card_obj.get("name", "Card"), "amount": amount})
    return events


def _is_instant_or_sorcery(card: dict[str, Any]) -> bool:
    type_line = (card.get("type_line") or "").lower()
    return "instant" in type_line or "sorcery" in type_line


def _has_keyword(card: dict[str, Any], keyword: str) -> bool:
    text = (card.get("oracle_text") or "").lower()
    keyword_lower = keyword.lower()
    if keyword_lower in {k.lower() for k in (card.get("temp_keywords") or [])}:
        return True
    if not text:
        return False
    return re.search(rf"\\b{re.escape(keyword_lower)}\\b", text) is not None


def _is_indestructible(card: dict[str, Any]) -> bool:
    return _has_keyword(card, "indestructible")


def _card_colors(card: dict[str, Any]) -> set[str]:
    colors = card.get("colors") or card.get("color_identity")
    if isinstance(colors, list):
        return {str(color) for color in colors if color}
    if isinstance(colors, str):
        return {colors}
    return set()


def _source_colors_for_protection(card: dict[str, Any]) -> set[str]:
    colors = _card_colors(card)
    if colors:
        return colors
    return {"C"}


def _protection_colors(card: dict[str, Any]) -> set[str]:
    text = (card.get("oracle_text") or "").lower()
    temp = " ".join(card.get("temp_keywords") or []).lower()
    if temp:
        text = f"{text} {temp}"
    if "protection from everything" in text:
        return {"W", "U", "B", "R", "G", "C"}
    colors: set[str] = set()
    for match in re.finditer(r"protection from ([^\\n\\.]*)", text):
        segment = match.group(1)
        if "everything" in segment or "all colors" in segment:
            return {"W", "U", "B", "R", "G", "C"}
        if "white" in segment:
            colors.add("W")
        if "blue" in segment:
            colors.add("U")
        if "black" in segment:
            colors.add("B")
        if "red" in segment:
            colors.add("R")
        if "green" in segment:
            colors.add("G")
        if "colorless" in segment:
            colors.add("C")
    return colors


def _has_cant_be_blocked(card: dict[str, Any]) -> bool:
    text = (card.get("oracle_text") or "").lower()
    return "can't be blocked" in text


def _damage_prevented_by_protection(target_card: dict[str, Any], source_colors: set[str]) -> bool:
    protection = _protection_colors(target_card)
    if not protection:
        return False
    if protection.intersection(source_colors):
        return True
    return False


def _counter_adjustment(card: dict[str, Any]) -> int:
    counters = card.get("counters") or {}
    try:
        plus = int(counters.get("+1/+1") or 0)
    except (TypeError, ValueError):
        plus = 0
    try:
        minus = int(counters.get("-1/-1") or 0)
    except (TypeError, ValueError):
        minus = 0
    return plus - minus


def _power_value(card: dict[str, Any]) -> int:
    try:
        base = int(card.get("power"))
    except (TypeError, ValueError):
        base = 0
    temp_power = int(card.get("temp_power") or 0)
    return base + _counter_adjustment(card) + temp_power


def _toughness_value(card: dict[str, Any]) -> int:
    try:
        base = int(card.get("toughness"))
    except (TypeError, ValueError):
        base = 0
    temp_toughness = int(card.get("temp_toughness") or 0)
    return base + _counter_adjustment(card) + temp_toughness


def _creature_can_strike(card: dict[str, Any], step: str) -> bool:
    has_double = _has_keyword(card, "double strike")
    has_first = _has_keyword(card, "first strike") or has_double
    if step == "first":
        return has_first
    return has_double or not has_first


def _parse_defender(defender: Any) -> dict[str, Any] | None:
    if defender is None:
        return None
    if isinstance(defender, str):
        if defender.startswith("player:"):
            try:
                return {"type": "player", "id": int(defender.split(":", 1)[1])}
            except ValueError:
                return None
        if defender.startswith("card:"):
            return {"type": "card", "id": defender.split(":", 1)[1]}
    if isinstance(defender, int):
        return {"type": "player", "id": defender}
    if isinstance(defender, dict):
        if "player_id" in defender:
            try:
                return {"type": "player", "id": int(defender.get("player_id"))}
            except (TypeError, ValueError):
                return None
        if "card_id" in defender:
            return {"type": "card", "id": defender.get("card_id")}
    return None


def _defender_controller_id(state: dict[str, Any], defender: dict[str, Any]) -> int | None:
    if defender.get("type") == "player":
        pid = int(defender.get("id"))
        if _find_player(state, pid):
            return pid
        return None
    if defender.get("type") == "card":
        located = _locate_card(state, defender.get("id"))
        if not located:
            return None
        owner, _, card_obj = located
        if "planeswalker" not in (card_obj.get("type_line") or "").lower():
            return None
        return int(card_obj.get("controller_id") or owner.get("user_id"))
    return None


def _parse_count_token(value: str | None) -> int | None:
    if not value:
        return None
    token = str(value).strip().lower()
    if token in {"a", "an", "one"}:
        return 1
    if token == "two":
        return 2
    if token == "three":
        return 3
    if token == "four":
        return 4
    if token == "five":
        return 5
    if token.isdigit():
        return int(token)
    return None


MAX_X_CHOICE = 100


def _parse_effects_text(text: str) -> list[dict[str, Any]]:
    text = str(text or "")
    lowered = text.lower()
    effects: list[dict[str, Any]] = []
    if not text:
        return effects

    count_token = r"(a|an|one|two|three|four|five|\d+|x)"

    draw_re = re.compile(rf"draw\s+(?P<upto>up to\s+)?(?P<count>{count_token})\s+card", re.I)
    discard_re = re.compile(rf"discard\s+(?P<upto>up to\s+)?(?P<count>{count_token})\s+card", re.I)
    for match in draw_re.finditer(text):
        count = _parse_count_token(match.group("count"))
        up_to = bool(match.group("upto"))
        effects.append({"kind": "draw", "count": count, "up_to": up_to, "index": match.start()})
    for match in discard_re.finditer(text):
        count = _parse_count_token(match.group("count"))
        up_to = bool(match.group("upto"))
        effects.append({"kind": "discard", "count": count, "up_to": up_to, "index": match.start()})

    target_draw_re = re.compile(rf"target player draws\s+(?P<upto>up to\s+)?(?P<count>{count_token})\s+card", re.I)
    target_discard_re = re.compile(rf"target player discards\s+(?P<upto>up to\s+)?(?P<count>{count_token})\s+card", re.I)
    for match in target_draw_re.finditer(text):
        count = _parse_count_token(match.group("count"))
        up_to = bool(match.group("upto"))
        effects.append(
            {
                "kind": "target_draw",
                "count": count,
                "up_to": up_to,
                "target": {"type": "player", "scope": "any"},
                "index": match.start(),
            }
        )
    for match in target_discard_re.finditer(text):
        count = _parse_count_token(match.group("count"))
        up_to = bool(match.group("upto"))
        effects.append(
            {
                "kind": "target_discard",
                "count": count,
                "up_to": up_to,
                "target": {"type": "player", "scope": "any"},
                "index": match.start(),
            }
        )

    each_opponent_draw_re = re.compile(rf"each opponent draws\s+(?P<count>{count_token})\s+card", re.I)
    each_opponent_discard_re = re.compile(rf"each opponent discards\s+(?P<count>{count_token})\s+card", re.I)
    for match in each_opponent_draw_re.finditer(text):
        count = _parse_count_token(match.group("count"))
        effects.append({"kind": "draw_each_opponent", "count": count, "index": match.start()})
    for match in each_opponent_discard_re.finditer(text):
        count = _parse_count_token(match.group("count"))
        effects.append({"kind": "discard_each_opponent", "count": count, "index": match.start()})

    each_player_draw_re = re.compile(rf"each player draws\s+(?P<count>{count_token})\s+card", re.I)
    each_player_discard_re = re.compile(rf"each player discards\s+(?P<count>{count_token})\s+card", re.I)
    for match in each_player_draw_re.finditer(text):
        count = _parse_count_token(match.group("count"))
        effects.append({"kind": "draw_each_player", "count": count, "index": match.start()})
    for match in each_player_discard_re.finditer(text):
        count = _parse_count_token(match.group("count"))
        effects.append({"kind": "discard_each_player", "count": count, "index": match.start()})

    life_gain_re = re.compile(rf"gain\s+{count_token}\s+life", re.I)
    life_loss_re = re.compile(rf"lose\s+{count_token}\s+life", re.I)
    for match in life_gain_re.finditer(text):
        count = _parse_count_token(match.group(1))
        effects.append({"kind": "gain_life", "count": count, "index": match.start()})
    for match in life_loss_re.finditer(text):
        count = _parse_count_token(match.group(1))
        effects.append({"kind": "lose_life", "count": count, "index": match.start()})

    each_opponent_lose_re = re.compile(rf"each opponent loses\s+{count_token}\s+life", re.I)
    each_player_lose_re = re.compile(rf"each player loses\s+{count_token}\s+life", re.I)
    for match in each_opponent_lose_re.finditer(text):
        count = _parse_count_token(match.group(1))
        effects.append({"kind": "lose_life_each_opponent", "count": count, "index": match.start()})
    for match in each_player_lose_re.finditer(text):
        count = _parse_count_token(match.group(1))
        effects.append({"kind": "lose_life_each_player", "count": count, "index": match.start()})

    each_opponent_gain_re = re.compile(rf"each opponent gains\s+{count_token}\s+life", re.I)
    each_player_gain_re = re.compile(rf"each player gains\s+{count_token}\s+life", re.I)
    for match in each_opponent_gain_re.finditer(text):
        count = _parse_count_token(match.group(1))
        effects.append({"kind": "gain_life_each_opponent", "count": count, "index": match.start()})
    for match in each_player_gain_re.finditer(text):
        count = _parse_count_token(match.group(1))
        effects.append({"kind": "gain_life_each_player", "count": count, "index": match.start()})

    scry_re = re.compile(rf"scry\s+{count_token}", re.I)
    for match in scry_re.finditer(text):
        count = _parse_count_token(match.group(1))
        effects.append({"kind": "scry", "count": count, "index": match.start()})

    mill_re = re.compile(rf"mill\s+{count_token}", re.I)
    for match in mill_re.finditer(text):
        count = _parse_count_token(match.group(1))
        effects.append({"kind": "mill", "count": count, "target": {"type": "player", "scope": "you"}, "index": match.start()})
    target_mill_re = re.compile(rf"target player mills\s+{count_token}", re.I)
    for match in target_mill_re.finditer(text):
        count = _parse_count_token(match.group(1))
        effects.append(
            {
                "kind": "mill",
                "count": count,
                "target": {"type": "player", "scope": "any"},
                "index": match.start(),
            }
        )
    each_player_mill_re = re.compile(rf"each player mills\s+{count_token}", re.I)
    for match in each_player_mill_re.finditer(text):
        count = _parse_count_token(match.group(1))
        effects.append({"kind": "mill_each_player", "count": count, "index": match.start()})

    if "create" in lowered and "token" in lowered:
        token_re = re.compile(rf"create\s+{count_token}\s+([^\\n\\.]+?)\s+token", re.I)
        match = token_re.search(text)
        if match:
            count = _parse_count_token(match.group(1))
            token_name = match.group(2).strip()
            effects.append(
                {
                    "kind": "create_tokens",
                    "count": count,
                    "token_name": token_name,
                    "index": match.start(),
                }
            )

    # Targeted removal/bounce/tap
    target_specs = [
        (r"destroy target creature or planeswalker", "destroy", {"type": "creature_or_planeswalker", "scope": "any"}),
        (r"destroy target artifact or enchantment", "destroy", {"type": "artifact_or_enchantment", "scope": "any"}),
        (r"destroy target nonland permanent", "destroy", {"type": "nonland_permanent", "scope": "any"}),
        (r"destroy target planeswalker", "destroy", {"type": "planeswalker", "scope": "any"}),
        (r"destroy target creature", "destroy", {"type": "creature", "scope": "any"}),
        (r"destroy target artifact", "destroy", {"type": "artifact", "scope": "any"}),
        (r"destroy target enchantment", "destroy", {"type": "enchantment", "scope": "any"}),
        (r"destroy target permanent", "destroy", {"type": "permanent", "scope": "any"}),
        (r"exile target creature or planeswalker", "exile", {"type": "creature_or_planeswalker", "scope": "any"}),
        (r"exile target artifact or enchantment", "exile", {"type": "artifact_or_enchantment", "scope": "any"}),
        (r"exile target nonland permanent", "exile", {"type": "nonland_permanent", "scope": "any"}),
        (r"exile target planeswalker", "exile", {"type": "planeswalker", "scope": "any"}),
        (r"exile target creature", "exile", {"type": "creature", "scope": "any"}),
        (r"exile target artifact", "exile", {"type": "artifact", "scope": "any"}),
        (r"exile target enchantment", "exile", {"type": "enchantment", "scope": "any"}),
        (r"exile target permanent", "exile", {"type": "permanent", "scope": "any"}),
        (r"return target creature to its owner's hand", "bounce", {"type": "creature", "scope": "any"}),
        (r"return target nonland permanent to its owner's hand", "bounce", {"type": "nonland_permanent", "scope": "any"}),
        (r"return target permanent to its owner's hand", "bounce", {"type": "permanent", "scope": "any"}),
        (r"tap target creature or artifact", "tap", {"type": "creature_or_artifact", "scope": "any"}),
        (r"tap target creature", "tap", {"type": "creature", "scope": "any"}),
        (r"tap target artifact", "tap", {"type": "artifact", "scope": "any"}),
        (r"untap target permanent", "untap", {"type": "permanent", "scope": "any"}),
        (r"untap target creature", "untap", {"type": "creature", "scope": "any"}),
    ]
    for pattern, kind, spec in target_specs:
        match = re.search(pattern, lowered)
        if match:
            effects.append({"kind": kind, "target": spec, "index": match.start()})

    # Damage to target creature/player/any target
    dmg_re = re.compile(
        rf"deal[s]?\s+{count_token}\s+damage to target (creature|player|planeswalker|creature or player|any target)",
        re.I,
    )
    dmg_match = dmg_re.search(text)
    if dmg_match:
        amount = _parse_count_token(dmg_match.group(1))
        target_phrase = dmg_match.group(2).lower()
        if "any target" in target_phrase:
            target_type = "any_target"
        elif "creature or player" in target_phrase:
            target_type = "creature_or_player"
        else:
            target_type = target_phrase
        effects.append(
            {
                "kind": "deal_damage",
                "amount": amount,
                "target": {"type": target_type, "scope": "any"},
                "index": dmg_match.start(),
            }
        )

    # Counters
    counter_re = re.compile(
        rf"put\s+(?P<count>{count_token})\s+(?P<counter>\\+\\d+/\\+\\d+|\\-\\d+/\\-\\d+|loyalty|charge)\s+counter(?:s)?\s+on\s+(?P<upto>up to one\\s+)?(?P<target>target creature|target planeswalker|target permanent)",
        re.I,
    )
    counter_match = counter_re.search(text)
    if counter_match:
        count = _parse_count_token(counter_match.group("count"))
        counter_type = counter_match.group("counter")
        target_phrase = counter_match.group("target").lower()
        if "planeswalker" in target_phrase:
            target_type = "planeswalker"
        elif "permanent" in target_phrase:
            target_type = "permanent"
        else:
            target_type = "creature"
        effects.append(
            {
                "kind": "add_counters",
                "count": count,
                "counter_type": counter_type,
                "up_to": bool(counter_match.group("upto")),
                "target": {"type": target_type, "scope": "any"},
                "index": counter_match.start(),
            }
        )
    team_counter_re = re.compile(
        rf"put\s+(?P<count>{count_token})\s+(?P<counter>\\+\\d+/\\+\\d+|\\-\\d+/\\-\\d+|loyalty|charge)\s+counter(?:s)?\s+on\s+each\s+creature you control",
        re.I,
    )
    team_counter_match = team_counter_re.search(text)
    if team_counter_match:
        count = _parse_count_token(team_counter_match.group("count"))
        counter_type = team_counter_match.group("counter")
        effects.append(
            {
                "kind": "add_counters_team",
                "count": count,
                "counter_type": counter_type,
                "scope": "you",
                "index": team_counter_match.start(),
            }
        )
    team_counter_any_re = re.compile(
        rf"put\s+(?P<count>{count_token})\s+(?P<counter>\\+\\d+/\\+\\d+|\\-\\d+/\\-\\d+|loyalty|charge)\s+counter(?:s)?\s+on\s+each\s+creature",
        re.I,
    )
    team_counter_any_match = team_counter_any_re.search(text)
    if team_counter_any_match:
        count = _parse_count_token(team_counter_any_match.group("count"))
        counter_type = team_counter_any_match.group("counter")
        effects.append(
            {
                "kind": "add_counters_team",
                "count": count,
                "counter_type": counter_type,
                "scope": "any",
                "index": team_counter_any_match.start(),
            }
        )
    remove_counter_re = re.compile(
        rf"remove\s+(?P<count>{count_token})\s+(?P<counter>\\+\\d+/\\+\\d+|\\-\\d+/\\-\\d+|loyalty|charge)\s+counter(?:s)?\s+from\s+(?P<target>target creature|target planeswalker|target permanent)",
        re.I,
    )
    remove_counter_match = remove_counter_re.search(text)
    if remove_counter_match:
        count = _parse_count_token(remove_counter_match.group("count"))
        counter_type = remove_counter_match.group("counter")
        target_phrase = remove_counter_match.group("target").lower()
        if "planeswalker" in target_phrase:
            target_type = "planeswalker"
        elif "permanent" in target_phrase:
            target_type = "permanent"
        else:
            target_type = "creature"
        effects.append(
            {
                "kind": "remove_counters",
                "count": count,
                "counter_type": counter_type,
                "target": {"type": target_type, "scope": "any"},
                "index": remove_counter_match.start(),
            }
        )

    # Until end of turn power/toughness
    pump_and_gain_re = re.compile(
        r"(?P<upto>up to one\\s+)?target creature gets (?P<power>[+\\-]\\d+)/(?P<toughness>[+\\-]\\d+) and gains (?P<keyword>[^\\n\\.]*) until end of turn",
        re.I,
    )
    pump_and_gain_match = pump_and_gain_re.search(text)
    if pump_and_gain_match:
        try:
            power_delta = int(pump_and_gain_match.group("power"))
            toughness_delta = int(pump_and_gain_match.group("toughness"))
        except ValueError:
            power_delta = None
            toughness_delta = None
        keyword = pump_and_gain_match.group("keyword").strip().lower()
        if power_delta is not None and toughness_delta is not None:
            effects.append(
                {
                    "kind": "pump_until_eot",
                    "power_delta": power_delta,
                    "toughness_delta": toughness_delta,
                    "up_to": bool(pump_and_gain_match.group("upto")),
                    "target": {"type": "creature", "scope": "any"},
                    "index": pump_and_gain_match.start(),
                }
            )
        if keyword:
            effects.append(
                {
                    "kind": "grant_keyword_until_eot",
                    "keyword": keyword,
                    "up_to": bool(pump_and_gain_match.group("upto")),
                    "target": {"type": "creature", "scope": "any"},
                    "index": pump_and_gain_match.start(),
                }
            )
    pump_re = re.compile(
        r"(?P<upto>up to one\\s+)?target creature gets (?P<power>[+\\-]\\d+)/(?P<toughness>[+\\-]\\d+) until end of turn",
        re.I,
    )
    pump_match = pump_re.search(text)
    if pump_match:
        try:
            power_delta = int(pump_match.group("power"))
            toughness_delta = int(pump_match.group("toughness"))
        except ValueError:
            power_delta = None
            toughness_delta = None
        if power_delta is not None and toughness_delta is not None:
            effects.append(
                {
                    "kind": "pump_until_eot",
                    "power_delta": power_delta,
                    "toughness_delta": toughness_delta,
                    "up_to": bool(pump_match.group("upto")),
                    "target": {"type": "creature", "scope": "any"},
                    "index": pump_match.start(),
                }
            )

    team_pump_and_gain_re = re.compile(
        r"creatures you control get (?P<power>[+\\-]\\d+)/(?P<toughness>[+\\-]\\d+) and gain (?P<keyword>[^\\n\\.]*) until end of turn",
        re.I,
    )
    team_pump_and_gain_match = team_pump_and_gain_re.search(text)
    if team_pump_and_gain_match:
        try:
            power_delta = int(team_pump_and_gain_match.group("power"))
            toughness_delta = int(team_pump_and_gain_match.group("toughness"))
        except ValueError:
            power_delta = None
            toughness_delta = None
        keyword = team_pump_and_gain_match.group("keyword").strip().lower()
        if power_delta is not None and toughness_delta is not None:
            effects.append(
                {
                    "kind": "pump_team_until_eot",
                    "power_delta": power_delta,
                    "toughness_delta": toughness_delta,
                    "scope": "you",
                    "index": team_pump_and_gain_match.start(),
                }
            )
        if keyword:
            effects.append(
                {
                    "kind": "grant_keyword_team_until_eot",
                    "keyword": keyword,
                    "scope": "you",
                    "index": team_pump_and_gain_match.start(),
                }
            )
    team_pump_re = re.compile(
        r"creatures you control get (?P<power>[+\\-]\\d+)/(?P<toughness>[+\\-]\\d+) until end of turn",
        re.I,
    )
    team_pump_match = team_pump_re.search(text)
    if team_pump_match:
        try:
            power_delta = int(team_pump_match.group("power"))
            toughness_delta = int(team_pump_match.group("toughness"))
        except ValueError:
            power_delta = None
            toughness_delta = None
        if power_delta is not None and toughness_delta is not None:
            effects.append(
                {
                    "kind": "pump_team_until_eot",
                    "power_delta": power_delta,
                    "toughness_delta": toughness_delta,
                    "scope": "you",
                    "index": team_pump_match.start(),
                }
            )

    # Until end of turn keyword grants
    keyword_re = re.compile(
        r"(?P<upto>up to one\\s+)?target creature gains ([^\\n\\.]*) until end of turn",
        re.I,
    )
    keyword_match = keyword_re.search(text)
    if keyword_match:
        keyword = keyword_match.group(2).strip().lower()
        effects.append(
            {
                "kind": "grant_keyword_until_eot",
                "keyword": keyword,
                "up_to": bool(keyword_match.group("upto")),
                "target": {"type": "creature", "scope": "any"},
                "index": keyword_match.start(),
            }
        )

    team_keyword_re = re.compile(
        r"creatures you control gain ([^\\n\\.]*) until end of turn",
        re.I,
    )
    team_keyword_match = team_keyword_re.search(text)
    if team_keyword_match:
        keyword = team_keyword_match.group(1).strip().lower()
        effects.append(
            {
                "kind": "grant_keyword_team_until_eot",
                "keyword": keyword,
                "scope": "you",
                "index": team_keyword_match.start(),
            }
        )

    # Return from graveyard
    reanimate_re = re.compile(
        r"return\s+(?P<upto>up to one\\s+)?target (?P<type>creature|artifact|enchantment|planeswalker|card) card from (?P<scope>your graveyard|a graveyard|any graveyard|an opponent's graveyard|opponent's graveyard) to (?P<dest>the battlefield|your hand|its owner's hand)(?P<tapped> tapped)?(?P<control> under (?:your|its owner's) control)?",
        re.I,
    )
    reanimate_match = reanimate_re.search(text)
    if reanimate_match:
        target_type = reanimate_match.group("type").lower()
        scope_raw = reanimate_match.group("scope").lower()
        if "opponent" in scope_raw:
            scope = "opponent"
        elif "your" in scope_raw:
            scope = "you"
        else:
            scope = "any"
        dest_raw = reanimate_match.group("dest").lower()
        if "owner" in dest_raw:
            destination = "owner_hand"
        elif "hand" in dest_raw:
            destination = "hand"
        else:
            destination = "battlefield"
        control_raw = (reanimate_match.group("control") or "").lower()
        control = "owner" if "its owner's" in control_raw else "you"
        effects.append(
            {
                "kind": "reanimate",
                "target": {"type": target_type, "scope": scope, "zone": "graveyard"},
                "destination": destination,
                "control": control,
                "tapped": bool(reanimate_match.group("tapped")),
                "up_to": bool(reanimate_match.group("upto")),
                "index": reanimate_match.start(),
            }
        )
    exile_graveyard_re = re.compile(
        r"exile\\s+(?P<upto>up to one\\s+)?target (?P<type>creature|artifact|enchantment|planeswalker|card) card from (?P<scope>a graveyard|any graveyard|your graveyard|an opponent's graveyard|opponent's graveyard)",
        re.I,
    )
    exile_graveyard_match = exile_graveyard_re.search(text)
    if exile_graveyard_match:
        target_type = exile_graveyard_match.group("type").lower()
        scope_raw = exile_graveyard_match.group("scope").lower()
        if "opponent" in scope_raw:
            scope = "opponent"
        elif "your" in scope_raw:
            scope = "you"
        else:
            scope = "any"
        effects.append(
            {
                "kind": "exile_from_graveyard",
                "target": {"type": target_type, "scope": scope, "zone": "graveyard"},
                "up_to": bool(exile_graveyard_match.group("upto")),
                "index": exile_graveyard_match.start(),
            }
        )

    # Sacrifice effects
    if "as an additional cost to cast this spell" not in lowered:
        sac_each_opp_re = re.compile(
            rf"each opponent sacrifices\s+{count_token}\s+(creature|artifact|land|permanent)",
            re.I,
        )
        sac_each_opp_match = sac_each_opp_re.search(text)
        if sac_each_opp_match:
            count = _parse_count_token(sac_each_opp_match.group(1))
            target_type = sac_each_opp_match.group(2).lower()
            effects.append(
                {
                    "kind": "sacrifice_each_opponent",
                    "count": count,
                    "target_type": target_type,
                    "index": sac_each_opp_match.start(),
                }
            )
        sac_each_player_re = re.compile(
            rf"each player sacrifices\s+{count_token}\s+(creature|artifact|land|permanent)",
            re.I,
        )
        sac_each_player_match = sac_each_player_re.search(text)
        if sac_each_player_match:
            count = _parse_count_token(sac_each_player_match.group(1))
            target_type = sac_each_player_match.group(2).lower()
            effects.append(
                {
                    "kind": "sacrifice_each_player",
                    "count": count,
                    "target_type": target_type,
                    "index": sac_each_player_match.start(),
                }
            )
        sac_re = re.compile(
            rf"sacrifice\s+{count_token}\s+(creature|artifact|land|permanent)",
            re.I,
        )
        sac_match = sac_re.search(text)
        if sac_match and not sac_each_opp_match and not sac_each_player_match:
            count = _parse_count_token(sac_match.group(1))
            target_type = sac_match.group(2).lower()
            effects.append(
                {
                    "kind": "sacrifice",
                    "count": count,
                    "target_type": target_type,
                    "scope": "you",
                    "index": sac_match.start(),
                }
            )

    if "search your library" in lowered and "basic land" in lowered:
        count_re = re.compile(
            rf"search your library for (?P<upto>up to\s+)?(?P<count>{count_token})\s+basic land(?: card| cards)?",
            re.I,
        )
        match = count_re.search(text)
        raw = match.group("count") if match else None
        up_to = bool(match.group("upto")) if match else False
        count = _parse_count_token(raw) if raw else 1
        split = ("into your hand" in lowered) and ("onto the battlefield" in lowered)
        if split:
            steps = []
            steps.append({"destination": "battlefield", "tapped": "tapped" in lowered, "count": 1})
            steps.append({"destination": "hand", "tapped": False, "count": max(1, (count or 2) - 1)})
            effects.append(
                {
                    "kind": "search_basic_split",
                    "steps": steps,
                    "count": count,
                    "up_to": up_to,
                    "index": lowered.index("search your library"),
                }
            )
        else:
            destination = "battlefield" if "onto the battlefield" in lowered else "hand"
            effects.append(
                {
                    "kind": "search_basic",
                    "count": count,
                    "destination": destination,
                    "tapped": "tapped" in lowered,
                    "up_to": up_to,
                    "index": lowered.index("search your library"),
                }
            )

    if "shuffle your library" in lowered and not any(
        effect.get("kind", "").startswith("search_basic") for effect in effects
    ):
        effects.append({"kind": "shuffle_library", "index": lowered.index("shuffle your library")})

    effects.sort(key=lambda e: e.get("index", 9999))
    return effects


def _parse_effects(card: dict[str, Any]) -> list[dict[str, Any]]:
    return _parse_effects_text(card.get("oracle_text") or "")


def _has_etb_trigger(card: dict[str, Any]) -> bool:
    triggers = _parse_triggers(card)
    return any(trigger.get("event") == "enter_battlefield" for trigger in triggers)


def _parse_costs(text: str) -> list[dict[str, Any]]:
    costs: list[dict[str, Any]] = []
    if not text:
        return costs
    lowered = text.lower()
    add_cost_match = re.search(r"as an additional cost to cast this spell,([^\\.]*)\\.", lowered)
    if not add_cost_match:
        return costs
    clause = add_cost_match.group(1).strip()
    count_token = r"(a|an|one|two|three|four|five|\d+|x)"
    sac_re = re.search(rf"sacrifice\\s+{count_token}\\s+(creature|artifact|land|permanent)", clause, re.I)
    if sac_re:
        count = _parse_count_token(sac_re.group(1))
        target_type = sac_re.group(2).lower()
        costs.append({"kind": "sacrifice", "count": count, "target_type": target_type})
    discard_re = re.search(rf"discard\\s+{count_token}\\s+card", clause, re.I)
    if discard_re:
        count = _parse_count_token(discard_re.group(1))
        costs.append({"kind": "discard", "count": count})
    exile_re = re.search(rf"exile\\s+{count_token}\\s+([^\\.]*)from your graveyard", clause, re.I)
    if exile_re:
        count = _parse_count_token(exile_re.group(1))
        type_phrase = exile_re.group(2).strip().lower()
        target_type = "card"
        for token in ["creature", "artifact", "enchantment", "planeswalker", "land"]:
            if token in type_phrase:
                target_type = token
                break
        costs.append({"kind": "exile_from_graveyard", "count": count, "target_type": target_type})
    return costs


def _collect_cost_options(
    state: dict[str, Any],
    player_id: int,
    cost: dict[str, Any],
) -> list[dict[str, Any]]:
    player = _find_player(state, player_id)
    if not player:
        return []
    options: list[dict[str, Any]] = []
    if cost.get("kind") == "sacrifice":
        target_type = cost.get("target_type") or "permanent"
        for card in player.get("zones", {}).get("battlefield", []):
            type_line = (card.get("type_line") or "").lower()
            if target_type == "permanent":
                pass
            elif target_type not in type_line:
                continue
            options.append({"id": card["instance_id"], "label": card.get("name", "Card")})
    elif cost.get("kind") == "discard":
        for card in player.get("zones", {}).get("hand", []):
            options.append({"id": card["instance_id"], "label": card.get("name", "Card")})
    elif cost.get("kind") == "exile_from_graveyard":
        target_type = cost.get("target_type") or "card"
        for card in player.get("zones", {}).get("graveyard", []):
            type_line = (card.get("type_line") or "").lower()
            if target_type == "card":
                pass
            elif target_type not in type_line:
                continue
            options.append({"id": card["instance_id"], "label": card.get("name", "Card")})
    return options


def _collect_graveyard_targets(
    state: dict[str, Any],
    player_id: int,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    target_type = spec.get("type") or "card"
    scope = spec.get("scope", "you")
    options: list[dict[str, Any]] = []
    for player in state.get("players", []):
        if scope == "you" and player.get("user_id") != player_id:
            continue
        if scope == "opponent" and player.get("user_id") == player_id:
            continue
        for card in player.get("zones", {}).get("graveyard", []):
            type_line = (card.get("type_line") or "").lower()
            if target_type == "card":
                pass
            elif target_type not in type_line:
                continue
            options.append({"id": card["instance_id"], "label": card.get("name", "Card")})
    return options


def _resolve_graveyard_move(
    state: dict[str, Any],
    player_id: int,
    card_id: str,
    destination: str,
    *,
    control: str | None = None,
    tapped: bool = False,
    set_controller: bool = True,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    located = _locate_card(state, card_id)
    if not located:
        return None, events
    owner, zone_name, card_obj = located
    if zone_name != "graveyard":
        return None, events
    owner_id = int(card_obj.get("owner_id") or owner.get("user_id") or 0)
    owner_player = _find_player(state, owner_id) or owner

    destination_zone = destination
    destination_owner = False
    if destination == "owner_hand":
        destination_zone = "hand"
        destination_owner = True
    elif destination == "owner_battlefield":
        destination_zone = "battlefield"
        destination_owner = True
    elif destination == "exile":
        destination_zone = "exile"
        destination_owner = True

    if destination_zone == "battlefield" and control == "owner":
        destination_owner = True
    if destination_owner:
        destination_player = owner_player
    else:
        destination_player = _find_player(state, player_id) or owner_player

    moved = _move_card_between_players(
        state,
        owner_player,
        "graveyard",
        destination_player,
        destination_zone,
        card_id,
    )
    if not moved:
        return None, events

    if destination_zone == "battlefield" and set_controller:
        if control == "owner" or destination_owner:
            card_obj["controller_id"] = owner_player.get("user_id")
        else:
            card_obj["controller_id"] = destination_player.get("user_id")
    if destination_zone == "battlefield" and tapped:
        card_obj["tapped"] = True
    if destination_zone == "battlefield" and "creature" in (card_obj.get("type_line") or "").lower():
        card_obj["summoning_sick"] = True
    if destination_zone == "battlefield":
        controller_id = int(card_obj.get("controller_id") or destination_player.get("user_id") or player_id)
        events.extend(
            _enqueue_triggers(state, "enter_battlefield", event_player_id=controller_id, event_card=card_obj)
        )
    return card_obj, events


def _add_counters(card: dict[str, Any], counter_type: str, count: int) -> None:
    counter_type = str(counter_type or "").lower()
    count = int(count or 0)
    counters = card.setdefault("counters", {})
    if counter_type in {"+1/+1", "-1/-1"}:
        new_value = int(counters.get(counter_type) or 0) + count
        counters[counter_type] = max(0, new_value)
        return
    if counter_type == "loyalty":
        try:
            loyalty = int(card.get("loyalty") or 0)
        except (TypeError, ValueError):
            loyalty = 0
        loyalty += count
        card["loyalty"] = str(loyalty)
        counters[counter_type] = max(0, int(counters.get(counter_type) or 0) + count)
        return
    counters[counter_type] = max(0, int(counters.get(counter_type) or 0) + count)


def _apply_temp_buff(card: dict[str, Any], power_delta: int, toughness_delta: int) -> None:
    card["temp_power"] = int(card.get("temp_power") or 0) + int(power_delta)
    card["temp_toughness"] = int(card.get("temp_toughness") or 0) + int(toughness_delta)


def _apply_temp_keyword(card: dict[str, Any], keyword: str) -> None:
    if not keyword:
        return
    keywords = card.setdefault("temp_keywords", [])
    parts = [part.strip() for part in re.split(r",| and ", keyword) if part.strip()]
    for part in parts:
        if part not in keywords:
            keywords.append(part)


def _event_card_matches_filter(
    event_card: dict[str, Any] | None,
    filter_spec: dict[str, Any] | None,
    source_card: dict[str, Any],
) -> bool:
    if not filter_spec:
        return True
    if filter_spec.get("exclude_self") and event_card and event_card.get("instance_id") == source_card.get("instance_id"):
        return False
    filter_type = filter_spec.get("type")
    if not filter_type:
        return True
    if not event_card:
        return False
    type_line = (event_card.get("type_line") or "").lower()
    if filter_type == "permanent":
        return True
    if filter_type == "nonland_permanent":
        return "land" not in type_line
    if filter_type == "creature":
        return "creature" in type_line
    if filter_type == "artifact":
        return "artifact" in type_line
    if filter_type == "enchantment":
        return "enchantment" in type_line
    if filter_type == "planeswalker":
        return "planeswalker" in type_line
    if filter_type == "land":
        return "land" in type_line
    if filter_type == "instant_or_sorcery":
        return "instant" in type_line or "sorcery" in type_line
    return False


def _parse_triggers(card: dict[str, Any]) -> list[dict[str, Any]]:
    text = (card.get("oracle_text") or "")
    triggers: list[dict[str, Any]] = []
    if not text:
        return triggers
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.search(r"(when|whenever|at)\\s+(.+?),\\s*(.+)", line, re.I)
        if not match:
            continue
        condition = match.group(2).lower()
        effect_text = match.group(3).strip()
        event: str | None = None
        scope = "you"
        filter_spec: dict[str, Any] | None = None
        exclude_self = "another" in condition

        if "enters the battlefield" in condition:
            if "this" in condition or card.get("name", "").lower() in condition:
                event = "enter_battlefield"
                scope = "self"
            else:
                event = "enter_battlefield"
                if "creature" in condition:
                    filter_spec = {"type": "creature"}
                elif "artifact" in condition:
                    filter_spec = {"type": "artifact"}
                elif "enchantment" in condition:
                    filter_spec = {"type": "enchantment"}
                elif "planeswalker" in condition:
                    filter_spec = {"type": "planeswalker"}
                elif "land" in condition:
                    filter_spec = {"type": "land"}
                elif "nonland permanent" in condition:
                    filter_spec = {"type": "nonland_permanent"}
                else:
                    filter_spec = {"type": "permanent"}
                if "under your control" in condition or "you control" in condition:
                    scope = "you"
                elif "opponent" in condition:
                    scope = "opponent"
                else:
                    scope = "any"
        elif "dies" in condition or "is put into a graveyard from the battlefield" in condition:
            if "this" in condition or card.get("name", "").lower() in condition:
                event = "dies"
                scope = "self"
            else:
                event = "dies"
                if "creature" in condition:
                    filter_spec = {"type": "creature"}
                elif "artifact" in condition:
                    filter_spec = {"type": "artifact"}
                elif "enchantment" in condition:
                    filter_spec = {"type": "enchantment"}
                elif "planeswalker" in condition:
                    filter_spec = {"type": "planeswalker"}
                elif "land" in condition:
                    filter_spec = {"type": "land"}
                elif "nonland permanent" in condition:
                    filter_spec = {"type": "nonland_permanent"}
                else:
                    filter_spec = {"type": "permanent"}
                if "you control" in condition:
                    scope = "you"
                elif "opponent" in condition:
                    scope = "opponent"
                else:
                    scope = "any"
        elif "upkeep" in condition:
            event = "upkeep"
            if "each" in condition:
                scope = "each"
            elif "opponent" in condition:
                scope = "opponent"
            else:
                scope = "you"
        elif "end step" in condition:
            event = "end_step"
            if "each" in condition:
                scope = "each"
            elif "opponent" in condition:
                scope = "opponent"
            else:
                scope = "you"
        elif "draw step" in condition:
            event = "draw_step"
            if "each" in condition:
                scope = "each"
            elif "opponent" in condition:
                scope = "opponent"
            else:
                scope = "you"
        elif "beginning of combat" in condition:
            event = "begin_combat"
            if "each" in condition:
                scope = "each"
            elif "opponent" in condition:
                scope = "opponent"
            else:
                scope = "you"
        elif "you draw" in condition:
            event = "draw"
            scope = "you"
        elif "an opponent draws" in condition:
            event = "draw"
            scope = "opponent"
        elif "draws a card" in condition:
            event = "draw"
            if "opponent" in condition:
                scope = "opponent"
            elif "you" in condition:
                scope = "you"
            else:
                scope = "any"
        elif "cast" in condition and "spell" in condition:
            event = "cast_spell"
            if "opponent" in condition:
                scope = "opponent"
            elif "you" in condition:
                scope = "you"
            else:
                scope = "any"
            if "instant or sorcery" in condition:
                filter_spec = {"type": "instant_or_sorcery"}
            elif "creature" in condition:
                filter_spec = {"type": "creature"}
        elif "gain" in condition and "life" in condition:
            event = "life_gain"
            if "opponent" in condition:
                scope = "opponent"
            elif "you" in condition:
                scope = "you"
            else:
                scope = "any"
        elif "lose" in condition and "life" in condition:
            event = "life_loss"
            if "opponent" in condition:
                scope = "opponent"
            elif "you" in condition:
                scope = "you"
            else:
                scope = "any"
        elif "sacrifice" in condition:
            event = "sacrifice"
            if "creature" in condition:
                filter_spec = {"type": "creature"}
            elif "artifact" in condition:
                filter_spec = {"type": "artifact"}
            elif "enchantment" in condition:
                filter_spec = {"type": "enchantment"}
            elif "planeswalker" in condition:
                filter_spec = {"type": "planeswalker"}
            elif "land" in condition:
                filter_spec = {"type": "land"}
            else:
                filter_spec = {"type": "permanent"}
            if "opponent" in condition:
                scope = "opponent"
            elif "you" in condition:
                scope = "you"
            else:
                scope = "any"
        elif "attacks" in condition:
            event = "attacks"
            filter_spec = {"type": "creature"}
            if "opponent" in condition:
                scope = "opponent"
            elif "you" in condition or "creature you control" in condition:
                scope = "you"
            else:
                scope = "any"
        elif "blocks" in condition:
            event = "blocks"
            filter_spec = {"type": "creature"}
            if "opponent" in condition:
                scope = "opponent"
            elif "you" in condition or "creature you control" in condition:
                scope = "you"
            else:
                scope = "any"
        elif "deals combat damage to a player" in condition:
            event = "combat_damage_to_player"
            filter_spec = {"type": "creature"}
            if "opponent" in condition:
                scope = "opponent"
            elif "you" in condition or "creature you control" in condition:
                scope = "you"
            else:
                scope = "any"

        if not event:
            continue
        if filter_spec is None:
            filter_spec = {}
        if exclude_self:
            filter_spec["exclude_self"] = True
        triggers.append(
            {
                "event": event,
                "scope": scope,
                "filter": filter_spec,
                "effects": _parse_effects_text(effect_text),
            }
        )
    return triggers


def _enqueue_triggers(
    state: dict[str, Any],
    event: str,
    *,
    event_player_id: int | None = None,
    event_card: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for player in state.get("players", []):
        for card in player.get("zones", {}).get("battlefield", []):
            triggers = _parse_triggers(card)
            for trigger in triggers:
                if trigger.get("event") != event:
                    continue
                scope = trigger.get("scope")
                controller_id = int(card.get("controller_id") or player.get("user_id"))
                if scope == "self":
                    if not event_card or event_card.get("instance_id") != card.get("instance_id"):
                        continue
                elif scope == "you":
                    if event_player_id is None or controller_id != int(event_player_id):
                        continue
                elif scope == "opponent":
                    if event_player_id is None or controller_id == int(event_player_id):
                        continue
                if not _event_card_matches_filter(event_card, trigger.get("filter"), card):
                    continue
                trigger_item = {
                    "id": uuid.uuid4().hex,
                    "card": card,
                    "controller_id": controller_id,
                    "effects": trigger.get("effects") or [],
                    "pending_effect_index": 0,
                    "is_trigger": True,
                }
                state.setdefault("stack", []).append(trigger_item)
                events.append({"type": "triggered", "card": card.get("name", "Card"), "event": event})
    return events


def _create_choice(
    state: dict[str, Any],
    *,
    player_id: int,
    kind: str,
    prompt: str,
    options: list[dict[str, Any]],
    min_count: int,
    max_count: int | None = None,
    context: dict[str, Any],
) -> dict[str, Any]:
    if max_count is None:
        max_count = min_count
    choice = {
        "id": uuid.uuid4().hex,
        "player_id": player_id,
        "kind": kind,
        "prompt": prompt,
        "options": options,
        "min": int(min_count),
        "max": int(max_count),
        "context": context,
    }
    state.setdefault("choices", []).append(choice)
    return choice


def _collect_targets(state: dict[str, Any], player_id: int, spec: dict[str, Any]) -> list[dict[str, Any]]:
    target_type = spec.get("type")
    scope = spec.get("scope", "any")
    options: list[dict[str, Any]] = []

    if target_type in {"player", "creature_or_player", "any_target"}:
        for player in state.get("players", []):
            options.append(
                {
                    "id": f"player:{player['user_id']}",
                    "label": f"Player {player['user_id']}",
                    "type": "player",
                }
            )
        if target_type == "player":
            return options

    def allow_controller(controller_id: int) -> bool:
        if scope == "any":
            return True
        if scope == "you":
            return controller_id == player_id
        if scope == "opponent":
            return controller_id != player_id
        return True

    def matches_target(card: dict[str, Any]) -> bool:
        type_line = (card.get("type_line") or "").lower()
        is_creature = "creature" in type_line
        is_planeswalker = "planeswalker" in type_line
        is_artifact = "artifact" in type_line
        is_enchantment = "enchantment" in type_line
        is_land = "land" in type_line
        if target_type == "permanent":
            return True
        if target_type == "nonland_permanent":
            return not is_land
        if target_type == "creature":
            return is_creature
        if target_type == "planeswalker":
            return is_planeswalker
        if target_type == "artifact":
            return is_artifact
        if target_type == "enchantment":
            return is_enchantment
        if target_type == "land":
            return is_land
        if target_type == "artifact_or_enchantment":
            return is_artifact or is_enchantment
        if target_type == "creature_or_planeswalker":
            return is_creature or is_planeswalker
        if target_type == "creature_or_artifact":
            return is_creature or is_artifact
        if target_type == "any_target":
            return is_creature or is_planeswalker
        if target_type == "creature_or_player":
            return is_creature
        return False

    for player in state.get("players", []):
        for card in player.get("zones", {}).get("battlefield", []):
            if not allow_controller(card.get("controller_id")):
                continue
            if matches_target(card):
                options.append(
                    {
                        "id": f"card:{card['instance_id']}",
                        "label": card.get("name", "Card"),
                        "type": "card",
                    }
                )
    return options


def _resolve_targeted_effect(state: dict[str, Any], effect: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    kind = effect.get("kind")
    for target in targets:
        if target.get("type") == "player":
            player_id = int(target.get("id"))
            player = _find_player(state, player_id)
            if not player:
                continue
            if kind == "deal_damage":
                amount = effect.get("amount") or 0
                player["life"] -= int(amount)
                events.append({"type": "damage", "target": player_id, "amount": int(amount)})
                events.extend(_enqueue_triggers(state, "life_loss", event_player_id=player_id))
        elif target.get("type") == "card":
            instance_id = target.get("id")
            located = _locate_card(state, instance_id)
            if not located:
                continue
            owner, zone_name, card_obj = located
            if kind == "destroy":
                if _is_indestructible(card_obj):
                    events.append({"type": "indestructible", "card": card_obj.get("name", "Card")})
                    continue
                _move_card_with_triggers(state, owner, zone_name, "graveyard", instance_id)
                events.append({"type": "destroyed", "card": card_obj.get("name", "Card")})
            elif kind == "exile":
                _move_card(owner, zone_name, "exile", instance_id)
                events.append({"type": "exiled", "card": card_obj.get("name", "Card")})
            elif kind == "bounce":
                _move_card(owner, zone_name, "hand", instance_id)
                events.append({"type": "bounced", "card": card_obj.get("name", "Card")})
            elif kind == "tap":
                card_obj["tapped"] = True
                events.append({"type": "tapped", "card": card_obj.get("name", "Card")})
            elif kind == "untap":
                card_obj["tapped"] = False
                events.append({"type": "untapped", "card": card_obj.get("name", "Card")})
            elif kind == "deal_damage":
                amount = effect.get("amount") or 0
                events.extend(_apply_damage_to_card(state, owner, zone_name, card_obj, int(amount)))
    return events


def _resolve_effects(state: dict[str, Any], player_id: int, stack_item: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if stack_item.get("effects"):
        effects = stack_item.get("effects") or []
    else:
        card = stack_item.get("card") or {}
        if stack_item.get("is_trigger") or _is_instant_or_sorcery(card):
            effects = _parse_effects(card)
        else:
            effects = []
    stack_item["effects"] = effects
    start_index = stack_item.get("pending_effect_index", 0)
    player = _find_player(state, player_id)
    if not player:
        return events

    for idx in range(start_index, len(effects)):
        effect = effects[idx]
        kind = effect.get("kind")
        target_spec = effect.get("target")
        if target_spec and kind in {"destroy", "exile", "bounce", "tap", "untap", "deal_damage"}:
            targets = (stack_item.get("targets") or {}).get(idx)
            if targets is None:
                options = _collect_targets(state, player_id, target_spec)
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="target",
                    prompt="Choose target(s).",
                    options=options,
                    min_count=1,
                    context={"stack_id": stack_item["id"], "effect_index": idx, "target_spec": target_spec},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            if kind == "deal_damage" and effect.get("amount") is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="effect_count",
                    prompt="Choose damage amount.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "value_key": "amount",
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            events.extend(_resolve_targeted_effect(state, effect, targets))
            continue
        if kind == "draw":
            count = effect.get("count")
            up_to = bool(effect.get("up_to"))
            if count is None or up_to:
                max_count = int(count) if count is not None else len(player["zones"]["library"])
                max_count = max(0, max_count)
                if max_count <= 0:
                    events.append({"type": "draw", "count": 0})
                    continue
                min_count = 0 if up_to or count is None else 1
                min_count = min(min_count, max_count)
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="draw_count",
                    prompt="Choose how many cards to draw.",
                    options=[],
                    min_count=min_count,
                    max_count=max_count,
                    context={"stack_id": stack_item["id"], "effect_index": idx},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            actual_draws = 0
            for _ in range(max(0, int(count))):
                if not player["zones"]["library"]:
                    break
                player["zones"]["hand"].append(player["zones"]["library"].pop(0))
                actual_draws += 1
            events.append({"type": "draw", "count": int(count)})
            for _ in range(actual_draws):
                events.extend(_enqueue_triggers(state, "draw", event_player_id=player_id))
        elif kind == "discard":
            count = effect.get("count")
            up_to = bool(effect.get("up_to"))
            variable_count = count is None
            if count is None:
                max_count = len(player["zones"]["hand"])
            else:
                max_count = min(int(count), len(player["zones"]["hand"]))
            if max_count <= 0:
                events.append({"type": "discard", "count": 0})
                continue
            min_count = 0 if up_to or variable_count else max_count
            options = [
                {"id": card["instance_id"], "label": card.get("name", "Card")}
                for card in player["zones"]["hand"]
            ]
            prompt_count = f"up to {max_count}" if up_to or variable_count else str(max_count)
            choice = _create_choice(
                state,
                player_id=player_id,
                kind="discard",
                prompt=f"Choose {prompt_count} card(s) to discard.",
                options=options,
                min_count=min_count,
                max_count=max_count,
                context={"stack_id": stack_item["id"], "effect_index": idx},
            )
            events.append({"type": "choice_requested", "choice": choice})
            stack_item["pending_effect_index"] = idx
            return events
        elif kind == "gain_life":
            count = effect.get("count")
            if count is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="effect_count",
                    prompt="Choose life amount.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "value_key": "count",
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            count = int(count)
            player["life"] += int(count)
            events.append({"type": "life_gain", "count": int(count)})
            events.extend(_enqueue_triggers(state, "life_gain", event_player_id=player_id))
        elif kind == "lose_life":
            count = effect.get("count")
            if count is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="effect_count",
                    prompt="Choose life amount.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "value_key": "count",
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            count = int(count)
            player["life"] -= int(count)
            events.append({"type": "life_loss", "count": int(count)})
            events.extend(_enqueue_triggers(state, "life_loss", event_player_id=player_id))
        elif kind in {"gain_life_each_opponent", "gain_life_each_player", "lose_life_each_opponent", "lose_life_each_player"}:
            count = effect.get("count")
            if count is None:
                choice_kind = "life_gain_count" if "gain" in kind else "life_loss_count"
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind=choice_kind,
                    prompt="Choose life amount.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={"stack_id": stack_item["id"], "effect_index": idx, "effect_kind": kind},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            for pl in state.get("players", []):
                if "opponent" in kind and pl.get("user_id") == player_id:
                    continue
                if "gain" in kind:
                    pl["life"] += int(count)
                    events.append({"type": "life_gain", "count": int(count), "player_id": pl.get("user_id")})
                    events.extend(_enqueue_triggers(state, "life_gain", event_player_id=pl.get("user_id")))
                else:
                    pl["life"] -= int(count)
                    events.append({"type": "life_loss", "count": int(count), "player_id": pl.get("user_id")})
                    events.extend(_enqueue_triggers(state, "life_loss", event_player_id=pl.get("user_id")))
        elif kind == "create_tokens":
            count = effect.get("count")
            if count is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="token_count",
                    prompt="Choose how many tokens to create.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={"stack_id": stack_item["id"], "effect_index": idx},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            token_name = effect.get("token_name") or "Token"
            for _ in range(max(0, int(count))):
                token_card = _make_card_instance(
                    {"name": f"{token_name} Token", "type_line": "Token", "oracle_text": ""}, player_id
                )
                player["zones"]["battlefield"].append(token_card)
                events.extend(
                    _enqueue_triggers(
                        state,
                        "enter_battlefield",
                        event_player_id=player_id,
                        event_card=token_card,
                    )
                )
            events.append({"type": "token_created", "count": int(count), "token": token_name})
        elif kind == "search_basic":
            count = effect.get("count")
            up_to = bool(effect.get("up_to"))
            choices = [
                card
                for card in player["zones"]["library"]
                if "basic land" in (card.get("type_line") or "").lower()
            ]
            if count is None:
                max_count = len(choices)
            else:
                max_count = min(int(count), len(choices))
            if max_count <= 0:
                random.shuffle(player["zones"]["library"])
                events.append({"type": "searched", "count": 0})
                continue
            options = [
                {"id": card["instance_id"], "label": card.get("name", "Card")}
                for card in choices
            ]
            variable_count = count is None
            min_count = 0 if up_to or variable_count else max_count
            prompt_count = f"up to {max_count}" if up_to or variable_count else str(max_count)
            choice = _create_choice(
                state,
                player_id=player_id,
                kind="search_basic",
                prompt=f"Choose {prompt_count} basic land card(s).",
                options=options,
                min_count=min_count,
                max_count=max_count,
                context={
                    "stack_id": stack_item["id"],
                    "effect_index": idx,
                    "destination": effect.get("destination", "hand"),
                    "tapped": bool(effect.get("tapped")),
                },
            )
            events.append({"type": "choice_requested", "choice": choice})
            stack_item["pending_effect_index"] = idx
            return events
        elif kind == "search_basic_split":
            steps = effect.get("steps") or []
            if not steps:
                continue
            up_to = bool(effect.get("up_to"))
            available = [
                card
                for card in player["zones"]["library"]
                if "basic land" in (card.get("type_line") or "").lower()
            ]
            total = int(effect.get("count") or sum(step.get("count", 1) for step in steps))
            remaining_total = min(total, len(available))
            if remaining_total <= 0:
                random.shuffle(player["zones"]["library"])
                events.append({"type": "searched", "count": 0})
                continue
            step = steps[0]
            remaining = steps[1:]
            step_max = min(int(step.get("count", 1)), remaining_total)
            min_count = 0 if up_to else step_max
            choice = _create_choice(
                state,
                player_id=player_id,
                kind="search_basic_split",
                prompt="Choose a basic land card.",
                options=[
                    {"id": card["instance_id"], "label": card.get("name", "Card")}
                    for card in available
                ],
                min_count=min_count,
                max_count=step_max,
                context={
                    "stack_id": stack_item["id"],
                    "effect_index": idx,
                    "steps": remaining,
                    "remaining": remaining_total,
                    "up_to": up_to,
                    "destination": step.get("destination", "hand"),
                    "tapped": bool(step.get("tapped")),
                },
            )
            events.append({"type": "choice_requested", "choice": choice})
            stack_item["pending_effect_index"] = idx
            return events
        elif kind == "shuffle_library":
            random.shuffle(player["zones"]["library"])
            events.append({"type": "shuffled", "player_id": player_id})
        elif kind == "scry":
            count = effect.get("count")
            if count is None:
                max_count = len(player["zones"]["library"])
                if max_count <= 0:
                    events.append({"type": "scry", "count": 0})
                    continue
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="scry_count",
                    prompt="Choose how many cards to scry.",
                    options=[],
                    min_count=0,
                    max_count=max_count if max_count > 0 else 1,
                    context={"stack_id": stack_item["id"], "effect_index": idx},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            top_cards = player["zones"]["library"][: int(count)]
            options = [
                {"id": card["instance_id"], "label": card.get("name", "Card")}
                for card in top_cards
            ]
            if not options:
                events.append({"type": "scry", "count": 0})
                continue
            choice = _create_choice(
                state,
                player_id=player_id,
                kind="scry_select",
                prompt="Choose any number of cards to put on the bottom.",
                options=options,
                min_count=0,
                max_count=len(options),
                context={
                    "stack_id": stack_item["id"],
                    "effect_index": idx,
                    "top_ids": [card["instance_id"] for card in top_cards],
                },
            )
            events.append({"type": "choice_requested", "choice": choice})
            stack_item["pending_effect_index"] = idx
            return events
        elif kind == "mill":
            count = effect.get("count")
            target_spec = effect.get("target") or {"type": "player", "scope": "you"}
            targets = (stack_item.get("targets") or {}).get(idx)
            if targets is None and target_spec.get("scope") != "you":
                options = _collect_targets(state, player_id, target_spec)
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="target",
                    prompt="Choose target(s).",
                    options=options,
                    min_count=1,
                    context={"stack_id": stack_item["id"], "effect_index": idx, "target_spec": target_spec},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            if target_spec.get("scope") == "you":
                targets = [{"type": "player", "id": player_id}]
            for target in targets or []:
                if target.get("type") != "player":
                    continue
                target_player = _find_player(state, int(target.get("id")))
                if not target_player:
                    continue
                if count is None:
                    max_count = len(target_player["zones"]["library"])
                    if max_count <= 0:
                        events.append({"type": "mill", "count": 0, "player_id": target_player.get("user_id")})
                        continue
                    choice = _create_choice(
                        state,
                        player_id=player_id,
                        kind="mill_count",
                        prompt="Choose how many cards to mill.",
                        options=[],
                        min_count=0,
                        max_count=max_count if max_count > 0 else 1,
                        context={
                            "stack_id": stack_item["id"],
                            "effect_index": idx,
                            "target_player_id": target_player.get("user_id"),
                        },
                    )
                    events.append({"type": "choice_requested", "choice": choice})
                    stack_item["pending_effect_index"] = idx
                    return events
                for _ in range(max(0, int(count))):
                    if not target_player["zones"]["library"]:
                        break
                    moved = target_player["zones"]["library"].pop(0)
                    target_player["zones"]["graveyard"].append(moved)
                events.append({"type": "mill", "count": int(count), "player_id": target_player.get("user_id")})
        elif kind == "mill_each_player":
            count = effect.get("count")
            if count is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="mill_count",
                    prompt="Choose how many cards to mill.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={"stack_id": stack_item["id"], "effect_index": idx, "each_player": True},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            for pl in state.get("players", []):
                for _ in range(max(0, int(count))):
                    if not pl["zones"]["library"]:
                        break
                    moved = pl["zones"]["library"].pop(0)
                    pl["zones"]["graveyard"].append(moved)
                events.append({"type": "mill", "count": int(count), "player_id": pl.get("user_id")})
        elif kind == "target_draw":
            count = effect.get("count")
            up_to = bool(effect.get("up_to"))
            targets = (stack_item.get("targets") or {}).get(idx)
            if targets is None:
                options = _collect_targets(state, player_id, {"type": "player", "scope": "any"})
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="target",
                    prompt="Choose target player.",
                    options=options,
                    min_count=1,
                    context={"stack_id": stack_item["id"], "effect_index": idx, "target_spec": {"type": "player", "scope": "any"}},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            target = targets[0] if targets else None
            if not target or target.get("type") != "player":
                continue
            target_player = _find_player(state, int(target.get("id")))
            if not target_player:
                continue
            if count is None or up_to:
                max_count = int(count) if count is not None else len(target_player["zones"]["library"])
                max_count = max(0, max_count)
                if max_count <= 0:
                    events.append({"type": "draw", "count": 0, "player_id": target_player.get("user_id")})
                    continue
                min_count = 0 if up_to or count is None else 1
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="target_draw_count",
                    prompt="Choose how many cards to draw.",
                    options=[],
                    min_count=min_count,
                    max_count=max_count if max_count > 0 else 1,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "target_player_id": target_player.get("user_id"),
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            actual_draws = 0
            for _ in range(max(0, int(count))):
                if not target_player["zones"]["library"]:
                    break
                target_player["zones"]["hand"].append(target_player["zones"]["library"].pop(0))
                actual_draws += 1
            for _ in range(actual_draws):
                events.extend(_enqueue_triggers(state, "draw", event_player_id=target_player.get("user_id")))
            events.append({"type": "draw", "count": int(count), "player_id": target_player.get("user_id")})
        elif kind == "draw_each_opponent" or kind == "draw_each_player":
            count = effect.get("count")
            if count is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="effect_count",
                    prompt="Choose draw amount.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "value_key": "count",
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            count = int(count)
            for pl in state.get("players", []):
                if kind == "draw_each_opponent" and pl.get("user_id") == player_id:
                    continue
                actual_draws = 0
                for _ in range(max(0, int(count))):
                    if not pl["zones"]["library"]:
                        break
                    pl["zones"]["hand"].append(pl["zones"]["library"].pop(0))
                    actual_draws += 1
                for _ in range(actual_draws):
                    events.extend(_enqueue_triggers(state, "draw", event_player_id=pl.get("user_id")))
                events.append({"type": "draw", "count": int(count), "player_id": pl.get("user_id")})
        elif kind == "target_discard":
            count = effect.get("count")
            up_to = bool(effect.get("up_to"))
            targets = (stack_item.get("targets") or {}).get(idx)
            if targets is None:
                options = _collect_targets(state, player_id, {"type": "player", "scope": "any"})
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="target",
                    prompt="Choose target player.",
                    options=options,
                    min_count=1,
                    context={"stack_id": stack_item["id"], "effect_index": idx, "target_spec": {"type": "player", "scope": "any"}},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            target = targets[0] if targets else None
            if not target or target.get("type") != "player":
                continue
            target_player = _find_player(state, int(target.get("id")))
            if not target_player:
                continue
            if count is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="effect_count",
                    prompt="Choose discard amount.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "value_key": "count",
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            max_count = min(int(count or len(target_player["zones"]["hand"])), len(target_player["zones"]["hand"]))
            if max_count <= 0:
                events.append({"type": "discard", "count": 0, "player_id": target_player.get("user_id")})
                continue
            min_count = 0 if up_to else max_count
            options = [
                {"id": card["instance_id"], "label": card.get("name", "Card")}
                for card in target_player["zones"]["hand"]
            ]
            choice = _create_choice(
                state,
                player_id=target_player.get("user_id"),
                kind="target_discard_cards",
                prompt="Choose card(s) to discard.",
                options=options,
                min_count=min_count,
                max_count=max_count,
                context={
                    "stack_id": stack_item["id"],
                    "effect_index": idx,
                    "target_player_id": target_player.get("user_id"),
                },
            )
            events.append({"type": "choice_requested", "choice": choice})
            stack_item["pending_effect_index"] = idx
            return events
        elif kind in {"discard_each_opponent", "discard_each_player"}:
            count = effect.get("count")
            if count is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="effect_count",
                    prompt="Choose discard amount.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "value_key": "count",
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            count = int(count)
            remaining = [p.get("user_id") for p in state.get("players", [])]
            if kind == "discard_each_opponent":
                remaining = [pid for pid in remaining if pid != player_id]
            target_player = None
            target_player_id = None
            options = []
            while remaining:
                target_player_id = remaining.pop(0)
                target_player = _find_player(state, int(target_player_id))
                if not target_player:
                    continue
                options = [
                    {"id": card["instance_id"], "label": card.get("name", "Card")}
                    for card in target_player["zones"]["hand"]
                ]
                if options:
                    break
            if not options or target_player_id is None:
                continue
            required = min(int(count), len(options))
            choice = _create_choice(
                state,
                player_id=int(target_player_id),
                kind="discard_choice",
                prompt="Choose card(s) to discard.",
                options=options,
                min_count=required,
                max_count=required,
                context={
                    "stack_id": stack_item["id"],
                    "effect_index": idx,
                    "remaining_players": remaining,
                    "count": required,
                    "kind": kind,
                },
            )
            events.append({"type": "choice_requested", "choice": choice})
            stack_item["pending_effect_index"] = idx
            return events
        elif kind == "reanimate":
            target_spec = effect.get("target") or {"type": "creature", "scope": "you", "zone": "graveyard"}
            targets = (stack_item.get("targets") or {}).get(idx)
            if targets is None:
                options = _collect_graveyard_targets(state, player_id, target_spec)
                min_count = 0 if effect.get("up_to") else 1
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="graveyard_target",
                    prompt="Choose card from graveyard.",
                    options=options,
                    min_count=min_count,
                    max_count=1,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "destination": effect.get("destination", "battlefield"),
                        "control": effect.get("control"),
                        "tapped": bool(effect.get("tapped")),
                        "set_controller": True,
                        "event_type": "reanimated",
                        "target_spec": target_spec,
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            target = targets[0] if targets else None
            if not target or target.get("type") != "card":
                continue
            destination = effect.get("destination", "battlefield")
            card_obj, moved_events = _resolve_graveyard_move(
                state,
                player_id,
                target.get("id"),
                destination,
                control=effect.get("control"),
                tapped=bool(effect.get("tapped")),
                set_controller=True,
            )
            events.extend(moved_events)
            if card_obj:
                events.append(
                    {"type": "reanimated", "card": card_obj.get("name", "Card"), "destination": destination}
                )
        elif kind == "exile_from_graveyard":
            target_spec = effect.get("target") or {"type": "card", "scope": "any", "zone": "graveyard"}
            targets = (stack_item.get("targets") or {}).get(idx)
            if targets is None:
                options = _collect_graveyard_targets(state, player_id, target_spec)
                min_count = 0 if effect.get("up_to") else 1
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="graveyard_target",
                    prompt="Choose card from graveyard.",
                    options=options,
                    min_count=min_count,
                    max_count=1,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "destination": "exile",
                        "set_controller": False,
                        "event_type": "exiled",
                        "target_spec": target_spec,
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            target = targets[0] if targets else None
            if not target or target.get("type") != "card":
                continue
            card_obj, moved_events = _resolve_graveyard_move(
                state,
                player_id,
                target.get("id"),
                "exile",
                set_controller=False,
            )
            events.extend(moved_events)
            if card_obj:
                events.append({"type": "exiled", "card": card_obj.get("name", "Card")})
        elif kind == "add_counters":
            count = effect.get("count")
            counter_type = effect.get("counter_type") or "+1/+1"
            target_spec = effect.get("target")
            targets: list[dict[str, Any]] = []
            if target_spec:
                targets = (stack_item.get("targets") or {}).get(idx)
                if targets is None:
                    options = _collect_targets(state, player_id, target_spec)
                    choice = _create_choice(
                        state,
                        player_id=player_id,
                        kind="target",
                        prompt="Choose target(s).",
                        options=options,
                        min_count=1,
                        context={"stack_id": stack_item["id"], "effect_index": idx, "target_spec": target_spec},
                    )
                    events.append({"type": "choice_requested", "choice": choice})
                    stack_item["pending_effect_index"] = idx
                    return events
            if count is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="effect_count",
                    prompt="Choose counters amount.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "value_key": "count",
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            count = int(count)
            for target in targets:
                if target.get("type") != "card":
                    continue
                located = _locate_card(state, target.get("id"))
                if not located:
                    continue
                _, _, card_obj = located
                _add_counters(card_obj, counter_type, count)
            events.append({"type": "counters_added", "count": int(count), "counter": counter_type})
        elif kind == "add_counters_team":
            count = effect.get("count")
            counter_type = effect.get("counter_type") or "+1/+1"
            scope = effect.get("scope") or "you"
            if count is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="effect_count",
                    prompt="Choose counters amount.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "value_key": "count",
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            count = int(count)
            for pl in state.get("players", []):
                if scope == "you" and pl.get("user_id") != player_id:
                    continue
                for card in pl.get("zones", {}).get("battlefield", []):
                    if "creature" not in (card.get("type_line") or "").lower():
                        continue
                    _add_counters(card, counter_type, count)
            events.append({"type": "counters_added_team", "count": int(count), "counter": counter_type})
        elif kind == "remove_counters":
            count = effect.get("count")
            counter_type = effect.get("counter_type") or "+1/+1"
            target_spec = effect.get("target")
            targets: list[dict[str, Any]] = []
            if target_spec:
                targets = (stack_item.get("targets") or {}).get(idx)
                if targets is None:
                    options = _collect_targets(state, player_id, target_spec)
                    choice = _create_choice(
                        state,
                        player_id=player_id,
                        kind="target",
                        prompt="Choose target(s).",
                        options=options,
                        min_count=1,
                        context={"stack_id": stack_item["id"], "effect_index": idx, "target_spec": target_spec},
                    )
                    events.append({"type": "choice_requested", "choice": choice})
                    stack_item["pending_effect_index"] = idx
                    return events
            if count is None:
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="effect_count",
                    prompt="Choose counters amount.",
                    options=[],
                    min_count=0,
                    max_count=MAX_X_CHOICE,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "value_key": "count",
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            count = int(count)
            for target in targets:
                if target.get("type") != "card":
                    continue
                located = _locate_card(state, target.get("id"))
                if not located:
                    continue
                _, _, card_obj = located
                _add_counters(card_obj, counter_type, -count)
            events.append({"type": "counters_removed", "count": int(count), "counter": counter_type})
        elif kind in {"sacrifice", "sacrifice_each_opponent", "sacrifice_each_player"}:
            target_type = effect.get("target_type") or "permanent"
            count = effect.get("count")
            if count is None:
                max_available = 0
                if kind == "sacrifice":
                    options = _collect_cost_options(
                        state, player_id, {"kind": "sacrifice", "target_type": target_type}
                    )
                    max_available = len(options)
                elif kind == "sacrifice_each_opponent":
                    for pl in state.get("players", []):
                        if pl.get("user_id") == player_id:
                            continue
                        options = _collect_cost_options(
                            state, int(pl.get("user_id")), {"kind": "sacrifice", "target_type": target_type}
                        )
                        max_available = max(max_available, len(options))
                else:
                    for pl in state.get("players", []):
                        options = _collect_cost_options(
                            state, int(pl.get("user_id")), {"kind": "sacrifice", "target_type": target_type}
                        )
                        max_available = max(max_available, len(options))
                if max_available <= 0:
                    continue
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="effect_count",
                    prompt="Choose sacrifice amount.",
                    options=[],
                    min_count=0,
                    max_count=max_available,
                    context={
                        "stack_id": stack_item["id"],
                        "effect_index": idx,
                        "value_key": "count",
                    },
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            count = int(count or 0)
            if kind == "sacrifice":
                target_player_id = player_id
                remaining = []
            elif kind == "sacrifice_each_opponent":
                remaining = [p.get("user_id") for p in state.get("players", []) if p.get("user_id") != player_id]
                if not remaining:
                    continue
                target_player_id = remaining.pop(0)
            else:
                remaining = [p.get("user_id") for p in state.get("players", [])]
                if not remaining:
                    continue
                target_player_id = remaining.pop(0)
            options = _collect_cost_options(state, int(target_player_id), {"kind": "sacrifice", "target_type": target_type})
            if not options:
                events.append({"type": "sacrifice_skipped", "player_id": target_player_id})
                continue
            required = min(count, len(options))
            choice = _create_choice(
                state,
                player_id=int(target_player_id),
                kind="sacrifice_choice",
                prompt="Choose permanent(s) to sacrifice.",
                options=options,
                min_count=required,
                max_count=required,
                context={
                    "stack_id": stack_item["id"],
                    "effect_index": idx,
                    "target_type": target_type,
                    "remaining_players": remaining,
                    "chain_kind": kind,
                    "count": required,
                },
            )
            events.append({"type": "choice_requested", "choice": choice})
            stack_item["pending_effect_index"] = idx
            return events
        elif kind == "pump_until_eot":
            target_spec = effect.get("target")
            power_delta = int(effect.get("power_delta") or 0)
            toughness_delta = int(effect.get("toughness_delta") or 0)
            up_to = bool(effect.get("up_to"))
            targets = (stack_item.get("targets") or {}).get(idx)
            if targets is None:
                options = _collect_targets(state, player_id, target_spec or {"type": "creature", "scope": "any"})
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="target",
                    prompt="Choose target(s).",
                    options=options,
                    min_count=0 if up_to else 1,
                    context={"stack_id": stack_item["id"], "effect_index": idx, "target_spec": target_spec},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            for target in targets:
                if target.get("type") != "card":
                    continue
                located = _locate_card(state, target.get("id"))
                if not located:
                    continue
                _, _, card_obj = located
                _apply_temp_buff(card_obj, power_delta, toughness_delta)
            events.append({"type": "pump_until_eot", "power": power_delta, "toughness": toughness_delta})
        elif kind == "pump_team_until_eot":
            power_delta = int(effect.get("power_delta") or 0)
            toughness_delta = int(effect.get("toughness_delta") or 0)
            for pl in state.get("players", []):
                if pl.get("user_id") != player_id:
                    continue
                for card in pl.get("zones", {}).get("battlefield", []):
                    if "creature" not in (card.get("type_line") or "").lower():
                        continue
                    _apply_temp_buff(card, power_delta, toughness_delta)
            events.append({"type": "pump_team_until_eot", "power": power_delta, "toughness": toughness_delta})
        elif kind == "grant_keyword_until_eot":
            keyword = effect.get("keyword") or ""
            target_spec = effect.get("target")
            up_to = bool(effect.get("up_to"))
            targets = (stack_item.get("targets") or {}).get(idx)
            if targets is None:
                options = _collect_targets(state, player_id, target_spec or {"type": "creature", "scope": "any"})
                choice = _create_choice(
                    state,
                    player_id=player_id,
                    kind="target",
                    prompt="Choose target(s).",
                    options=options,
                    min_count=0 if up_to else 1,
                    context={"stack_id": stack_item["id"], "effect_index": idx, "target_spec": target_spec},
                )
                events.append({"type": "choice_requested", "choice": choice})
                stack_item["pending_effect_index"] = idx
                return events
            for target in targets:
                if target.get("type") != "card":
                    continue
                located = _locate_card(state, target.get("id"))
                if not located:
                    continue
                _, _, card_obj = located
                _apply_temp_keyword(card_obj, keyword)
            events.append({"type": "keyword_until_eot", "keyword": keyword})
        elif kind == "grant_keyword_team_until_eot":
            keyword = effect.get("keyword") or ""
            for pl in state.get("players", []):
                if pl.get("user_id") != player_id:
                    continue
                for card in pl.get("zones", {}).get("battlefield", []):
                    if "creature" not in (card.get("type_line") or "").lower():
                        continue
                    _apply_temp_keyword(card, keyword)
            events.append({"type": "keyword_team_until_eot", "keyword": keyword})

    stack_item["pending_effect_index"] = len(effects)
    return events


def _resolve_stack_top(state: dict[str, Any], player_id: int) -> list[dict[str, Any]]:
    if not state.get("stack"):
        return []
    stack_item = state["stack"][-1]
    events = _resolve_effects(state, player_id, stack_item)
    if state.get("choices"):
        return events
    state["stack"].pop()
    card = stack_item.get("card") or {}
    owner_id = stack_item.get("controller_id") or player_id
    player = _find_player(state, owner_id)
    if player:
        if _is_instant_or_sorcery(card):
            player["zones"]["graveyard"].append(card)
        else:
            player["zones"]["battlefield"].append(card)
            if "creature" in (card.get("type_line") or "").lower():
                card["summoning_sick"] = True
            events.extend(
                _enqueue_triggers(state, "enter_battlefield", event_player_id=owner_id, event_card=card)
            )
    events.append({"type": "stack_resolved", "card": card.get("name", "Card")})
    return events


def _apply_step_actions(state: dict[str, Any], phase: str, step: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    turn = state.get("turn") or {}
    active_player_id = turn.get("active_player")
    active_player = _find_player(state, active_player_id) if active_player_id is not None else None
    if phase == "beginning" and step == "untap":
        if active_player:
            for card in active_player.get("zones", {}).get("battlefield", []):
                card["tapped"] = False
                if card.get("summoning_sick") is not None:
                    card["summoning_sick"] = False
            events.append({"type": "untap_step", "player_id": active_player_id})
        lands = state.setdefault("meta", {}).setdefault("lands_played", {})
        if active_player_id is not None:
            lands[str(active_player_id)] = 0
    if phase == "beginning" and step == "upkeep":
        events.extend(_enqueue_triggers(state, "upkeep", event_player_id=active_player_id))
    if phase == "combat" and step == "begin_combat":
        state["combat"] = {
            "attackers": {},
            "blockers": {},
            "declared_attackers": False,
            "declared_blockers": False,
            "damage_resolved": False,
        }
        events.append({"type": "combat_started"})
        events.extend(_enqueue_triggers(state, "begin_combat", event_player_id=active_player_id))
    if phase == "beginning" and step == "draw":
        order = [p.get("user_id") for p in state.get("players", [])]
        skip_first_draw = (
            len(order) == 2
            and int(turn.get("number", 1)) == 1
            and active_player_id == order[0]
        )
        events.extend(_enqueue_triggers(state, "draw_step", event_player_id=active_player_id))
        if skip_first_draw:
            events.append({"type": "draw_step", "player_id": active_player_id, "count": 0})
        elif active_player and active_player.get("zones", {}).get("library"):
            active_player["zones"]["hand"].append(active_player["zones"]["library"].pop(0))
            events.append({"type": "draw_step", "player_id": active_player_id, "count": 1})
            events.extend(_enqueue_triggers(state, "draw", event_player_id=active_player_id))
        else:
            events.append({"type": "draw_step", "player_id": active_player_id, "count": 0})
    if phase == "ending" and step == "cleanup":
        for player in state.get("players", []):
            for card in player.get("zones", {}).get("battlefield", []):
                if card.get("damage"):
                    card["damage"] = 0
                if card.get("temp_power"):
                    card["temp_power"] = 0
                if card.get("temp_toughness"):
                    card["temp_toughness"] = 0
                if card.get("temp_keywords"):
                    card["temp_keywords"] = []
        events.append({"type": "cleanup"})
    if phase == "ending" and step == "end_step":
        events.extend(_enqueue_triggers(state, "end_step", event_player_id=active_player_id))
    if phase == "combat" and step == "end_combat":
        combat_state = state.get("combat") or {}
        if combat_state:
            state["combat"] = {}
        for player in state.get("players", []):
            for card in player.get("zones", {}).get("battlefield", []):
                if "attacking" in card:
                    card.pop("attacking", None)
                if "blocking" in card:
                    card.pop("blocking", None)
    return events


def _advance_turn(state: dict[str, Any]) -> list[dict[str, Any]]:
    turn = state.get("turn") or {}
    current = (turn.get("phase"), turn.get("step"))
    idx = TURN_SEQUENCE.index(current) if current in TURN_SEQUENCE else 0
    idx = (idx + 1) % len(TURN_SEQUENCE)
    phase, step = TURN_SEQUENCE[idx]
    if idx == 0:
        turn["number"] = int(turn.get("number", 1)) + 1
        order = [p.get("user_id") for p in state.get("players", [])]
        if order:
            current_active = turn.get("active_player")
            if current_active in order:
                turn["active_player"] = order[(order.index(current_active) + 1) % len(order)]
            else:
                turn["active_player"] = order[0]
    turn["phase"] = phase
    turn["step"] = step
    turn["passed"] = []
    return _apply_step_actions(state, phase, step)


def _has_priority(state: dict[str, Any], player_id: int) -> bool:
    turn = state.get("turn") or {}
    return turn.get("priority_player") == player_id


def _is_main_phase(state: dict[str, Any]) -> bool:
    turn = state.get("turn") or {}
    return turn.get("phase") == "main" and turn.get("step") in {"precombat", "postcombat"}


def _stack_is_empty(state: dict[str, Any]) -> bool:
    return not state.get("stack")


def _get_mulligan_state(state: dict[str, Any]) -> dict[str, Any]:
    mulligan = state.setdefault("mulligan", {})
    mulligan.setdefault("counts", {})
    mulligan.setdefault("kept", {})
    mulligan.setdefault("status", "pending")
    return mulligan


def _push_spell_to_stack(state: dict[str, Any], player: dict[str, Any], card_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    found = _find_card_in_zone(player, "hand", card_id)
    if not found:
        return events
    idx, card = found
    player["zones"]["hand"].pop(idx)
    stack_item = {
        "id": uuid.uuid4().hex,
        "card": card,
        "controller_id": player.get("user_id"),
        "effects": [],
        "pending_effect_index": 0,
        "targets": {},
    }
    state.setdefault("stack", []).append(stack_item)
    state.setdefault("turn", {}).setdefault("passed", [])
    state["turn"]["passed"] = []
    events.append({"type": "spell_cast", "card": card.get("name", "Card")})
    events.extend(
        _enqueue_triggers(
            state,
            "cast_spell",
            event_player_id=player.get("user_id"),
            event_card=card,
        )
    )
    return events


def _normalize_blocker_order(blockers: list[str], provided: list[str] | None) -> list[str]:
    if not blockers:
        return []
    if not provided:
        return list(blockers)
    order = [bid for bid in provided if bid in blockers]
    for bid in blockers:
        if bid not in order:
            order.append(bid)
    return order


def apply_action(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    action_type = action.get("action_type")
    player_id = int(action.get("player_id"))
    payload = action.get("payload") or {}
    events: list[dict[str, Any]] = []
    player = _find_player(state, player_id)
    if not player:
        return {"ok": False, "error": "player_not_found", "events": events}
    for pl in state.get("players", []):
        pl.setdefault("commander_damage", {})
    status = state.get("status", "waiting")
    if status == "waiting" and action_type not in {"load_deck", "start_game", "adjust_life", "set_commander", "adjust_commander_damage"}:
        return {"ok": False, "error": "game_not_started", "events": events}
    if status == "mulligan" and action_type not in {
        "mulligan",
        "keep_hand",
        "resolve_choice",
        "adjust_life",
        "set_commander",
        "adjust_commander_damage",
    }:
        return {"ok": False, "error": "mulligan_in_progress", "events": events}
    if state.get("choices") and action_type != "resolve_choice":
        return {"ok": False, "error": "choice_pending", "events": events}
    if action_type in {"play_land", "cast_spell", "resolve_top", "pass_priority"}:
        if not _has_priority(state, player_id):
            return {"ok": False, "error": "priority_required", "events": events}

    if action_type == "load_deck":
        cards = payload.get("cards") or []
        player["zones"]["library"] = [_make_card_instance(card, player_id) for card in cards]
        if payload.get("shuffle", True):
            random.shuffle(player["zones"]["library"])
        events.append({"type": "deck_loaded", "count": len(cards)})
    elif action_type == "start_game":
        format_name = (state.get("format") or "commander").lower()
        starting_player = payload.get("starting_player_id") or state.get("turn", {}).get("active_player")
        order = [p.get("user_id") for p in state.get("players", [])]
        if starting_player not in order and order:
            starting_player = order[0]
        state["status"] = "mulligan"
        for pl in state.get("players", []):
            zones = pl.get("zones", {})
            library = zones.get("library", [])
            for zone_name in ["hand", "battlefield", "graveyard", "exile", "command"]:
                library.extend(zones.get(zone_name, []))
                zones[zone_name] = []
            random.shuffle(library)
            zones["library"] = library
            opening = []
            for _ in range(7):
                if not library:
                    break
                opening.append(library.pop(0))
            zones["hand"] = opening
            pl["life"] = 40 if format_name == "commander" else 20
            pl["commander_damage"] = {}
        state.setdefault("meta", {})["lands_played"] = {str(p.get("user_id")): 0 for p in state.get("players", [])}
        turn = state.setdefault("turn", {})
        turn["number"] = 1
        turn["phase"] = "beginning"
        turn["step"] = "untap"
        turn["active_player"] = starting_player
        turn["priority_player"] = starting_player
        turn["passed"] = []
        state["combat"] = {}
        mulligan = _get_mulligan_state(state)
        mulligan["status"] = "pending"
        for pl in state.get("players", []):
            pid = str(pl.get("user_id"))
            mulligan["counts"][pid] = 0
            mulligan["kept"][pid] = False
        events.append({"type": "game_started", "starting_player": starting_player})
    elif action_type == "mulligan":
        if state.get("status") != "mulligan":
            return {"ok": False, "error": "not_in_mulligan", "events": events}
        mulligan = _get_mulligan_state(state)
        pid_key = str(player_id)
        if mulligan["kept"].get(pid_key):
            return {"ok": False, "error": "already_kept", "events": events}
        zones = player.get("zones", {})
        library = zones.get("library", [])
        library.extend(zones.get("hand", []))
        zones["hand"] = []
        random.shuffle(library)
        zones["library"] = library
        mulligan["counts"][pid_key] = int(mulligan["counts"].get(pid_key, 0)) + 1
        for _ in range(7):
            if not library:
                break
            zones["hand"].append(library.pop(0))
        events.append({"type": "mulligan_taken", "player_id": player_id, "count": mulligan["counts"][pid_key]})
    elif action_type == "keep_hand":
        if state.get("status") != "mulligan":
            return {"ok": False, "error": "not_in_mulligan", "events": events}
        mulligan = _get_mulligan_state(state)
        pid_key = str(player_id)
        if mulligan["kept"].get(pid_key):
            return {"ok": False, "error": "already_kept", "events": events}
        count = int(mulligan["counts"].get(pid_key, 0))
        if count > 0:
            hand = player.get("zones", {}).get("hand", [])
            max_count = min(count, len(hand))
            choice = _create_choice(
                state,
                player_id=player_id,
                kind="bottom_cards",
                prompt=f"Choose {max_count} card(s) to put on the bottom of your library.",
                options=[{"id": card["instance_id"], "label": card.get("name", "Card")} for card in hand],
                min_count=max_count,
                max_count=max_count,
                context={"mulligan_bottom": True},
            )
            events.append({"type": "choice_requested", "choice": choice})
            return {"ok": True, "state": state, "events": events}
        mulligan["kept"][pid_key] = True
        if all(mulligan["kept"].get(str(pid)) for pid in [p.get("user_id") for p in state.get("players", [])]):
            mulligan["status"] = "complete"
            state["status"] = "active"
            events.append({"type": "mulligan_complete"})
    elif action_type == "draw":
        count = int(payload.get("count") or 1)
        actual_draws = 0
        for _ in range(max(0, count)):
            if not player["zones"]["library"]:
                break
            player["zones"]["hand"].append(player["zones"]["library"].pop(0))
            actual_draws += 1
        events.append({"type": "draw", "count": count})
        for _ in range(actual_draws):
            events.extend(_enqueue_triggers(state, "draw", event_player_id=player_id))
    elif action_type == "adjust_life":
        target_id = payload.get("target_id")
        target_id = int(target_id) if target_id is not None else player_id
        target_player = _find_player(state, target_id)
        if not target_player:
            return {"ok": False, "error": "target_not_found", "events": events}
        if "life" in payload:
            try:
                new_life = int(payload.get("life"))
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_life_value", "events": events}
            delta = new_life - int(target_player.get("life") or 0)
            target_player["life"] = new_life
        else:
            try:
                delta = int(payload.get("delta") or 0)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_life_delta", "events": events}
            if delta == 0:
                return {"ok": True, "state": state, "events": events}
            target_player["life"] = int(target_player.get("life") or 0) + delta
        events.append(
            {
                "type": "life_adjusted",
                "target": target_id,
                "delta": int(delta),
                "total": int(target_player.get("life") or 0),
            }
        )
    elif action_type == "adjust_commander_damage":
        target_id = payload.get("target_id")
        source_id = payload.get("source_id")
        if target_id is None or source_id is None:
            return {"ok": False, "error": "commander_damage_requires_target_and_source", "events": events}
        try:
            target_id = int(target_id)
            source_id = int(source_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_commander_damage_target", "events": events}
        target_player = _find_player(state, target_id)
        if not target_player:
            return {"ok": False, "error": "target_not_found", "events": events}
        damage_map = target_player.setdefault("commander_damage", {})
        if "total" in payload:
            try:
                new_total = int(payload.get("total") or 0)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_commander_damage_total", "events": events}
            new_total = max(0, new_total)
            delta = new_total - int(damage_map.get(str(source_id), 0) or 0)
            damage_map[str(source_id)] = new_total
        else:
            try:
                delta = int(payload.get("delta") or 0)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_commander_damage_delta", "events": events}
            if delta == 0:
                return {"ok": True, "state": state, "events": events}
            current = int(damage_map.get(str(source_id), 0) or 0)
            damage_map[str(source_id)] = max(0, current + delta)
        events.append(
            {
                "type": "commander_damage_adjusted",
                "target": target_id,
                "source": source_id,
                "delta": int(delta),
                "total": int(damage_map.get(str(source_id), 0) or 0),
            }
        )
    elif action_type == "set_commander":
        card_id = payload.get("card_id")
        if not card_id:
            return {"ok": False, "error": "card_id_required", "events": events}
        desired = payload.get("is_commander")
        is_commander = True if desired is None else bool(desired)
        located = None
        for zone_name, cards in (player.get("zones") or {}).items():
            for card in cards:
                if card.get("instance_id") == card_id:
                    located = card
                    break
            if located:
                break
        if not located:
            return {"ok": False, "error": "card_not_found", "events": events}
        if int(located.get("owner_id") or player_id) != player_id:
            return {"ok": False, "error": "not_card_owner", "events": events}
        located["is_commander"] = is_commander
        if is_commander:
            located["commander_owner_id"] = int(located.get("commander_owner_id") or player_id)
        else:
            located.pop("commander_owner_id", None)
        events.append(
            {
                "type": "commander_flagged",
                "card": located.get("name", "Card"),
                "card_id": card_id,
                "is_commander": is_commander,
            }
        )
    elif action_type == "play_land":
        card_id = payload.get("card_id")
        if card_id:
            turn = state.get("turn") or {}
            if turn.get("active_player") != player_id:
                return {"ok": False, "error": "not_active_player", "events": events}
            if not _is_main_phase(state):
                return {"ok": False, "error": "not_main_phase", "events": events}
            if not _stack_is_empty(state):
                return {"ok": False, "error": "stack_not_empty", "events": events}
            lands = state.setdefault("meta", {}).setdefault("lands_played", {})
            if int(lands.get(str(player_id), 0)) >= 1:
                return {"ok": False, "error": "land_play_limit", "events": events}
            found = _find_card_in_zone(player, "hand", card_id)
            if not found:
                return {"ok": False, "error": "card_not_found", "events": events}
            _, card = found
            if "land" not in (card.get("type_line") or "").lower():
                return {"ok": False, "error": "not_land", "events": events}
            moved = _move_card(player, "hand", "battlefield", card_id)
            if moved:
                lands[str(player_id)] = int(lands.get(str(player_id), 0)) + 1
                events.append({"type": "land_played", "card": moved.get("name", "Land")})
                if "creature" in (moved.get("type_line") or "").lower():
                    moved["summoning_sick"] = True
                events.extend(
                    _enqueue_triggers(state, "enter_battlefield", event_player_id=player_id, event_card=moved)
                )
    elif action_type == "cast_spell":
        card_id = payload.get("card_id")
        if card_id:
            found = _find_card_in_zone(player, "hand", card_id)
            if not found:
                return {"ok": False, "error": "card_not_found", "events": events}
            idx, card = found
            type_line = (card.get("type_line") or "").lower()
            is_instant = "instant" in type_line
            if not is_instant:
                turn = state.get("turn") or {}
                if turn.get("active_player") != player_id:
                    return {"ok": False, "error": "not_active_player", "events": events}
                if not _is_main_phase(state):
                    return {"ok": False, "error": "not_main_phase", "events": events}
                if not _stack_is_empty(state):
                    return {"ok": False, "error": "stack_not_empty", "events": events}
            costs = _parse_costs(card.get("oracle_text") or "")
            if costs:
                remaining_costs = list(costs)
                while remaining_costs:
                    cost = remaining_costs[0]
                    options = _collect_cost_options(state, player_id, cost)
                    count = cost.get("count")
                    if count is None:
                        if not options:
                            remaining_costs = remaining_costs[1:]
                            continue
                        min_count = 0
                        max_count = len(options)
                    else:
                        required = int(count or 0)
                        if required <= 0:
                            remaining_costs = remaining_costs[1:]
                            continue
                        if len(options) < required:
                            return {"ok": False, "error": "cost_cannot_be_paid", "events": events}
                        min_count = required
                        max_count = required
                    choice = _create_choice(
                        state,
                        player_id=player_id,
                        kind="pay_cost",
                        prompt="Pay additional cost.",
                        options=options,
                        min_count=min_count,
                        max_count=max_count,
                        context={
                            "card_id": card_id,
                            "cost": cost,
                            "remaining_costs": remaining_costs[1:],
                        },
                    )
                    events.append({"type": "choice_requested", "choice": choice})
                    return {"ok": True, "state": state, "events": events}
            events.extend(_push_spell_to_stack(state, player, card_id))
    elif action_type == "resolve_top":
        if not state.get("stack"):
            return {"ok": False, "error": "stack_empty", "events": events}
        events.extend(_resolve_stack_top(state, player_id))
    elif action_type == "pass_priority":
        turn = state.setdefault("turn", {})
        passed = turn.setdefault("passed", [])
        if player_id not in passed:
            passed.append(player_id)
        order = [p.get("user_id") for p in state.get("players", [])]
        if not order:
            return {"ok": True, "state": state, "events": events}
        idx = order.index(player_id) if player_id in order else 0
        next_player = order[(idx + 1) % len(order)]
        turn["priority_player"] = next_player
        if all(pid in passed for pid in order):
            if state.get("stack"):
                events.extend(_resolve_stack_top(state, player_id))
                turn["priority_player"] = turn.get("active_player")
            else:
                events.extend(_advance_turn(state))
                turn["priority_player"] = turn.get("active_player")
            turn["passed"] = []
        events.append({"type": "priority_passed", "player_id": player_id})
    elif action_type == "declare_attackers":
        turn = state.get("turn") or {}
        if turn.get("phase") != "combat" or turn.get("step") != "declare_attackers":
            return {"ok": False, "error": "not_declare_attackers_step", "events": events}
        if turn.get("active_player") != player_id:
            return {"ok": False, "error": "not_active_player", "events": events}
        combat_state = state.setdefault("combat", {})
        attackers_payload = payload.get("attackers") or []
        normalized: list[dict[str, Any]] = []
        if attackers_payload and all(isinstance(item, str) for item in attackers_payload):
            defender = _parse_defender(payload.get("defender"))
            if not defender:
                return {"ok": False, "error": "defender_required", "events": events}
            for attacker_id in attackers_payload:
                normalized.append({"attacker_id": attacker_id, "defender": defender})
        else:
            for item in attackers_payload:
                if not isinstance(item, dict):
                    continue
                defender = _parse_defender(item.get("defender"))
                if not defender:
                    return {"ok": False, "error": "defender_required", "events": events}
                normalized.append({"attacker_id": item.get("attacker_id"), "defender": defender})

        for entry in normalized:
            attacker_id = entry.get("attacker_id")
            defender = entry.get("defender")
            if not attacker_id or not defender:
                continue
            found = _find_card_in_zone(player, "battlefield", attacker_id)
            if not found:
                return {"ok": False, "error": "attacker_not_found", "events": events}
            _, card = found
            if "creature" not in (card.get("type_line") or "").lower():
                return {"ok": False, "error": "attacker_not_creature", "events": events}
            if card.get("tapped"):
                return {"ok": False, "error": "attacker_tapped", "events": events}
            if card.get("summoning_sick") and not _has_keyword(card, "haste"):
                return {"ok": False, "error": "attacker_summoning_sick", "events": events}
            if _has_keyword(card, "defender"):
                return {"ok": False, "error": "attacker_has_defender", "events": events}
            defender_controller = _defender_controller_id(state, defender)
            if defender_controller is None:
                return {"ok": False, "error": "invalid_defender", "events": events}
            card["attacking"] = defender
            if not _has_keyword(card, "vigilance"):
                card["tapped"] = True
            combat_state.setdefault("attackers", {})[attacker_id] = defender
            events.extend(_enqueue_triggers(state, "attacks", event_player_id=player_id, event_card=card))
        combat_state["declared_attackers"] = True
        events.append({"type": "attackers_declared", "count": len(combat_state.get("attackers", {}))})
    elif action_type == "declare_blockers":
        turn = state.get("turn") or {}
        if turn.get("phase") != "combat" or turn.get("step") != "declare_blockers":
            return {"ok": False, "error": "not_declare_blockers_step", "events": events}
        combat_state = state.setdefault("combat", {})
        blocks = payload.get("blocks") or []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            blocker_id = block.get("blocker_id")
            attacker_id = block.get("attacker_id")
            if not blocker_id or not attacker_id:
                continue
            attacker_defender = (combat_state.get("attackers") or {}).get(attacker_id)
            if not attacker_defender:
                return {"ok": False, "error": "attacker_not_attacking", "events": events}
            defender_controller = _defender_controller_id(state, attacker_defender)
            if defender_controller != player_id:
                return {"ok": False, "error": "not_defending_player", "events": events}
            attacker_located = _locate_card(state, attacker_id)
            if not attacker_located:
                return {"ok": False, "error": "attacker_not_found", "events": events}
            _, attacker_zone, attacker_card = attacker_located
            if attacker_zone != "battlefield":
                return {"ok": False, "error": "attacker_not_on_battlefield", "events": events}
            if _has_cant_be_blocked(attacker_card):
                return {"ok": False, "error": "attacker_unblockable", "events": events}
            found = _find_card_in_zone(player, "battlefield", blocker_id)
            if not found:
                return {"ok": False, "error": "blocker_not_found", "events": events}
            _, blocker = found
            if "creature" not in (blocker.get("type_line") or "").lower():
                return {"ok": False, "error": "blocker_not_creature", "events": events}
            if blocker.get("tapped"):
                return {"ok": False, "error": "blocker_tapped", "events": events}
            if blocker.get("blocking"):
                return {"ok": False, "error": "blocker_already_blocking", "events": events}
            if _has_keyword(attacker_card, "flying") and not (
                _has_keyword(blocker, "flying") or _has_keyword(blocker, "reach")
            ):
                return {"ok": False, "error": "blocker_cannot_block_flying", "events": events}
            protection_colors = _protection_colors(attacker_card)
            if protection_colors:
                blocker_colors = _source_colors_for_protection(blocker)
                if protection_colors.intersection(blocker_colors):
                    return {"ok": False, "error": "blocker_blocked_by_protection", "events": events}
            combat_state.setdefault("blockers", {}).setdefault(attacker_id, []).append(blocker_id)
            blocker["blocking"] = attacker_id
            events.extend(_enqueue_triggers(state, "blocks", event_player_id=player_id, event_card=blocker))
        blocker_order_payload = payload.get("blocker_order") or {}
        for attacker_id, provided_order in blocker_order_payload.items():
            if not isinstance(provided_order, list):
                continue
            base = (combat_state.get("blockers") or {}).get(attacker_id) or []
            if not base:
                continue
            combat_state.setdefault("blocker_order", {})[attacker_id] = _normalize_blocker_order(base, provided_order)
        was_blocked = combat_state.setdefault("was_blocked", {})
        for attacker_id, blocker_list in (combat_state.get("blockers") or {}).items():
            was_blocked[attacker_id] = bool(blocker_list)
        for attacker_id, defender in (combat_state.get("attackers") or {}).items():
            if defender.get("type") != "player" and defender.get("type") != "card":
                continue
            attacker_located = _locate_card(state, attacker_id)
            if not attacker_located:
                continue
            _, attacker_zone, attacker_card = attacker_located
            if attacker_zone != "battlefield":
                continue
            if _has_keyword(attacker_card, "menace"):
                count = len((combat_state.get("blockers") or {}).get(attacker_id) or [])
                if count == 1:
                    return {"ok": False, "error": "menace_requires_two_blockers", "events": events}
        combat_state["declared_blockers"] = True
        events.append({"type": "blockers_declared"})
    elif action_type == "combat_damage":
        turn = state.get("turn") or {}
        if turn.get("phase") != "combat" or turn.get("step") != "damage":
            return {"ok": False, "error": "not_combat_damage_step", "events": events}
        if turn.get("active_player") != player_id:
            return {"ok": False, "error": "not_active_player", "events": events}
        combat_state = state.setdefault("combat", {})
        if combat_state.get("damage_resolved"):
            return {"ok": False, "error": "combat_damage_already_resolved", "events": events}
        order_override = payload.get("blocker_order") or {}
        for attacker_id, provided_order in order_override.items():
            if not isinstance(provided_order, list):
                continue
            base = (combat_state.get("blockers") or {}).get(attacker_id) or []
            if not base:
                continue
            combat_state.setdefault("blocker_order", {})[attacker_id] = _normalize_blocker_order(base, provided_order)

        attackers = combat_state.get("attackers") or {}
        blockers = combat_state.get("blockers") or {}
        was_blocked = combat_state.get("was_blocked") or {}

        def combat_has_first_strike() -> bool:
            for attacker_id in attackers:
                located = _locate_card(state, attacker_id)
                if not located:
                    continue
                _, zone_name, card = located
                if zone_name != "battlefield":
                    continue
                if _has_keyword(card, "first strike") or _has_keyword(card, "double strike"):
                    return True
            for blocker_list in blockers.values():
                for blocker_id in blocker_list:
                    located = _locate_card(state, blocker_id)
                    if not located:
                        continue
                    _, zone_name, card = located
                    if zone_name != "battlefield":
                        continue
                    if _has_keyword(card, "first strike") or _has_keyword(card, "double strike"):
                        return True
            return False

        steps = ["first", "normal"] if combat_has_first_strike() else ["normal"]
        for step in steps:
            assignments: list[dict[str, Any]] = []
            for attacker_id, defender in attackers.items():
                located = _locate_card(state, attacker_id)
                if not located:
                    continue
                _, zone_name, attacker = located
                if zone_name != "battlefield":
                    continue
                if "creature" not in (attacker.get("type_line") or "").lower():
                    continue
                if not _creature_can_strike(attacker, step):
                    continue
                attacker_power = _power_value(attacker)
                if attacker_power <= 0:
                    continue
                blocker_ids = blockers.get(attacker_id) or []
                live_blockers = []
                for blocker_id in blocker_ids:
                    located_blocker = _locate_card(state, blocker_id)
                    if not located_blocker:
                        continue
                    _, blocker_zone, _ = located_blocker
                    if blocker_zone != "battlefield":
                        continue
                    live_blockers.append(blocker_id)

                has_trample = _has_keyword(attacker, "trample")
                has_deathtouch = _has_keyword(attacker, "deathtouch")
                has_lifelink = _has_keyword(attacker, "lifelink")
                source_controller = int(attacker.get("controller_id") or attacker.get("owner_id") or 0)

                if live_blockers:
                    order = (combat_state.get("blocker_order") or {}).get(attacker_id)
                    order = _normalize_blocker_order(live_blockers, order)
                    remaining = attacker_power
                    for blocker_id in order:
                        if remaining <= 0:
                            break
                        located_blocker = _locate_card(state, blocker_id)
                        if not located_blocker:
                            continue
                        _, blocker_zone, blocker_card = located_blocker
                        if blocker_zone != "battlefield":
                            continue
                        if has_deathtouch:
                            lethal = 1 if remaining > 0 else 0
                        else:
                            try:
                                toughness_value = int(blocker_card.get("toughness"))
                            except (TypeError, ValueError):
                                toughness_value = None
                            current_damage = int(blocker_card.get("damage") or 0)
                            lethal = remaining if toughness_value is None else max(0, toughness_value - current_damage)
                        if lethal <= 0:
                            continue
                        assign = min(remaining, lethal)
                        if assign <= 0:
                            continue
                        assignments.append(
                            {
                                "source_id": attacker_id,
                                "source_controller": source_controller,
                                "source_lifelink": has_lifelink,
                                "source_deathtouch": has_deathtouch,
                                "source_colors": list(_source_colors_for_protection(attacker)),
                                "target_type": "card",
                                "target_id": blocker_id,
                                "amount": int(assign),
                            }
                        )
                        remaining -= int(assign)
                    if has_trample and remaining > 0:
                        assignments.append(
                            {
                                "source_id": attacker_id,
                                "source_controller": source_controller,
                                "source_lifelink": has_lifelink,
                                "source_deathtouch": has_deathtouch,
                                "source_colors": list(_source_colors_for_protection(attacker)),
                                "target_type": defender.get("type"),
                                "target_id": defender.get("id"),
                                "amount": int(remaining),
                            }
                        )
                else:
                    if was_blocked.get(attacker_id) and not has_trample:
                        continue
                    assignments.append(
                        {
                            "source_id": attacker_id,
                            "source_controller": source_controller,
                            "source_lifelink": has_lifelink,
                            "source_deathtouch": has_deathtouch,
                            "source_colors": list(_source_colors_for_protection(attacker)),
                            "target_type": defender.get("type"),
                            "target_id": defender.get("id"),
                            "amount": int(attacker_power),
                        }
                    )

            for attacker_id, blocker_ids in blockers.items():
                attacker_located = _locate_card(state, attacker_id)
                if not attacker_located:
                    continue
                _, attacker_zone, attacker_card = attacker_located
                if attacker_zone != "battlefield":
                    continue
                for blocker_id in blocker_ids:
                    blocker_located = _locate_card(state, blocker_id)
                    if not blocker_located:
                        continue
                    _, blocker_zone, blocker_card = blocker_located
                    if blocker_zone != "battlefield":
                        continue
                    if "creature" not in (blocker_card.get("type_line") or "").lower():
                        continue
                    if not _creature_can_strike(blocker_card, step):
                        continue
                    blocker_power = _power_value(blocker_card)
                    if blocker_power <= 0:
                        continue
                    assignments.append(
                        {
                            "source_id": blocker_id,
                            "source_controller": int(blocker_card.get("controller_id") or blocker_card.get("owner_id") or 0),
                            "source_lifelink": _has_keyword(blocker_card, "lifelink"),
                            "source_deathtouch": _has_keyword(blocker_card, "deathtouch"),
                            "source_colors": list(_source_colors_for_protection(blocker_card)),
                            "target_type": "card",
                            "target_id": attacker_id,
                            "amount": int(blocker_power),
                        }
                    )

            lifelink_gains: dict[int, int] = {}
            for assignment in assignments:
                target_type = assignment.get("target_type")
                target_id = assignment.get("target_id")
                amount = int(assignment.get("amount") or 0)
                if amount <= 0:
                    continue
                if target_type == "player":
                    try:
                        pid = int(target_id)
                    except (TypeError, ValueError):
                        continue
                    pl = _find_player(state, pid)
                    if not pl:
                        continue
                    pl["life"] -= amount
                    events.append({"type": "combat_damage", "target": pid, "amount": amount})
                    events.extend(_enqueue_triggers(state, "life_loss", event_player_id=pid))
                    source_card = None
                    source_id = assignment.get("source_id")
                    if source_id:
                        located_source = _locate_card(state, source_id)
                        if located_source:
                            _, _, source_card = located_source
                    if source_card and source_card.get("is_commander"):
                        format_name = (state.get("format") or "commander").lower()
                        if format_name == "commander":
                            commander_owner = (
                                source_card.get("commander_owner_id")
                                or source_card.get("owner_id")
                                or assignment.get("source_controller")
                            )
                            if commander_owner is not None:
                                damage_map = pl.setdefault("commander_damage", {})
                                key = str(int(commander_owner))
                                damage_map[key] = int(damage_map.get(key, 0) or 0) + amount
                                events.append(
                                    {
                                        "type": "commander_damage",
                                        "target": pid,
                                        "source": int(commander_owner),
                                        "amount": amount,
                                        "total": int(damage_map.get(key, 0) or 0),
                                    }
                                )
                    events.extend(
                        _enqueue_triggers(
                            state,
                            "combat_damage_to_player",
                            event_player_id=int(assignment.get("source_controller") or 0),
                            event_card=source_card,
                        )
                    )
                elif target_type == "card":
                    located = _locate_card(state, target_id)
                    if not located:
                        continue
                    owner, zone_name, card_obj = located
                    source_colors = set(assignment.get("source_colors") or [])
                    if _damage_prevented_by_protection(card_obj, source_colors):
                        continue
                    events.extend(
                        _apply_damage_to_card(
                            state,
                            owner,
                            zone_name,
                            card_obj,
                            amount,
                            deathtouch=bool(assignment.get("source_deathtouch")),
                        )
                    )
                else:
                    continue
                if assignment.get("source_lifelink"):
                    controller = int(assignment.get("source_controller") or 0)
                    lifelink_gains[controller] = lifelink_gains.get(controller, 0) + amount

            for controller_id, amount in lifelink_gains.items():
                pl = _find_player(state, controller_id)
                if pl:
                    pl["life"] += int(amount)
                    events.append({"type": "lifelink", "player_id": controller_id, "amount": int(amount)})
                    events.extend(_enqueue_triggers(state, "life_gain", event_player_id=controller_id))

        combat_state["damage_resolved"] = True
        events.append({"type": "combat_damage_resolved"})
    elif action_type == "resolve_choice":
        choice_id = payload.get("choice_id")
        selections = payload.get("selections") or []
        choice = next((c for c in state.get("choices", []) if c.get("id") == choice_id), None)
        if not choice:
            return {"ok": False, "error": "choice_not_found", "events": events}
        if choice.get("player_id") != player_id:
            return {"ok": False, "error": "choice_not_owned", "events": events}
        state["choices"] = [c for c in state.get("choices", []) if c.get("id") != choice_id]
        kind = choice.get("kind")
        context = choice.get("context") or {}
        if kind == "discard":
            for card_id in selections:
                moved = _move_card(player, "hand", "graveyard", card_id)
                if moved:
                    events.append({"type": "discarded", "card": moved.get("name", "Card")})
        elif kind == "search_basic":
            destination = context.get("destination", "hand")
            tapped = bool(context.get("tapped"))
            for card_id in selections:
                moved = _move_card(player, "library", destination, card_id)
                if moved:
                    moved["tapped"] = tapped
                    events.append({"type": "searched", "card": moved.get("name", "Card")})
                    if destination == "battlefield":
                        events.extend(
                            _enqueue_triggers(
                                state,
                                "enter_battlefield",
                                event_player_id=player_id,
                                event_card=moved,
                            )
                        )
            random.shuffle(player["zones"]["library"])
        elif kind == "search_basic_split":
            destination = context.get("destination", "hand")
            tapped = bool(context.get("tapped"))
            for card_id in selections:
                moved = _move_card(player, "library", destination, card_id)
                if moved:
                    moved["tapped"] = tapped
                    events.append({"type": "searched", "card": moved.get("name", "Card")})
                    if destination == "battlefield":
                        events.extend(
                            _enqueue_triggers(
                                state,
                                "enter_battlefield",
                                event_player_id=player_id,
                                event_card=moved,
                            )
                        )
            remaining = max(0, int(context.get("remaining", 0)) - len(selections))
            steps = context.get("steps") or []
            up_to = bool(context.get("up_to"))
            if steps and remaining > 0 and selections:
                next_step = steps[0]
                remaining_steps = steps[1:]
                available = [
                    card
                    for card in player["zones"]["library"]
                    if "basic land" in (card.get("type_line") or "").lower()
                ]
                step_max = min(int(next_step.get("count", 1)), remaining, len(available))
                if step_max > 0:
                    min_count = 0 if up_to else step_max
                    _create_choice(
                        state,
                        player_id=player_id,
                        kind="search_basic_split",
                        prompt="Choose a basic land card.",
                        options=[
                            {"id": card["instance_id"], "label": card.get("name", "Card")}
                            for card in available
                        ],
                        min_count=min_count,
                        max_count=step_max,
                        context={
                            "stack_id": context.get("stack_id"),
                            "effect_index": context.get("effect_index"),
                            "steps": remaining_steps,
                            "remaining": remaining,
                            "up_to": up_to,
                            "destination": next_step.get("destination", "hand"),
                            "tapped": bool(next_step.get("tapped")),
                        },
                    )
                    return {"ok": True, "state": state, "events": events}
            random.shuffle(player["zones"]["library"])
        elif kind == "draw_count":
            count = int(selections[0]) if selections else int(choice.get("min", 0) or 0)
            for _ in range(max(0, count)):
                if not player["zones"]["library"]:
                    break
                player["zones"]["hand"].append(player["zones"]["library"].pop(0))
                events.extend(_enqueue_triggers(state, "draw", event_player_id=player_id))
        elif kind == "token_count":
            count = int(selections[0]) if selections else int(choice.get("min", 0) or 0)
            for _ in range(max(0, count)):
                token_card = _make_card_instance({"name": "Token", "type_line": "Token"}, player_id)
                player["zones"]["battlefield"].append(token_card)
                events.extend(
                    _enqueue_triggers(
                        state,
                        "enter_battlefield",
                        event_player_id=player_id,
                        event_card=token_card,
                    )
                )
        elif kind == "pay_cost":
            cost = context.get("cost") or {}
            remaining = context.get("remaining_costs") or []
            if cost.get("kind") == "sacrifice":
                for card_id in selections:
                    moved = _move_card_with_triggers(state, player, "battlefield", "graveyard", card_id)
                    if moved:
                        events.extend(
                            _enqueue_triggers(
                                state,
                                "sacrifice",
                                event_player_id=player_id,
                                event_card=moved,
                            )
                        )
            elif cost.get("kind") == "discard":
                for card_id in selections:
                    _move_card(player, "hand", "graveyard", card_id)
            elif cost.get("kind") == "exile_from_graveyard":
                for card_id in selections:
                    _move_card(player, "graveyard", "exile", card_id)
            remaining_costs = list(remaining)
            while remaining_costs:
                next_cost = remaining_costs[0]
                options = _collect_cost_options(state, player_id, next_cost)
                count = next_cost.get("count")
                if count is None:
                    if not options:
                        remaining_costs = remaining_costs[1:]
                        continue
                    min_count = 0
                    max_count = len(options)
                else:
                    required = int(count or 0)
                    if required <= 0:
                        remaining_costs = remaining_costs[1:]
                        continue
                    if len(options) < required:
                        return {"ok": False, "error": "cost_cannot_be_paid", "events": events}
                    min_count = required
                    max_count = required
                _create_choice(
                    state,
                    player_id=player_id,
                    kind="pay_cost",
                    prompt="Pay additional cost.",
                    options=options,
                    min_count=min_count,
                    max_count=max_count,
                    context={
                        "card_id": context.get("card_id"),
                        "cost": next_cost,
                        "remaining_costs": remaining_costs[1:],
                    },
                )
                return {"ok": True, "state": state, "events": events}
            card_id = context.get("card_id")
            if card_id:
                events.extend(_push_spell_to_stack(state, player, card_id))
        elif kind == "sacrifice_choice":
            target_player_id = int(choice.get("player_id"))
            target_player = _find_player(state, target_player_id)
            if target_player:
                for card_id in selections:
                    moved = _move_card_with_triggers(state, target_player, "battlefield", "graveyard", card_id)
                    if moved:
                        events.extend(
                            _enqueue_triggers(
                                state,
                                "sacrifice",
                                event_player_id=target_player_id,
                                event_card=moved,
                            )
                        )
            remaining_players = context.get("remaining_players") or []
            if remaining_players:
                next_player_id = remaining_players[0]
                rest = remaining_players[1:]
                target_type = context.get("target_type") or "permanent"
                count_value = context.get("count")
                count = int(count_value) if count_value is not None else 1
                options = _collect_cost_options(state, int(next_player_id), {"kind": "sacrifice", "target_type": target_type})
                if options:
                    required = min(count, len(options))
                    _create_choice(
                        state,
                        player_id=int(next_player_id),
                        kind="sacrifice_choice",
                        prompt="Choose permanent(s) to sacrifice.",
                        options=options,
                        min_count=required,
                        max_count=required,
                        context={
                            "stack_id": context.get("stack_id"),
                            "effect_index": context.get("effect_index"),
                            "target_type": target_type,
                            "remaining_players": rest,
                            "chain_kind": context.get("chain_kind"),
                            "count": required,
                        },
                    )
                    return {"ok": True, "state": state, "events": events}
            stack_id = context.get("stack_id")
            if stack_id:
                stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                if stack_item:
                    stack_item["pending_effect_index"] = int(context.get("effect_index", 0)) + 1
                    events.extend(_resolve_stack_top(state, player_id))
            return {"ok": True, "state": state, "events": events}
        elif kind == "target_draw_count":
            target_player_id = int(context.get("target_player_id") or player_id)
            target_player = _find_player(state, target_player_id)
            if target_player:
                count = int(selections[0]) if selections else int(choice.get("min", 0) or 0)
                actual_draws = 0
                for _ in range(max(0, count)):
                    if not target_player["zones"]["library"]:
                        break
                    target_player["zones"]["hand"].append(target_player["zones"]["library"].pop(0))
                    actual_draws += 1
                for _ in range(actual_draws):
                    events.extend(_enqueue_triggers(state, "draw", event_player_id=target_player_id))
            stack_id = context.get("stack_id")
            if stack_id:
                stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                if stack_item:
                    stack_item["pending_effect_index"] = int(context.get("effect_index", 0)) + 1
                    events.extend(_resolve_stack_top(state, player_id))
            return {"ok": True, "state": state, "events": events}
        elif kind == "target_discard_cards":
            target_player_id = int(context.get("target_player_id") or player_id)
            target_player = _find_player(state, target_player_id)
            if target_player:
                for card_id in selections:
                    _move_card(target_player, "hand", "graveyard", card_id)
            stack_id = context.get("stack_id")
            if stack_id:
                stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                if stack_item:
                    stack_item["pending_effect_index"] = int(context.get("effect_index", 0)) + 1
                    events.extend(_resolve_stack_top(state, player_id))
            return {"ok": True, "state": state, "events": events}
        elif kind == "effect_count":
            count = int(selections[0]) if selections else int(choice.get("min", 0) or 0)
            stack_id = context.get("stack_id")
            if stack_id:
                stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                if stack_item:
                    effect_index = int(context.get("effect_index", 0))
                    value_key = context.get("value_key") or "count"
                    effects = stack_item.get("effects") or []
                    if 0 <= effect_index < len(effects):
                        effects[effect_index][value_key] = count
                    stack_item["pending_effect_index"] = effect_index
                    events.extend(_resolve_stack_top(state, player_id))
            return {"ok": True, "state": state, "events": events}
        elif kind == "discard_choice":
            target_player_id = int(choice.get("player_id"))
            target_player = _find_player(state, target_player_id)
            if target_player:
                for card_id in selections:
                    _move_card(target_player, "hand", "graveyard", card_id)
            remaining_players = context.get("remaining_players") or []
            if remaining_players:
                count_value = context.get("count")
                count = int(count_value) if count_value is not None else 1
                next_player_id = None
                options = []
                rest = []
                while remaining_players:
                    next_player_id = remaining_players[0]
                    rest = remaining_players[1:]
                    next_player = _find_player(state, int(next_player_id))
                    remaining_players = rest
                    if not next_player:
                        continue
                    options = [
                        {"id": card["instance_id"], "label": card.get("name", "Card")}
                        for card in next_player["zones"]["hand"]
                    ]
                    if options:
                        break
                if options and next_player_id is not None:
                    required = min(count, len(options))
                    _create_choice(
                        state,
                        player_id=int(next_player_id),
                        kind="discard_choice",
                        prompt="Choose card(s) to discard.",
                        options=options,
                        min_count=required,
                        max_count=required,
                        context={
                            "stack_id": context.get("stack_id"),
                            "effect_index": context.get("effect_index"),
                            "remaining_players": rest,
                            "count": required,
                        },
                    )
                    return {"ok": True, "state": state, "events": events}
            stack_id = context.get("stack_id")
            if stack_id:
                stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                if stack_item:
                    stack_item["pending_effect_index"] = int(context.get("effect_index", 0)) + 1
                    events.extend(_resolve_stack_top(state, player_id))
            return {"ok": True, "state": state, "events": events}
        elif kind == "scry_count":
            count = int(selections[0]) if selections else int(choice.get("min", 0) or 0)
            if count <= 0 or not player["zones"]["library"]:
                stack_id = context.get("stack_id")
                if stack_id:
                    stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                    if stack_item:
                        stack_item["pending_effect_index"] = int(context.get("effect_index", 0)) + 1
                        events.extend(_resolve_stack_top(state, player_id))
                return {"ok": True, "state": state, "events": events}
            top_cards = player["zones"]["library"][: int(count)]
            options = [{"id": card["instance_id"], "label": card.get("name", "Card")} for card in top_cards]
            _create_choice(
                state,
                player_id=player_id,
                kind="scry_select",
                prompt="Choose any number of cards to put on the bottom.",
                options=options,
                min_count=0,
                max_count=len(options),
                context={
                    "stack_id": context.get("stack_id"),
                    "effect_index": context.get("effect_index"),
                    "top_ids": [card["instance_id"] for card in top_cards],
                },
            )
            return {"ok": True, "state": state, "events": events}
        elif kind == "scry_select":
            top_ids = context.get("top_ids") or []
            if top_ids:
                library = player["zones"]["library"]
                top_cards = [card for card in library if card.get("instance_id") in top_ids]
                remaining = [card for card in top_cards if card.get("instance_id") not in selections]
                bottom = [card for card in top_cards if card.get("instance_id") in selections]
                rest = [card for card in library if card.get("instance_id") not in top_ids]
                player["zones"]["library"] = remaining + rest + bottom
            stack_id = context.get("stack_id")
            if stack_id:
                stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                if stack_item:
                    stack_item["pending_effect_index"] = int(context.get("effect_index", 0)) + 1
                    events.extend(_resolve_stack_top(state, player_id))
            return {"ok": True, "state": state, "events": events}
        elif kind == "mill_count":
            count = int(selections[0]) if selections else int(choice.get("min", 0) or 0)
            if context.get("each_player"):
                for pl in state.get("players", []):
                    for _ in range(max(0, count)):
                        if not pl["zones"]["library"]:
                            break
                        moved = pl["zones"]["library"].pop(0)
                        pl["zones"]["graveyard"].append(moved)
                    events.append({"type": "mill", "count": int(count), "player_id": pl.get("user_id")})
            else:
                target_player_id = int(context.get("target_player_id") or player_id)
                target_player = _find_player(state, target_player_id)
                if target_player:
                    for _ in range(max(0, count)):
                        if not target_player["zones"]["library"]:
                            break
                        moved = target_player["zones"]["library"].pop(0)
                        target_player["zones"]["graveyard"].append(moved)
                    events.append({"type": "mill", "count": int(count), "player_id": target_player_id})
            stack_id = context.get("stack_id")
            if stack_id:
                stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                if stack_item:
                    stack_item["pending_effect_index"] = int(context.get("effect_index", 0)) + 1
                    events.extend(_resolve_stack_top(state, player_id))
            return {"ok": True, "state": state, "events": events}
        elif kind == "life_gain_count" or kind == "life_loss_count":
            count = int(selections[0]) if selections else int(choice.get("min", 0) or 0)
            effect_kind = context.get("effect_kind")
            if effect_kind in {"gain_life_each_opponent", "gain_life_each_player", "lose_life_each_opponent", "lose_life_each_player"}:
                for pl in state.get("players", []):
                    if "opponent" in effect_kind and pl.get("user_id") == player_id:
                        continue
                    if "gain" in effect_kind:
                        pl["life"] += count
                        events.extend(_enqueue_triggers(state, "life_gain", event_player_id=pl.get("user_id")))
                    else:
                        pl["life"] -= count
                        events.extend(_enqueue_triggers(state, "life_loss", event_player_id=pl.get("user_id")))
            stack_id = context.get("stack_id")
            if stack_id:
                stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                if stack_item:
                    stack_item["pending_effect_index"] = int(context.get("effect_index", 0)) + 1
                    events.extend(_resolve_stack_top(state, player_id))
            return {"ok": True, "state": state, "events": events}
        elif kind == "graveyard_target":
            target_id = selections[0] if selections else None
            destination = context.get("destination", "battlefield")
            if target_id:
                card_obj, moved_events = _resolve_graveyard_move(
                    state,
                    player_id,
                    target_id,
                    destination,
                    control=context.get("control"),
                    tapped=bool(context.get("tapped")),
                    set_controller=bool(context.get("set_controller", destination == "battlefield")),
                )
                events.extend(moved_events)
                event_type = context.get("event_type")
                if event_type and card_obj:
                    event_payload = {"type": event_type, "card": card_obj.get("name", "Card")}
                    if event_type == "reanimated":
                        event_payload["destination"] = destination
                    events.append(event_payload)
            stack_id = context.get("stack_id")
            if stack_id:
                stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                if stack_item:
                    stack_item["pending_effect_index"] = int(context.get("effect_index", 0)) + 1
                    events.extend(_resolve_stack_top(state, player_id))
            return {"ok": True, "state": state, "events": events}
        elif kind == "target":
            stack_id = context.get("stack_id")
            effect_index = int(context.get("effect_index", 0))
            targets = []
            for selection in selections:
                if isinstance(selection, str) and selection.startswith("player:"):
                    targets.append({"type": "player", "id": int(selection.split(":", 1)[1])})
                elif isinstance(selection, str) and selection.startswith("card:"):
                    targets.append({"type": "card", "id": selection.split(":", 1)[1]})
            if stack_id:
                stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
                if stack_item is not None:
                    stack_item.setdefault("targets", {})[effect_index] = targets
                    stack_item["pending_effect_index"] = effect_index
                    events.extend(_resolve_stack_top(state, player_id))
            return {"ok": True, "state": state, "events": events}
        elif kind == "bottom_cards":
            for card_id in selections:
                moved = _move_card(player, "hand", "library", card_id)
                if moved:
                    events.append({"type": "bottomed", "card": moved.get("name", "Card")})
            mulligan = _get_mulligan_state(state)
            pid_key = str(player_id)
            mulligan["kept"][pid_key] = True
            if all(mulligan["kept"].get(str(pid)) for pid in [p.get("user_id") for p in state.get("players", [])]):
                mulligan["status"] = "complete"
                state["status"] = "active"
                events.append({"type": "mulligan_complete"})
        stack_id = context.get("stack_id")
        if stack_id:
            stack_item = next((item for item in state.get("stack", []) if item.get("id") == stack_id), None)
            if stack_item:
                stack_item["pending_effect_index"] = int(context.get("effect_index", 0)) + 1
                events.extend(_resolve_stack_top(state, player_id))
    else:
        return {"ok": False, "error": "unsupported_action", "events": events}

    return {"ok": True, "state": state, "events": events}
