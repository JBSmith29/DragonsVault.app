"""Page-context and drawer-summary assembly for build sessions."""

from __future__ import annotations

from models import BuildSession


def build_session_page_context(
    session: BuildSession,
    *,
    user_id: int | None,
    sort_mode: str,
    build_view: str,
    rec_source: str,
    edhrec_job_id: str | None,
) -> dict:
    from core.domains.decks.services import build_session_service as legacy

    tags = legacy._normalized_tags(session.tags_json)
    commander = legacy._oracle_payload(session.commander_oracle_id, fallback=session.commander_name)
    cards = legacy._session_cards(session.cards or [])
    build_oracle_ids = legacy._build_oracle_ids(session.cards or [])
    cards_by_type = legacy._group_session_cards_by_type(cards)
    metrics = legacy._deck_metrics(session.cards or [])
    deck_type_breakdown = legacy._type_breakdown_for_entries(session.cards or [])
    deck_type_distribution = legacy._distribution_breakdown_for_entries(session.cards or [])
    edhrec_type_distribution = legacy._edhrec_type_breakdown(
        session.commander_oracle_id or "",
        tags,
    )
    build_bracket = legacy._build_session_bracket_context(session, session.cards or [])
    normalized_rec_source = (rec_source or "edhrec").strip().lower()
    if normalized_rec_source not in {"edhrec", "collection"}:
        normalized_rec_source = "edhrec"
    tag_groups = legacy.get_deck_tag_groups()
    recommendations = legacy.build_recommendation_sections(
        session.commander_oracle_id or "",
        tags,
        role_needs=metrics["role_needs"],
        sort_mode=sort_mode,
    )
    collection_oracles = legacy._collection_oracle_ids(user_id)
    collection_name_keys = legacy._collection_name_keys(user_id)
    legacy._mark_collection_cards(recommendations, collection_oracles, collection_name_keys)
    edhrec_oracles = legacy._recommendation_oracle_ids(recommendations)
    collection_sections = legacy._collection_recommendation_sections(
        session.commander_oracle_id or "",
        tags,
        collection_oracles,
        metrics["role_needs"],
        exclude_oracles=edhrec_oracles,
        sort_mode=sort_mode,
    )
    legacy._mark_build_cards(recommendations, build_oracle_ids)
    legacy._mark_build_cards(collection_sections, build_oracle_ids)

    return {
        "build_session": session,
        "commander": commander,
        "tags": tags,
        "tag_groups": tag_groups,
        "recommendations": recommendations,
        "collection_sections": collection_sections,
        "deck_metrics": metrics,
        "deck_type_breakdown": deck_type_breakdown,
        "deck_type_distribution": deck_type_distribution,
        "edhrec_type_distribution": edhrec_type_distribution,
        "mana_pip_dist": metrics["mana_pip_dist"],
        "land_mana_sources": metrics["land_mana_sources"],
        "sort_mode": sort_mode,
        "rec_source": normalized_rec_source,
        "build_view": build_view,
        "edhrec_estimate_seconds": legacy._edhrec_estimate_seconds(tags),
        "edhrec_job_id": edhrec_job_id,
        "phase": metrics["phase"],
        "session_cards": cards,
        "session_cards_by_type": cards_by_type,
        "build_bracket": build_bracket,
    }


def build_session_drawer_summary(session: BuildSession) -> dict:
    from core.domains.decks.services import build_session_service as legacy

    tags = legacy._normalized_tags(session.tags_json)
    tag_label = None
    if tags:
        category = legacy.get_deck_tag_category(tags[0])
        tag_label = f"{category}: {tags[0]}" if category else tags[0]

    deck_name = session.build_name or session.commander_name or "Build"
    commander_payload = legacy._commander_drawer_payload(
        session.commander_oracle_id,
        session.commander_name,
    )

    entries = session.cards or []
    metrics = legacy._deck_metrics(entries)
    type_breakdown = [
        (label, count)
        for label, count in legacy._type_breakdown_for_entries(entries)
        if count > 0
    ]
    mana_pip_dist = [
        {"color": color, "icon": icon, "count": count}
        for color, icon, count in metrics.get("mana_pip_dist") or []
    ]
    land_mana_sources = [
        {"color": color, "icon": icon, "label": color, "count": count}
        for color, icon, count in metrics.get("land_mana_sources") or []
    ]
    curve_rows = legacy._curve_rows_for_entries(entries)
    deck_colors = sorted(legacy._color_identity_set(session.commander_oracle_id or ""))
    commander_ctx = legacy._build_session_bracket_context(session, entries)

    return {
        "deck": {
            "id": session.id,
            "name": deck_name,
            "tag": tags[0] if tags else None,
            "tag_label": tag_label,
        },
        "commander": commander_payload,
        "bracket": commander_ctx,
        "type_breakdown": type_breakdown,
        "mana_pip_dist": mana_pip_dist,
        "land_mana_sources": land_mana_sources,
        "curve_rows": curve_rows,
        "missing_cmc": metrics.get("missing_cmc") or 0,
        "total_cards": metrics.get("total_cards") or 0,
        "deck_colors": deck_colors,
    }
