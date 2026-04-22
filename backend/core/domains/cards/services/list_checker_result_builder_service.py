"""Row assembly for list checker output."""

from __future__ import annotations

from core.domains.cards.services import list_checker_scryfall_service as scryfall_service
from core.domains.cards.services import scryfall_cache
from shared.mtg import color_identity_name


def _resolve_status(
    *,
    requested: int,
    total_owned: int,
    available: int,
    available_user: int,
    available_friends: int,
    is_basic_land: bool,
) -> tuple[str, int, int, int]:
    missing_qty = max(0, requested - available)
    if is_basic_land:
        available = max(available, requested)
        total_owned = max(total_owned, requested)
        return "have_all", available, total_owned, 0
    if available_user >= requested:
        return "have_all", available, total_owned, missing_qty
    if available_friends > 0:
        return "friends", available, total_owned, missing_qty
    if available_user > 0:
        return "partial", available, total_owned, missing_qty
    return "missing", available, total_owned, missing_qty


def _build_friend_targets(normalized_name, available_per_folder_counts, formatter):
    friend_targets_map = {}
    if not formatter.current_user_id or not formatter.friend_ids:
        return []

    for folder_id, count in available_per_folder_counts[normalized_name].items():
        meta = formatter.folder_meta.get(folder_id) or {}
        owner_id = meta.get("owner_user_id")
        if owner_id not in formatter.friend_ids:
            continue
        owner_label = formatter.owner_label_map.get(owner_id) or (meta.get("owner") or "").strip() or "Friend"
        entry = friend_targets_map.setdefault(
            owner_id,
            {"user_id": owner_id, "label": owner_label, "qty": 0, "folders": []},
        )
        entry["qty"] += count
        entry["folders"].append({"name": formatter.label_for_folder(folder_id), "qty": count})
    return sorted(
        friend_targets_map.values(),
        key=lambda entry: (-entry["qty"], (entry["label"] or "").lower()),
    )


def _resolve_card_metadata(
    *,
    normalized_name,
    rep_card_map,
    name_to_sid,
    face_to_sid,
    name_to_meta,
    face_to_meta,
):
    rep_card = rep_card_map.get(normalized_name)
    rep_card_id = int(rep_card.id) if rep_card else None
    oracle_id = getattr(rep_card, "oracle_id", None)
    rarity = scryfall_service.normalize_rarity(getattr(rep_card, "rarity", None)) if rep_card else ""
    type_label = scryfall_service.normalize_type(getattr(rep_card, "type_line", None)) if rep_card else ""
    ci_letters = ""
    ci_known = False
    if rep_card:
        raw_ci = getattr(rep_card, "color_identity", None)
        if raw_ci not in (None, ""):
            ci_letters, _ = scryfall_cache.normalize_color_identity(raw_ci)
            ci_known = True

    scry_id = None
    sid_tuple = name_to_sid.get(normalized_name) or face_to_sid.get(normalized_name)
    if sid_tuple:
        scry_id = sid_tuple[0]
        oracle_id = oracle_id or sid_tuple[2]

    meta = name_to_meta.get(normalized_name) or face_to_meta.get(normalized_name) or {}
    if not rarity:
        rarity = meta.get("rarity") or ""
    if not type_label:
        type_label = meta.get("type") or ""
    if not ci_known and "color_identity" in meta:
        ci_letters = meta.get("color_identity") or ""
        ci_known = True

    if rep_card and not oracle_id:
        oracle_id = rep_card.oracle_id

    return {
        "card_id": rep_card_id,
        "scry_id": scry_id,
        "oracle_id": oracle_id,
        "color_identity": ci_letters if ci_known else "",
        "color_identity_label": color_identity_name(ci_letters) if ci_known else "—",
        "rarity": rarity or "—",
        "type": type_label or "—",
    }


def build_results(
    *,
    want,
    basic_land_slugs: set[str],
    per_folder_counts,
    collection_counts,
    deck_counts,
    available_per_folder_counts,
    available_count,
    rep_card_map,
    name_to_sid,
    face_to_sid,
    name_to_meta,
    face_to_meta,
    formatter,
):
    results = []
    have_all = partial = missing = friends = 0

    for normalized_name, spec in want.items():
        requested = spec["qty"]
        display = spec["display"]

        is_basic_land = normalized_name in basic_land_slugs
        total_owned = sum(per_folder_counts[normalized_name].values())
        available = available_count[normalized_name]
        available_user = 0
        available_friends = 0

        if formatter.current_user_id:
            for folder_id, count in available_per_folder_counts[normalized_name].items():
                meta = formatter.folder_meta.get(folder_id) or {}
                owner_id = meta.get("owner_user_id")
                if owner_id == formatter.current_user_id:
                    available_user += count
                elif owner_id in formatter.friend_ids:
                    available_friends += count
        else:
            available_user = available

        status, available, total_owned, missing_qty = _resolve_status(
            requested=requested,
            total_owned=total_owned,
            available=available,
            available_user=available_user,
            available_friends=available_friends,
            is_basic_land=is_basic_land,
        )
        if status == "have_all":
            have_all += 1
        elif status == "friends":
            friends += 1
        elif status == "partial":
            partial += 1
        else:
            missing += 1

        collection_breakdown = formatter.format_breakdown(collection_counts[normalized_name])
        deck_breakdown = formatter.format_breakdown(deck_counts[normalized_name])
        available_breakdown = formatter.format_breakdown(available_per_folder_counts[normalized_name])
        available_detail = formatter.format_breakdown_detail(available_per_folder_counts[normalized_name])
        if formatter.current_user_id:
            available_user_breakdown = formatter.format_breakdown(
                formatter.filter_breakdown_by_owner(
                    available_per_folder_counts[normalized_name],
                    {formatter.current_user_id},
                )
            )
            available_friend_breakdown = formatter.format_breakdown(
                formatter.filter_breakdown_by_owner(
                    available_per_folder_counts[normalized_name],
                    formatter.friend_ids,
                )
            )
        else:
            available_user_breakdown = available_breakdown
            available_friend_breakdown = []

        friend_targets = _build_friend_targets(normalized_name, available_per_folder_counts, formatter)
        card_metadata = _resolve_card_metadata(
            normalized_name=normalized_name,
            rep_card_map=rep_card_map,
            name_to_sid=name_to_sid,
            face_to_sid=face_to_sid,
            name_to_meta=name_to_meta,
            face_to_meta=face_to_meta,
        )

        results.append(
            {
                "name": display,
                "requested": requested,
                "available_in_collection": available,
                "missing_qty": missing_qty,
                "status": status,
                "folders": collection_breakdown,
                "collection_folders": collection_breakdown,
                "deck_folders": deck_breakdown,
                "available_folders": available_breakdown,
                "available_folders_detail": available_detail,
                "available_user_folders": available_user_breakdown,
                "available_friend_folders": available_friend_breakdown,
                "available_user": available_user,
                "available_friends": available_friends,
                "friend_targets": friend_targets,
                "total_owned": total_owned,
                **card_metadata,
            }
        )

    results.sort(
        key=lambda rec: {"missing": 0, "friends": 1, "partial": 2, "have_all": 3}.get(rec["status"], 4)
    )
    summary = {
        "have_all": have_all,
        "friends": friends,
        "partial": partial,
        "missing": missing,
        "total_rows": len(results),
    }
    return results, summary


__all__ = ["build_results"]
