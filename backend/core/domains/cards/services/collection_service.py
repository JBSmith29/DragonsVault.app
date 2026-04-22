"""Collection card-browser route wrapper."""

from __future__ import annotations

from flask import flash, redirect, render_template, url_for

from core.domains.cards.services.collection_facets_service import (
    card_browser_facets,
    collection_rarity_options,
    set_options_with_names,
)
from core.domains.cards.services.collection_query_service import (
    build_collection_browser_context,
)
from core.domains.cards.services.collection_request_service import (
    parse_collection_browser_request,
)
from shared.mtg import _move_folder_choices
from shared.validation import ValidationError, log_validation_error


def list_cards():
    try:
        params = parse_collection_browser_request()
    except ValidationError as exc:
        log_validation_error(exc, context="list_cards")
        flash("Invalid folder selection.", "warning")
        return redirect(url_for("views.list_cards"))

    page_context = build_collection_browser_context(params)
    sets, langs, _folders = card_browser_facets()
    set_options = set_options_with_names(sets)
    rarity_options = collection_rarity_options()
    move_folder_options = _move_folder_choices()
    return render_template(
        "cards/cards.html",
        **page_context,
        sets=sets,
        langs=langs,
        set_options=set_options,
        rarity_options=rarity_options,
        move_folder_options=move_folder_options,
    )


__all__ = ["list_cards"]
