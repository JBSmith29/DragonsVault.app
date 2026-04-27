"""Lookup builders for opening-hand deck previews and token lists."""

from __future__ import annotations

import json
from typing import Iterable

from flask_login import current_user

from extensions import db
from models import BuildSession, BuildSessionCard, Card
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    find_by_set_cn,
    prints_for_oracle,
)
from core.domains.decks.services.opening_hand_deck_source_service import (
    _opening_hand_build_key,
    _opening_hand_deck_key,
    _parse_opening_hand_deck_ref,
)
from core.domains.decks.services.opening_hand_payload_service import (
    _back_image_from_print,
    _ensure_cache_ready,
    _image_from_print,
    _opening_hand_token_key,
    _pick_nondigital_print,
    _token_payload,
)
from core.domains.decks.viewmodels.opening_hand_vm import OpeningHandCardVM
from core.shared.utils.assets import static_url
from shared.mtg import (
    _card_type_flags,
    _lookup_print_data,
    _oracle_text_from_faces,
    _token_stubs_from_oracle_text,
    _type_line_from_print,
)
from shared.validation import ValidationError


def _opening_hand_lookups(deck_refs: Iterable[str]) -> tuple[str, str]:
    deck_card_lookup: dict[str, list[dict]] = {}
    deck_token_lookup: dict[str, list[dict]] = {}
    normalized_refs: list[str] = []
    folder_ids: list[int] = []
    build_ids: list[int] = []
    for raw in [str(deck_ref).strip() for deck_ref in (deck_refs or []) if deck_ref]:
        try:
            parsed = _parse_opening_hand_deck_ref(raw)
        except ValidationError:
            continue
        if not parsed:
            continue
        source, deck_id = parsed
        deck_key = _opening_hand_deck_key(source, deck_id)
        if deck_key not in normalized_refs:
            normalized_refs.append(deck_key)
        if source == "build":
            build_ids.append(deck_id)
        else:
            folder_ids.append(deck_id)

    if normalized_refs:
        have_cache = _ensure_cache_ready()
        token_cache: dict[str, list[dict]] = {}
        placeholder = static_url("img/card-placeholder.svg")

        if folder_ids:
            card_rows = (
                Card.query.with_entities(
                    Card.folder_id,
                    Card.id,
                    Card.name,
                    Card.set_code,
                    Card.collector_number,
                    Card.lang,
                    Card.is_foil,
                    Card.oracle_id,
                    Card.type_line,
                    Card.mana_value,
                    Card.oracle_text,
                    Card.faces_json,
                )
                .filter(Card.folder_id.in_(folder_ids))
                .order_by(Card.folder_id.asc(), Card.name.asc(), Card.collector_number.asc())
                .all()
            )
            seen_cards: dict[str, set[str]] = {}
            seen_tokens: dict[str, set[str]] = {}
            for (
                folder_id,
                card_id,
                card_name,
                set_code,
                collector_number,
                lang,
                is_foil,
                oracle_id,
                type_line,
                mana_value,
                oracle_text,
                faces_json,
            ) in card_rows:
                if not card_name:
                    continue
                folder_key = str(folder_id)
                entries = deck_card_lookup.setdefault(folder_key, [])
                seen = seen_cards.setdefault(folder_key, set())
                value_token = f"{card_id or 0}:{set_code}:{collector_number}:{lang or 'en'}:{1 if is_foil else 0}"
                if value_token in seen:
                    continue
                seen.add(value_token)

                try:
                    pr = _lookup_print_data(set_code, collector_number, card_name, oracle_id)
                except Exception:
                    pr = None
                if not pr and oracle_id:
                    try:
                        pr = _pick_nondigital_print(prints_for_oracle(oracle_id) or [])
                    except Exception:
                        pr = None
                if not pr:
                    try:
                        pr = find_by_set_cn(set_code, collector_number, card_name)
                    except Exception:
                        pr = None

                imgs = _image_from_print(pr)
                back_imgs = _back_image_from_print(pr)
                resolved_type_line = (type_line or "").strip() or _type_line_from_print(pr)
                resolved_oracle_text = (
                    (oracle_text or "").strip()
                    or _oracle_text_from_faces(faces_json)
                    or (pr or {}).get("oracle_text")
                    or _oracle_text_from_faces((pr or {}).get("card_faces"))
                    or ""
                )
                flags = _card_type_flags(resolved_type_line)
                entries.append(
                    OpeningHandCardVM(
                        value=value_token,
                        name=card_name,
                        image=imgs.get("normal") or imgs.get("large") or imgs.get("small") or placeholder,
                        hover=imgs.get("large") or imgs.get("normal") or imgs.get("small") or placeholder,
                        back_image=back_imgs.get("normal") or back_imgs.get("large") or back_imgs.get("small"),
                        back_hover=back_imgs.get("large") or back_imgs.get("normal") or back_imgs.get("small"),
                        type_line=resolved_type_line,
                        oracle_text=resolved_oracle_text,
                        mana_value=mana_value if mana_value is not None else (pr or {}).get("cmc"),
                        mana_cost=(pr or {}).get("mana_cost") or "",
                        is_creature=bool(flags["is_creature"]),
                        is_land=bool(flags["is_land"]),
                        is_instant=bool(flags["is_instant"]),
                        is_sorcery=bool(flags["is_sorcery"]),
                        is_permanent=bool(flags["is_permanent"]),
                        zone_hint=str(flags["zone_hint"]),
                    ).to_payload()
                )

                tokens: list[dict] = []
                if have_cache and oracle_id:
                    cached_tokens = token_cache.get(oracle_id)
                    if cached_tokens is None:
                        try:
                            cached_tokens = sc.tokens_from_oracle(oracle_id) or []
                        except Exception:
                            cached_tokens = []
                        token_cache[oracle_id] = cached_tokens
                    tokens = cached_tokens
                if not tokens:
                    tokens = _token_stubs_from_oracle_text(resolved_oracle_text)
                if tokens:
                    bucket = deck_token_lookup.setdefault(folder_key, [])
                    bucket_seen = seen_tokens.setdefault(folder_key, set())
                    for token in tokens:
                        token_key = _opening_hand_token_key(token)
                        if token_key in bucket_seen:
                            continue
                        bucket_seen.add(token_key)
                        bucket.append(_token_payload(token, placeholder))

        if build_ids:
            build_rows = (
                db.session.query(BuildSessionCard.session_id, BuildSessionCard.card_oracle_id)
                .join(BuildSession, BuildSessionCard.session_id == BuildSession.id)
                .filter(
                    BuildSessionCard.session_id.in_(build_ids),
                    BuildSession.owner_user_id == current_user.id,
                    BuildSession.status == "active",
                )
                .all()
            )
            seen_cards: dict[str, set[str]] = {}
            seen_tokens: dict[str, set[str]] = {}
            for session_id, oracle_id in build_rows:
                oracle_id = (oracle_id or "").strip()
                if not oracle_id:
                    continue
                session_key = _opening_hand_build_key(session_id)
                entries = deck_card_lookup.setdefault(session_key, [])
                seen = seen_cards.setdefault(session_key, set())
                if oracle_id in seen:
                    continue
                seen.add(oracle_id)

                try:
                    pr = _pick_nondigital_print(prints_for_oracle(oracle_id) or [])
                except Exception:
                    pr = None
                imgs = _image_from_print(pr)
                back_imgs = _back_image_from_print(pr)
                type_line = _type_line_from_print(pr)
                oracle_text = (pr or {}).get("oracle_text") or _oracle_text_from_faces((pr or {}).get("card_faces"))
                flags = _card_type_flags(type_line)
                entries.append(
                    OpeningHandCardVM(
                        value=oracle_id,
                        name=(pr or {}).get("name") or oracle_id or "Card",
                        image=imgs.get("normal") or imgs.get("large") or imgs.get("small") or placeholder,
                        hover=imgs.get("large") or imgs.get("normal") or imgs.get("small") or placeholder,
                        back_image=back_imgs.get("normal") or back_imgs.get("large") or back_imgs.get("small"),
                        back_hover=back_imgs.get("large") or back_imgs.get("normal") or back_imgs.get("small"),
                        type_line=type_line,
                        oracle_text=oracle_text or "",
                        mana_value=(pr or {}).get("cmc"),
                        mana_cost=(pr or {}).get("mana_cost") or "",
                        is_creature=bool(flags["is_creature"]),
                        is_land=bool(flags["is_land"]),
                        is_instant=bool(flags["is_instant"]),
                        is_sorcery=bool(flags["is_sorcery"]),
                        is_permanent=bool(flags["is_permanent"]),
                        zone_hint=str(flags["zone_hint"]),
                    ).to_payload()
                )

                tokens: list[dict] = []
                if have_cache:
                    cached_tokens = token_cache.get(oracle_id)
                    if cached_tokens is None:
                        try:
                            cached_tokens = sc.tokens_from_oracle(oracle_id) or []
                        except Exception:
                            cached_tokens = []
                        token_cache[oracle_id] = cached_tokens
                    tokens = cached_tokens
                if not tokens:
                    tokens = _token_stubs_from_oracle_text(oracle_text)
                if tokens:
                    bucket = deck_token_lookup.setdefault(session_key, [])
                    bucket_seen = seen_tokens.setdefault(session_key, set())
                    for token in tokens:
                        token_key = _opening_hand_token_key(token)
                        if token_key in bucket_seen:
                            continue
                        bucket_seen.add(token_key)
                        bucket.append(_token_payload(token, placeholder))

        for entries in deck_card_lookup.values():
            entries.sort(key=lambda item: (item.get("name") or "").lower())
        for tokens in deck_token_lookup.values():
            tokens.sort(key=lambda item: (item.get("name") or "").lower())

    for deck_key in normalized_refs:
        deck_card_lookup.setdefault(deck_key, [])
        deck_token_lookup.setdefault(deck_key, [])
    return (
        json.dumps(deck_card_lookup, ensure_ascii=True),
        json.dumps(deck_token_lookup, ensure_ascii=True),
    )


__all__ = ["_opening_hand_lookups"]
