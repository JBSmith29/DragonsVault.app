from datetime import datetime, timezone

from flask import Flask


def test_validate_sqlite_database_quarantines_corrupt_file(tmp_path, monkeypatch):
    from shared.database import schema_bootstrap

    instance_dir = tmp_path / "instance"
    db_path = tmp_path / "data" / "broken.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"not a sqlite database")
    db_path.with_name(db_path.name + "-wal").write_bytes(b"wal")
    db_path.with_name(db_path.name + "-shm").write_bytes(b"shm")

    app = Flask(__name__, instance_path=str(instance_dir))
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path.as_posix()}"

    monkeypatch.setattr(
        schema_bootstrap,
        "utcnow",
        lambda: datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    )

    schema_bootstrap.validate_sqlite_database(app)

    backup = db_path.with_name("broken.sqlite.corrupt-20240102-030405")
    assert backup.exists()
    assert backup.read_bytes() == b"not a sqlite database"
    assert backup.with_name(backup.name + "-wal").exists()
    assert backup.with_name(backup.name + "-shm").exists()
    assert not db_path.exists()


def test_ensure_runtime_schema_fallbacks_runs_legacy_repairs(app, monkeypatch):
    from shared.database import schema_bootstrap

    calls: list[str] = []

    monkeypatch.setattr(schema_bootstrap.db, "create_all", lambda: calls.append("create_all"))
    monkeypatch.setattr(schema_bootstrap, "_ensure_folder_deck_tag_column", lambda: calls.append("deck_tag"))
    monkeypatch.setattr(schema_bootstrap, "_ensure_folder_owner_user_column", lambda: calls.append("owner_user"))
    monkeypatch.setattr(schema_bootstrap, "_ensure_card_metadata_columns", lambda: calls.append("card_metadata"))
    monkeypatch.setattr(schema_bootstrap, "_ensure_folder_notes_column", lambda: calls.append("notes"))
    monkeypatch.setattr(schema_bootstrap, "_ensure_folder_sleeve_color_column", lambda: calls.append("sleeve_color"))
    monkeypatch.setattr(
        schema_bootstrap,
        "_ensure_folder_sharing_columns",
        lambda *, fallback_enabled: calls.append(f"sharing_columns:{fallback_enabled}"),
    )
    monkeypatch.setattr(
        schema_bootstrap,
        "_ensure_folder_share_table",
        lambda *, fallback_enabled: calls.append(f"share_table:{fallback_enabled}"),
    )
    monkeypatch.setattr(schema_bootstrap, "_ensure_wishlist_columns", lambda: calls.append("wishlist"))

    class Inspector:
        @staticmethod
        def get_table_names():
            return {"folder"}

    monkeypatch.setattr(schema_bootstrap, "inspect", lambda _engine: Inspector())
    monkeypatch.setitem(app.config, "ALLOW_RUNTIME_INDEX_BOOTSTRAP", True)

    with app.app_context():
        schema_bootstrap.ensure_runtime_schema_fallbacks(app, fallback_enabled=True)

    assert calls == [
        "create_all",
        "deck_tag",
        "owner_user",
        "card_metadata",
        "notes",
        "sleeve_color",
        "sharing_columns:True",
        "share_table:True",
        "wishlist",
        "create_all",
    ]
