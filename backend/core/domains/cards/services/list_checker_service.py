"""Collection comparison orchestration for pasted list checker input."""

from __future__ import annotations

from core.domains.cards.services import list_checker_inventory_service as inventory_service
from core.domains.cards.services import list_checker_parsing_service as parsing_service
from core.domains.cards.services import list_checker_result_service as result_service
from core.domains.cards.services import list_checker_scryfall_service as scryfall_service
from core.domains.cards.services import scryfall_cache
from core.domains.decks.services import deck_utils
from shared.mtg import _normalize_name

BASIC_LAND_SLUGS = {_normalize_name(name) for name in deck_utils.BASIC_LANDS}


def compute_list_checker(pasted: str):
    """
    Compute folder availability and Scryfall metadata for pasted list checker text.

    Enhancements:
    - When the exact name doesn't hit, run a face-aware rescue pass that
      counts copies and picks a representative card using either face.
    - Scryfall id resolution also understands face names.
    """
    want = parsing_service.parse_card_list(pasted)
    if not want:
        return [], {"have_all": 0, "partial": 0, "missing": 0, "total_rows": 0}, "No card names found."

    keys = list(want.keys())
    display_by_nkey = {nkey: spec["display"] for nkey, spec in want.items()}
    snapshot = inventory_service.build_inventory_snapshot(want, display_by_nkey)

    owner_label_map = result_service.load_owner_label_map(snapshot.owner_user_ids)
    formatter = result_service.ListCheckerBreakdownFormatter(
        current_user_id=snapshot.current_user_id,
        friend_ids=snapshot.friend_ids,
        collection_id_set=snapshot.collection_id_set,
        folder_meta=snapshot.folder_meta,
        owner_label_map=owner_label_map,
    )
    for normalized_name in keys:
        if normalized_name in BASIC_LAND_SLUGS:
            snapshot.available_count[normalized_name] = max(snapshot.available_count[normalized_name], 9999)

    name_to_sid, face_to_sid, name_to_meta, face_to_meta = scryfall_service.build_scryfall_lookup_maps()

    folder_ids = set()
    for breakdown in (
        snapshot.per_folder_counts,
        snapshot.collection_counts,
        snapshot.deck_counts,
        snapshot.available_per_folder_counts,
    ):
        for counts in breakdown.values():
            folder_ids.update(folder_id for folder_id in counts if folder_id is not None)
    result_service.load_missing_folder_metadata(snapshot.folder_meta, owner_label_map, folder_ids)

    results, summary = result_service.build_results(
        want=want,
        basic_land_slugs=BASIC_LAND_SLUGS,
        per_folder_counts=snapshot.per_folder_counts,
        collection_counts=snapshot.collection_counts,
        deck_counts=snapshot.deck_counts,
        available_per_folder_counts=snapshot.available_per_folder_counts,
        available_count=snapshot.available_count,
        rep_card_map=snapshot.rep_card_map,
        name_to_sid=name_to_sid,
        face_to_sid=face_to_sid,
        name_to_meta=name_to_meta,
        face_to_meta=face_to_meta,
        formatter=formatter,
    )
    return results, summary, None


_parse_card_list = parsing_service.parse_card_list
find_card_by_name_or_face = parsing_service.find_card_by_name_or_face


__all__ = [
    "BASIC_LAND_SLUGS",
    "compute_list_checker",
    "find_card_by_name_or_face",
]
