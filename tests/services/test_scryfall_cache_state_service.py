import json


def test_clear_in_memory_prints_resets_state_and_calls_hooks():
    from core.domains.cards.services import scryfall_cache_state_service as state_service

    calls = []
    state = {
        "_cache": [{"name": "Card A"}],
        "_by_set_cn": {"set::1": {"name": "Card A"}},
        "_by_oracle": {"oid-a": [{"name": "Card A"}]},
        "_set_names": {"set": "Set Name"},
        "_set_releases": {"set": "2020-01-01"},
        "_idx_by_set_num": {("set", 1): [{"name": "Card A"}]},
        "_idx_by_name": {"card-a": [{"name": "Card A"}]},
        "_idx_by_front": {"card-a": [{"name": "Card A"}]},
        "_idx_by_back": {"card-b": [{"name": "Card B"}]},
    }

    state_service.clear_in_memory_prints(
        state,
        clear_cached_set_profiles_fn=lambda: calls.append("profiles"),
        bump_cache_epoch_fn=lambda: calls.append("epoch"),
        cache_clearers=[lambda: calls.append("prints"), lambda: calls.append("oracle")],
    )

    assert state["_cache"] == []
    assert state["_by_set_cn"] == {}
    assert state["_by_oracle"] == {}
    assert state["_set_names"] is None
    assert state["_set_releases"] is None
    assert state["_idx_by_set_num"] == {}
    assert state["_idx_by_name"] == {}
    assert state["_idx_by_front"] == {}
    assert state["_idx_by_back"] == {}
    assert calls == ["profiles", "epoch", "prints", "oracle"]


def test_load_default_cache_reads_file_and_primes_indexes(tmp_path):
    from core.domains.cards.services import scryfall_cache_state_service as state_service

    cache_path = tmp_path / "default-cards.json"
    cache_path.write_text(json.dumps([{"name": "Card A"}]), encoding="utf-8")

    state = {
        "_cache": [],
    }
    calls = []

    loaded = state_service.load_default_cache(
        state,
        path=str(cache_path),
        default_cards_path_fn=lambda path: str(cache_path),
        prime_default_indexes_fn=lambda: calls.append(("prime", list(state["_cache"]))),
        clear_cached_catalog_fn=lambda: calls.append(("catalog", None)),
    )

    assert loaded is True
    assert state["_cache"] == [{"name": "Card A"}]
    assert calls == [("prime", [{"name": "Card A"}]), ("catalog", None)]


def test_load_and_index_with_progress_builds_indexes(tmp_path):
    from core.domains.cards.services import scryfall_cache_state_service as state_service

    cache_path = tmp_path / "default-cards.json"
    cache_path.write_text(
        json.dumps(
            [
                {
                    "name": "Ojer Axonil, Deepest Might // Temple of Power",
                    "set": "lci",
                    "collector_number": "158",
                    "oracle_id": "oid-1",
                    "card_faces": [
                        {"name": "Ojer Axonil, Deepest Might"},
                        {"name": "Temple of Power"},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    state = {
        "_cache": [],
        "_by_set_cn": {},
        "_by_oracle": {},
        "_set_names": None,
        "_set_releases": None,
        "_idx_by_set_num": {},
        "_idx_by_name": {},
        "_idx_by_front": {},
        "_idx_by_back": {},
    }
    progress = []
    calls = []

    loaded = state_service.load_and_index_with_progress(
        state,
        path=str(cache_path),
        default_cards_path_fn=lambda path: str(cache_path),
        step=1,
        progress_cb=lambda done, total: progress.append((done, total)),
        key_set_cn_fn=lambda set_code, cn: f"{set_code}::{cn}",
        cn_num_fn=lambda cn: int(cn),
        name_key_fn=lambda name: str(name).strip().lower(),
        front_face_name_fn=lambda card: (card.get("card_faces") or [{}])[0].get("name", card.get("name", "")),
        back_face_names_fn=lambda card: [
            face.get("name", "") for face in (card.get("card_faces") or [])[1:]
        ],
        clear_cached_set_profiles_fn=lambda: calls.append("profiles"),
        clear_cached_catalog_fn=lambda: calls.append("catalog"),
    )

    assert loaded is True
    assert state["_by_set_cn"]["lci::158"]["oracle_id"] == "oid-1"
    assert state["_by_oracle"]["oid-1"][0]["name"].startswith("Ojer Axonil")
    assert state["_idx_by_front"]["ojer axonil, deepest might"][0]["oracle_id"] == "oid-1"
    assert state["_idx_by_back"]["temple of power"][0]["oracle_id"] == "oid-1"
    assert progress == [(1, 1)]
    assert calls == ["profiles", "catalog"]
