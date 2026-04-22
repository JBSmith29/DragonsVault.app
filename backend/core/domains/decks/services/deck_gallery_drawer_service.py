"""Deck gallery drawer payload builders."""

from __future__ import annotations

from typing import Any


def _oracle_text_from_faces(faces_json) -> str | None:
    if not faces_json:
        return None
    if isinstance(faces_json, dict):
        faces = faces_json.get("faces") or []
    else:
        faces = faces_json
    parts = [face.get("oracle_text") for face in faces if isinstance(face, dict) and face.get("oracle_text")]
    if not parts:
        return None
    return " // ".join(parts)


def _type_line_from_print(print_payload: dict | None) -> str:
    if not isinstance(print_payload, dict):
        return ""
    direct = str(print_payload.get("type_line") or "").strip()
    if direct:
        return direct
    faces = print_payload.get("card_faces")
    if not isinstance(faces, list):
        return ""
    parts = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        value = str(face.get("type_line") or "").strip()
        if value:
            parts.append(value)
    if not parts:
        return ""
    return " // ".join(parts)


def build_deck_drawer_summary(folder, *, hooks: Any) -> dict:
    hooks._ensure_cache_ready()

    cards = (
        hooks.db.session.query(
            hooks.Card.id,
            hooks.Card.name,
            hooks.Card.set_code,
            hooks.Card.collector_number,
            hooks.Card.oracle_id,
            hooks.Card.lang,
            hooks.Card.is_foil,
            hooks.Card.quantity,
            hooks.Card.type_line,
            hooks.Card.oracle_text,
            hooks.Card.mana_value,
            hooks.Card.faces_json,
        )
        .filter(hooks.Card.folder_id == folder.id)
        .all()
    )

    base_types = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]
    type_counts = {card_type: 0 for card_type in base_types}

    def _mana_cost_from_faces(faces_json) -> str | None:
        if not faces_json:
            return None
        if isinstance(faces_json, dict):
            faces = faces_json.get("faces") or []
        else:
            faces = faces_json
        costs = [face.get("mana_cost") for face in faces if isinstance(face, dict) and face.get("mana_cost")]
        if not costs:
            return None
        return " // ".join(costs) if len(costs) > 1 else costs[0]

    bracket_cards: list[dict[str, object]] = []
    total_cards = 0

    for _card_id, name, set_code, collector_number, oracle_id, _lang, _is_foil, qty, type_line, oracle_text, mana_value, faces_json in cards:
        qty = int(qty or 0) or 1
        total_cards += qty
        resolved_type_line = (type_line or "").strip()
        resolved_oracle_text = (oracle_text or "").strip()
        if not resolved_oracle_text:
            resolved_oracle_text = _oracle_text_from_faces(faces_json) or ""
        resolved_mana_value = mana_value
        print_payload = None
        if not resolved_type_line or not resolved_oracle_text or resolved_mana_value is None:
            try:
                print_payload = hooks._lookup_print_data(set_code, collector_number, name, oracle_id)
            except Exception:
                print_payload = None
        if not resolved_type_line:
            resolved_type_line = _type_line_from_print(print_payload)
        if not resolved_oracle_text:
            resolved_oracle_text = (
                (print_payload or {}).get("oracle_text")
                or _oracle_text_from_faces((print_payload or {}).get("card_faces"))
                or ""
            )
        if resolved_mana_value is None:
            resolved_mana_value = (print_payload or {}).get("cmc")

        if resolved_type_line:
            for card_type in base_types:
                if card_type in resolved_type_line:
                    type_counts[card_type] += qty

        mana_cost = _mana_cost_from_faces(faces_json)
        if not mana_cost:
            mana_cost = (print_payload or {}).get("mana_cost")

        bracket_cards.append(
            {
                "name": name,
                "type_line": resolved_type_line or "",
                "oracle_text": resolved_oracle_text or "",
                "mana_value": resolved_mana_value,
                "quantity": qty,
                "mana_cost": mana_cost,
                "produced_mana": None,
            }
        )

    type_breakdown = [(card_type, type_counts[card_type]) for card_type in base_types if type_counts[card_type] > 0]
    mana_pip_dist = hooks.deck_mana_pip_dist(folder.id, mode="drawer")
    land_mana_sources = [
        {"color": color, "icon": icon, "label": color, "count": count}
        for color, icon, count in hooks.deck_land_mana_sources(folder.id, filter_by_identity=False)
    ]
    curve_rows = hooks.deck_curve_rows(folder.id, mode="drawer")

    placeholder_thumb = hooks.static_url("img/card-placeholder.svg")
    commander_payload = None
    if folder.commander_oracle_id or folder.commander_name:
        pr = None
        try:
            target_oracle_id = hooks.primary_commander_oracle_id(folder.commander_oracle_id)
            if not target_oracle_id and folder.commander_name:
                lookup_name = hooks.primary_commander_name(folder.commander_name) or folder.commander_name
                target_oracle_id = hooks.unique_oracle_by_name(lookup_name)
            if target_oracle_id:
                prints = hooks.prints_for_oracle(target_oracle_id) or []
                pr = prints[0] if prints else None
        except Exception:
            pr = None
        if pr:
            image_pack = hooks._image_pack_from_print(pr)
            commander_payload = {
                "name": folder.commander_name or pr.get("name"),
                "image": image_pack.get("small") or image_pack.get("normal"),
                "hover": image_pack.get("large") or image_pack.get("normal") or image_pack.get("small"),
                "scryfall": pr.get("scryfall_uri"),
            }
        else:
            commander_payload = {"name": folder.commander_name}

    if commander_payload:
        commander_payload.setdefault("image", placeholder_thumb)
        commander_payload.setdefault("hover", placeholder_thumb)

    commander_stub = {
        "oracle_id": hooks.primary_commander_oracle_id(folder.commander_oracle_id),
        "name": hooks.primary_commander_name(folder.commander_name) or folder.commander_name,
    }
    epoch = hooks.cache_epoch() + hooks.BRACKET_RULESET_EPOCH + hooks.spellbook_dataset_epoch()
    signature = hooks.compute_bracket_signature(bracket_cards, commander_stub, epoch=epoch)
    commander_ctx = None
    if folder.id:
        commander_ctx = hooks.get_cached_bracket(folder.id, signature, epoch)
    if not commander_ctx:
        commander_ctx = hooks.evaluate_commander_bracket(bracket_cards, commander_stub)
        if folder.id:
            hooks.store_cached_bracket(folder.id, signature, epoch, commander_ctx)

    spellbook_details = commander_ctx.get("spellbook_details") or []
    if len(spellbook_details) > 8:
        spellbook_details = spellbook_details[:8]

    deck_color_letters, _deck_color_label = hooks.compute_folder_color_identity(folder.id, "20260311a")
    deck_color_list = list(deck_color_letters) if deck_color_letters else []

    deck_tag_label = None
    if folder.deck_tag:
        for category, tags in hooks.get_deck_tag_groups().items():
            if folder.deck_tag in tags:
                deck_tag_label = f"{category}: {folder.deck_tag}"
                break
        if not deck_tag_label:
            deck_tag_label = folder.deck_tag

    return {
        "deck": {
            "id": folder.id,
            "name": folder.name,
            "tag": folder.deck_tag,
            "tag_label": deck_tag_label,
            "tag_category": hooks.get_deck_tag_category(folder.deck_tag),
        },
        "commander": commander_payload,
        "bracket": {
            "level": commander_ctx.get("level"),
            "label": commander_ctx.get("label"),
            "score": commander_ctx.get("score"),
            "summary_points": commander_ctx.get("summary_points") or [],
            "spellbook_combos": spellbook_details,
        },
        "type_breakdown": type_breakdown,
        "mana_pip_dist": mana_pip_dist,
        "land_mana_sources": land_mana_sources,
        "curve_rows": curve_rows,
        "total_cards": total_cards,
        "deck_colors": deck_color_list,
    }


__all__ = ["build_deck_drawer_summary"]
