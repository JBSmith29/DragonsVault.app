"""General maintenance, import, and queue CLI command registration."""

from __future__ import annotations

import json
from pathlib import Path

import click
from sqlalchemy import func, text

from extensions import db
from models import Card
from core.domains.cards.services.import_helpers import (
    delete_empty_folders,
    purge_cards_preserve_commanders,
    restore_commander_metadata,
)
from shared.database.fts import ensure_fts, reindex_fts


def register_general_cli_commands(app) -> None:
    @app.cli.command("seed-roles")
    def seed_roles():
        from seeds.seed_roles import seed_roles_and_subroles

        seed_roles_and_subroles()

    from core.domains.cards.services.csv_importer import HeaderValidationError, process_csv
    from core.domains.decks.services.spellbook_sync import (
        DEFAULT_SPELLBOOK_CONCURRENCY,
        EARLY_MANA_VALUE_THRESHOLD,
        LATE_MANA_VALUE_THRESHOLD,
        generate_spellbook_combo_dataset,
        write_dataset_to_file,
    )

    @app.cli.command("import-csv")
    @click.argument("filepath")
    @click.option("--dry-run", is_flag=True, help="Preview only; no DB changes.")
    @click.option("--default-folder", default="Unsorted", show_default=True, help="Folder to use when file lacks a folder column.")
    @click.option("--overwrite", is_flag=True, help="Delete ALL cards first (keep folders/commanders), then import.")
    @click.option("--owner-user-id", type=int, default=None, help="User ID to scope the import to.")
    @click.option(
        "--quantity-mode",
        type=click.Choice(["new_only"]),
        default="new_only",
        show_default=True,
        help="new_only: create only brand-new rows.",
    )
    def import_csv_cmd(filepath, dry_run, default_folder, overwrite, owner_user_id, quantity_mode):
        """Import CSV or Excel file (xlsx/xlsm supported)."""
        path = Path(filepath).expanduser()
        if not path.is_absolute():
            path = Path(app.root_path) / path
        path = path.resolve()
        if not path.exists():
            raise click.ClickException(f"File not found: {path}")
        if owner_user_id is None:
            raise click.ClickException("Provide --owner-user-id to scope the import to a single user.")

        preserved = None
        removed = 0
        should_reset = not dry_run and overwrite
        try:
            with db.session.begin():
                if should_reset:
                    click.echo("Clearing existing cards before import...")
                    preserved = purge_cards_preserve_commanders(
                        owner_user_id=owner_user_id,
                        commit=False,
                    )

                stats, per_folder = process_csv(
                    str(path),
                    default_folder=default_folder,
                    dry_run=dry_run,
                    quantity_mode=quantity_mode,
                    commit=False,
                    owner_user_id=owner_user_id,
                )

                if preserved:
                    restore_commander_metadata(
                        preserved,
                        owner_user_id=owner_user_id,
                        commit=False,
                    )
                    removed = delete_empty_folders(
                        owner_user_id=owner_user_id,
                        commit=False,
                    )
        except HeaderValidationError as exc:
            raise click.ClickException(str(exc)) from exc

        if preserved:
            click.echo(f"Restored commander metadata; removed {removed} empty folder(s).")

        click.echo(
            f"Added {stats.added}, Updated {stats.updated}, "
            f"Skipped {stats.skipped}, Errors {stats.errors}"
        )
        if per_folder:
            top = ", ".join(f"{key}:{value}" for key, value in list(per_folder.items())[:10])
            click.echo(f"By folder (first 10): {top}")

    @app.cli.command("rq-worker")
    @click.option("--queue", default="default", show_default=True)
    def rq_worker(queue):
        """Run an RQ worker that processes background jobs."""
        from rq import Worker
        from shared.jobs.task_queue import get_queue

        q = get_queue(queue)
        worker = Worker([q], connection=q.connection)
        click.echo(f"Starting RQ worker for queue '{queue}'")
        worker.work()

    @app.cli.command("sync-spellbook-combos")
    @click.option(
        "--output",
        default="data/spellbook_combos.json",
        show_default=True,
        help="Destination file for the Commander Spellbook combo dataset.",
    )
    @click.option(
        "--early-threshold",
        default=EARLY_MANA_VALUE_THRESHOLD,
        type=int,
        show_default=True,
        help="Maximum mana value needed to treat a combo as early-game.",
    )
    @click.option(
        "--late-threshold",
        default=LATE_MANA_VALUE_THRESHOLD,
        type=int,
        show_default=True,
        help="Minimum mana value needed to treat a combo as late-game.",
    )
    @click.option(
        "--card-count",
        "card_counts",
        type=int,
        multiple=True,
        help="Restrict combos to the given card counts (repeat flag to include multiple). Defaults to 2 and 3 cards.",
    )
    @click.option(
        "--progress/--no-progress",
        default=True,
        show_default=True,
        help="Show a progress bar while downloading combos.",
    )
    @click.option(
        "--concurrency",
        default=DEFAULT_SPELLBOOK_CONCURRENCY,
        show_default=True,
        type=int,
        help="Parallel download workers for Commander Spellbook API (lower if the API rate limits).",
    )
    @click.option(
        "--skip-existing/--no-skip-existing",
        default=False,
        show_default=True,
        help="Skip combos already present in the output file (faster, but may miss updated data).",
    )
    def sync_spellbook_combos(output, early_threshold, late_threshold, card_counts, progress, concurrency, skip_existing):
        """Download instant-win combos from Commander Spellbook and persist them locally."""
        workers = max(1, int(concurrency or 1))
        existing_ids = set()
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = Path(app.root_path) / output_path

        if skip_existing and output_path.exists():
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
                for entry in payload.get("early_game", []):
                    combo_id = entry.get("id")
                    if combo_id:
                        existing_ids.add(str(combo_id))
                for entry in payload.get("late_game", []):
                    combo_id = entry.get("id")
                    if combo_id:
                        existing_ids.add(str(combo_id))
            except Exception as exc:  # pragma: no cover - defensive
                click.echo(f"Warning: unable to read existing dataset ({exc}); not skipping existing combos.", err=True)
                existing_ids.clear()

        def _run_sync(with_progress: bool):
            if not with_progress:
                return generate_spellbook_combo_dataset(
                    early_threshold=early_threshold,
                    late_threshold=late_threshold,
                    card_count_targets=card_counts or (2, 3),
                    concurrency=workers,
                    existing_ids=existing_ids if skip_existing else None,
                )

            with click.progressbar(
                iterable=range(0),
                length=0,
                label="Downloading Commander Spellbook combos",
                show_eta=False,
                show_percent=False,
                show_pos=True,
            ) as bar:

                def _progress_callback(_, total):
                    if total and (bar.length or 0) == 0:
                        bar.length = total
                        bar.show_percent = True
                        bar.show_eta = True
                    bar.update(1)

                return generate_spellbook_combo_dataset(
                    early_threshold=early_threshold,
                    late_threshold=late_threshold,
                    card_count_targets=card_counts or (2, 3),
                    progress_callback=_progress_callback,
                    concurrency=workers,
                    existing_ids=existing_ids if skip_existing else None,
                )

        dataset = _run_sync(progress)
        write_dataset_to_file(dataset, output_path)

        click.echo(
            "Commander Spellbook combos synced: "
            f"{len(dataset['early_game'])} early-game, {len(dataset['late_game'])} late-game entries."
        )
        click.echo(f"Dataset written to {output_path}")

    @app.cli.command("fts-ensure")
    def cli_fts_ensure():
        """Create FTS table & triggers if missing."""
        ensure_fts()
        click.echo("FTS ensured.")

    @app.cli.command("fts-reindex")
    def cli_fts_reindex():
        """Rebuild the FTS index from current cards."""
        reindex_fts()
        click.echo("FTS reindexed.")

    @app.cli.command("dedupe-cards")
    def dedupe_cards():
        required = ["lang", "is_foil", "quantity"]
        missing = [field for field in required if not hasattr(Card, field)]
        if missing:
            click.echo(
                "Card model missing: " + ", ".join(missing) +
                "\nEnsure your models are up to date and migrations applied."
            )
            return

        session = db.session
        groups = (
            session.query(
                Card.name, Card.folder_id, Card.set_code, Card.collector_number,
                Card.lang, Card.is_foil, func.count(Card.id).label("cnt")
            )
            .group_by(
                Card.name, Card.folder_id, Card.set_code, Card.collector_number,
                Card.lang, Card.is_foil
            )
            .having(func.count(Card.id) > 1)
            .all()
        )

        total_merged = 0
        for (name, folder_id, set_code, collector_number, lang, is_foil, _cnt) in groups:
            rows = (
                session.query(Card)
                .filter_by(
                    name=name, folder_id=folder_id, set_code=set_code,
                    collector_number=collector_number, lang=lang, is_foil=is_foil
                )
                .order_by(Card.id.asc())
                .all()
            )
            keeper = rows[0]
            keeper.quantity = sum((row.quantity or 1) for row in rows)
            for row in rows[1:]:
                session.delete(row)
            total_merged += len(rows) - 1

        session.commit()
        click.echo(f"Merged {total_merged} duplicate rows.")

    @app.cli.command("analyze")
    def analyze_sqlite():
        db.session.execute(text("ANALYZE"))
        db.session.commit()
        click.echo("ANALYZE complete.")

    @app.cli.command("vacuum")
    def vacuum_sqlite():
        db.session.execute(text("VACUUM"))
        db.session.commit()
        click.echo("VACUUM complete.")
