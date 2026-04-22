"""Scryfall and oracle maintenance CLI command registration."""

from __future__ import annotations

import os

import click
from sqlalchemy import func

from extensions import db
from models import Card
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    cache_exists,
    candidates_by_set_and_name,
    find_by_set_cn,
    find_by_set_cn_loose,
    load_cache,
    normalize_set_code,
    unique_oracle_by_name,
)


def register_scryfall_cli_commands(app) -> None:
    @app.cli.command("inspect-oracle-ids")
    def inspect_oracle_ids():
        total = db.session.query(func.count(Card.id)).scalar() or 0
        with_oid = (
            db.session.query(func.count(Card.id))
            .filter(
                Card.oracle_id.isnot(None),
                Card.oracle_id != "",
            )
            .scalar()
            or 0
        )
        missing = total - with_oid
        click.echo(f"Cards total: {total}")
        click.echo(f"With oracle_id: {with_oid}")
        click.echo(f"Missing oracle_id: {missing}")

    @app.cli.command("backfill-oracle-ids")
    @click.option("--limit", type=int, default=0, help="Limit rows processed (0 = no limit).")
    def backfill_oracle_ids(limit):
        if not (cache_exists() and load_cache()):
            click.echo("No Scryfall bulk cache found. Run: flask fetch-scryfall-bulk")
            return

        query = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == ""))  # noqa: E711
        if limit and limit > 0:
            query = query.limit(limit)

        scanned = set_count = batch = 0
        for card in query:
            scanned += 1
            found = find_by_set_cn(card.set_code, card.collector_number, card.name)
            if found and found.get("oracle_id"):
                card.oracle_id = found["oracle_id"]
                set_count += 1
                batch += 1
                if batch >= 500:
                    db.session.commit()
                    batch = 0
        db.session.commit()
        click.echo(f"Scanned {scanned} row(s). Set oracle_id on {set_count}.")

    @app.cli.command("refresh-scryfall")
    def refresh_scryfall_cmd():
        if not (cache_exists() and load_cache()):
            click.echo("No local Scryfall cache found. Run: flask fetch-scryfall-bulk")
            return

        missing = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == "")).all()  # noqa: E711
        fixed = 0
        for card in missing:
            found = find_by_set_cn(card.set_code, card.collector_number, card.name)
            if found and found.get("oracle_id"):
                card.oracle_id = found["oracle_id"]
                fixed += 1
                if fixed % 500 == 0:
                    db.session.flush()
        db.session.commit()
        click.echo(f"Backfilled oracle_id for {fixed} card rows.")

    @app.cli.command("refresh-oracle-tags")
    def refresh_oracle_tags_cmd():
        """Recompute oracle core roles and evergreen tags from the Scryfall cache."""
        if not (cache_exists() and load_cache()):
            click.echo("No local Scryfall cache found. Run: flask fetch-scryfall-bulk")
            return
        from worker.tasks import recompute_oracle_deck_tags

        recompute_oracle_deck_tags()
        click.echo("Oracle core roles + evergreen tags refreshed from Scryfall cache.")

    @app.cli.command("refresh-oracle-tags-full")
    def refresh_oracle_tags_full_cmd():
        """Recompute oracle roles, keywords, typal tags, core roles, deck tags, and evergreen tags."""
        if not (cache_exists() and load_cache()):
            click.echo("No local Scryfall cache found. Run: flask fetch-scryfall-bulk")
            return
        from worker.tasks import recompute_oracle_enrichment

        recompute_oracle_enrichment()
        click.echo("Full oracle enrichment refreshed from Scryfall cache.")

    @app.cli.command("refresh-card-roles")
    @click.option("--replace", is_flag=True, help="Replace existing roles instead of merging.")
    def refresh_card_roles_cmd(replace):
        """Update card roles from Scryfall oracle text (merges by default)."""
        if not (cache_exists() and load_cache()):
            click.echo("No local Scryfall cache found. Roles will be derived from card rows only.")
        from worker.tasks import recompute_all_roles

        recompute_all_roles(merge_existing=not replace)
        click.echo("Card roles refreshed from oracle text.")

    @app.cli.command("cache-stats")
    @click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
    def cache_stats_cmd(as_json):
        """Show status of local Scryfall caches (prints + rulings)."""
        import json as _json

        stats = sc.cache_stats()
        if as_json:
            click.echo(_json.dumps(stats, indent=2, sort_keys=True))
            return

        prints = stats.get("prints", {}) or {}
        rulings = stats.get("rulings", {}) or {}

        def _hb(size_bytes):
            units = ["B", "KB", "MB", "GB", "TB"]
            size_bytes = int(size_bytes or 0)
            index = 0
            while size_bytes >= 1024 and index < len(units) - 1:
                size_bytes /= 1024.0
                index += 1
            return f"{size_bytes:.1f} {units[index]}"

        click.echo("PRINTS (default_cards):")
        click.echo(f"  File: {prints.get('file')}")
        click.echo(f"  Exists: {prints.get('exists')}  Size: {_hb(prints.get('size_bytes'))}  Stale: {prints.get('stale')}")
        click.echo(
            f"  Records loaded: {prints.get('records')}  "
            f"Unique sets: {prints.get('unique_sets')}  "
            f"Unique oracles: {prints.get('unique_oracles')}"
        )
        index_sizes = prints.get("index_sizes", {}) or {}
        click.echo(f"  Index sizes: by_set_cn={index_sizes.get('by_set_cn')}  by_oracle={index_sizes.get('by_oracle')}")
        click.echo("")
        click.echo("RULINGS:")
        click.echo(f"  File: {rulings.get('file')}")
        click.echo(f"  Exists: {rulings.get('exists')}  Size: {_hb(rulings.get('size_bytes'))}  Stale: {rulings.get('stale')}")
        click.echo(f"  Entries loaded: {rulings.get('entries')}  Oracle keys: {rulings.get('oracle_keys')}")

    @app.cli.command("diagnose-missing-oracle")
    def diagnose_missing_oracle():
        """Print rows missing oracle_id and show likely Scryfall candidates."""
        if not (cache_exists() and load_cache()):
            click.echo("No Scryfall bulk cache found. Run: flask --app app:create_app fetch-scryfall-bulk")
            return

        rows = (
            db.session.query(Card)
            .filter((Card.oracle_id == None) | (Card.oracle_id == ""))  # noqa: E711
            .order_by(Card.set_code.asc(), Card.collector_number.asc(), Card.name.asc())
            .all()
        )
        if not rows:
            click.echo("All cards have oracle_id.")
            return

        click.echo(f"{len(rows)} row(s) missing oracle_id:\n")
        for card in rows:
            click.echo(f"- id={card.id}  {card.name}  [{card.set_code} {card.collector_number}]  lang={card.lang or 'en'}")
            candidate = find_by_set_cn_loose(card.set_code, card.collector_number, card.name)
            if candidate:
                click.echo(
                    "    ✓ loose match "
                    f"oracle_id={candidate.get('oracle_id')}  "
                    f"cn={candidate.get('collector_number')}  name={candidate.get('name')}"
                )
            else:
                candidates = candidates_by_set_and_name(card.set_code, card.name)
                if candidates:
                    sample = ", ".join(f"{entry.get('collector_number')}" for entry in candidates[:5])
                    more = "" if len(candidates) <= 5 else f" (+{len(candidates) - 5} more)"
                    click.echo(f"    ? name/set candidates: {sample}{more}")
                else:
                    click.echo("    ? no candidates by set+name")

    @app.cli.command("backfill-oracle-ids-fuzzy")
    @click.option("--limit", type=int, default=0, help="Optionally limit how many to try (0 = all).")
    @click.option("--dry-run", is_flag=True, help="Show what would change, but do not write.")
    def backfill_oracle_ids_fuzzy(limit, dry_run):
        if not (cache_exists() and load_cache()):
            click.echo("No Scryfall cache. Run: flask --app app:create_app fetch-scryfall-bulk")
            return

        query = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == ""))  # noqa: E711
        if limit and limit > 0:
            query = query.limit(limit)

        scanned = 0
        set_count = 0
        for card in query:
            scanned += 1
            candidate = find_by_set_cn_loose(card.set_code, card.collector_number, card.name)
            if not candidate or not candidate.get("oracle_id"):
                continue
            if dry_run:
                click.echo(
                    f"DRY: would set card id={card.id} "
                    f"'{card.name}' ({card.set_code} {card.collector_number}) -> {candidate['oracle_id']}"
                )
                set_count += 1
            else:
                card.oracle_id = candidate["oracle_id"]
                set_count += 1
                if set_count % 500 == 0:
                    db.session.flush()

        if not dry_run:
            db.session.commit()

        click.echo(f"Scanned {scanned} row(s). {'Would set' if dry_run else 'Set'} oracle_id on {set_count}.")

    @app.cli.command("repair-oracle-ids-advanced")
    @click.option("--limit", type=int, default=0, help="Process only N missing rows.")
    @click.option("--dry-run", is_flag=True, help="Show what would be changed, but don’t write.")
    def repair_oracle_ids_advanced(limit, dry_run):
        if not (cache_exists() and load_cache()):
            click.echo("No Scryfall cache. Run: flask --app app:create_app fetch-scryfall-bulk")
            return

        query = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == ""))  # noqa: E711
        if limit and limit > 0:
            query = query.limit(limit)

        scanned = 0
        fixed = 0
        for card in query:
            scanned += 1
            found = (
                find_by_set_cn(card.set_code, card.collector_number, card.name)
                or find_by_set_cn_loose(card.set_code, card.collector_number, card.name)
            )

            if not found:
                normalized_set = normalize_set_code(card.set_code)
                if normalized_set != (card.set_code or ""):
                    found = (
                        find_by_set_cn(normalized_set, card.collector_number, card.name)
                        or find_by_set_cn_loose(normalized_set, card.collector_number, card.name)
                    )

            if not found and " // " in (card.name or ""):
                for face_name in (card.name or "").split(" // "):
                    face_name = face_name.strip()
                    found = find_by_set_cn_loose(card.set_code, card.collector_number, face_name)
                    if found:
                        break

            if not found:
                oracle_id = unique_oracle_by_name(card.name)
                if oracle_id:
                    if not dry_run:
                        card.oracle_id = oracle_id
                    fixed += 1
                    continue

            if found and found.get("oracle_id"):
                if not dry_run:
                    card.oracle_id = found["oracle_id"]
                fixed += 1

        if not dry_run and fixed:
            db.session.commit()
        click.echo(f"Scanned {scanned} row(s). {'Set' if not dry_run else 'Would set'} oracle_id on {fixed}.")

    @app.cli.command("map-set-codes")
    @click.option("--apply", is_flag=True, help="Apply changes (otherwise preview).")
    def map_set_codes(apply):
        aliases = {"vthb": "thb"}
        keys = list(aliases.keys())
        if not keys:
            click.echo("No aliases configured.")
            return

        rows = Card.query.filter(Card.set_code.in_(keys)).all()
        if not rows:
            click.echo("No rows with mapped vendor set codes.")
            return

        for row in rows:
            new_code = aliases.get((row.set_code or "").lower())
            click.echo(f"{row.id}: {row.name}   {row.set_code} -> {new_code}")
            if apply and new_code:
                row.set_code = new_code

        if apply:
            db.session.commit()
            click.echo(f"Updated {len(rows)} row(s). Now run:")
            click.echo("  flask --app app:create_app backfill-oracle-ids")

    @app.cli.command("diagnose-missing-oracle-extended")
    def diagnose_missing_oracle_extended():
        query = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == "")).order_by(Card.id.asc())  # noqa: E711
        rows = query.all()
        if not rows:
            click.echo("No rows missing oracle_id.")
            return

        have_cache = cache_exists() and load_cache()
        cache_sets = set(sc.all_set_codes()) if have_cache else set()

        click.echo(f"{len(rows)} row(s) missing oracle_id:\n")
        for card in rows:
            set_code = (card.set_code or "").lower()
            normalized_set = normalize_set_code(set_code)
            unique_oid = unique_oracle_by_name(card.name)

            reasons = []
            if not have_cache:
                reasons.append("cache not loaded")
            if set_code and set_code not in cache_sets:
                reasons.append(f"set '{set_code}' not in cache")
            if normalized_set != set_code and normalized_set in cache_sets:
                reasons.append(f"try alias: {set_code} → {normalized_set}")
            if unique_oid:
                reasons.append("name is unique across cache (can force by name)")

            reasons_txt = "; ".join(reasons) or "unknown cause"
            click.echo(
                f"- id={card.id:<6} {card.name}  [{card.set_code or '?'} {card.collector_number or '?'}] lang={card.lang or 'en'}\n"
                f"    → {reasons_txt}"
            )

        click.echo("\nNext steps:")
        click.echo("  1) Refresh bulk cache if sets look 'not in cache':")
        click.echo("       flask --app app:create_app fetch-scryfall-bulk --progress")
        click.echo("       flask --app app:create_app refresh-scryfall")
        click.echo("       flask --app app:create_app repair-oracle-ids-advanced")
        click.echo("  2) If an alias is suggested (e.g. tdm → realcode), add it to normalize_set_code() and rerun.")
        click.echo("  3) If a name is unique, use:")
        click.echo("       flask --app app:create_app force-oracle-by-name \"Exact Card Name\"")

    @app.cli.command("force-oracle-by-name")
    @click.argument("name", nargs=-1)
    @click.option("--dry-run", is_flag=True, help="Preview only; no DB writes.")
    def force_oracle_by_name(name, dry_run):
        full_name = " ".join(name).strip()
        if not full_name:
            raise click.ClickException("Provide the exact card name, e.g. \"Magmatic Hellkite\"")

        if not (cache_exists() and load_cache()):
            raise click.ClickException("No Scryfall cache. Run fetch-scryfall-bulk first.")

        oracle_id = unique_oracle_by_name(full_name)
        if not oracle_id:
            raise click.ClickException(f"Name '{full_name}' is not unique across cache (or not found).")

        query = (
            Card.query
            .filter((Card.oracle_id == None) | (Card.oracle_id == ""))  # noqa: E711
            .filter(Card.name == full_name)
        )
        targets = query.all()
        if not targets:
            click.echo(f"No rows missing oracle_id for name '{full_name}'.")
            return

        for card in targets:
            click.echo(f"Set id={card.id}  {card.name} [{card.set_code or '?'} {card.collector_number or '?'}] → {oracle_id}")
            if not dry_run:
                card.oracle_id = oracle_id

        if not dry_run:
            db.session.commit()
            click.echo(f"Updated {len(targets)} row(s).")
        else:
            click.echo(f"Would update {len(targets)} row(s).")

    @app.cli.command("cache-has-set")
    @click.argument("set_code")
    def cache_has_set(set_code):
        if not (cache_exists() and load_cache()):
            click.echo("No Scryfall cache loaded.")
            return
        normalized_set = (set_code or "").lower()
        present = normalized_set in set(sc.all_set_codes())
        click.echo(f"Set '{normalized_set}': {'present' if present else 'NOT present'} in cache.")

    @app.cli.command("fetch-scryfall-bulk")
    @click.option("--path", default=None, show_default=False, help="Where to save the bulk file (defaults to sc.DEFAULT_PATH).")
    @click.option("--progress", is_flag=True, help="Show download/index progress.")
    def fetch_scryfall_bulk(path, progress):
        """Download Scryfall 'default_cards' bulk JSON and (re)build local indexes."""
        path = path or sc.DEFAULT_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)

        url = sc.get_default_cards_download_uri()
        if not url:
            raise click.ClickException("Could not get default_cards download URI from Scryfall.")

        progress_cb = None
        if progress:
            click.echo(f"Downloading default_cards -> {path}")
            last_report = 0

            def _progress_cb(got, total):
                nonlocal last_report
                if total and total > 0:
                    should_report = (got - last_report) >= (2 << 20) or got == total
                else:
                    should_report = (got - last_report) >= (2 << 20)
                if not should_report:
                    return
                pct = (got / total * 100.0) if total else 0.0
                click.echo(f"\r  {got:,}/{total or 0:,} bytes ({pct:5.1f}%)", nl=False)
                last_report = got

            progress_cb = _progress_cb

        result = sc.stream_download_to(path, url, progress_cb=progress_cb)
        if progress:
            click.echo()

        if result.get("status") == "not_modified":
            click.echo("Remote bulk file is already up to date (ETag matched). (Re)building indexes...")
        else:
            click.echo("Download complete. (Re)building indexes...")

        if progress:
            def cb(done, total):
                pct = (done / total * 100.0) if total else 0.0
                click.echo(f"\r  Indexed {done:,}/{total:,} cards ({pct:5.1f}%)", nl=False)

            sc.load_and_index_with_progress(path, step=5000, progress_cb=cb)
            click.echo()
        else:
            sc.load_cache(path)

        stats = sc.cache_stats(path)["prints"]
        size = stats.get("size_bytes", 0)
        click.echo(
            f"Loaded {stats.get('records', 0):,} records; "
            f"{stats.get('unique_oracles', 0):,} unique oracles; "
            f"file size {size:,} bytes."
        )

    @app.cli.command("force-unique-names-missing")
    @click.option("--dry-run", is_flag=True, help="Preview updates; no DB writes.")
    def force_unique_names_missing(dry_run):
        if not (cache_exists() and load_cache()):
            raise click.ClickException("No Scryfall cache loaded. Run: flask --app app:create_app fetch-scryfall-bulk")

        rows = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == "")).order_by(Card.id.asc()).all()  # noqa: E711
        if not rows:
            click.echo("Nothing to do: no rows missing oracle_id.")
            return

        updated = 0
        scanned = 0
        for card in rows:
            scanned += 1
            oracle_id = unique_oracle_by_name(card.name)
            if oracle_id:
                click.echo(f" id={card.id:<6} {card.name} [{card.set_code or '?'} {card.collector_number or '?'}] → {oracle_id}")
                if not dry_run:
                    card.oracle_id = oracle_id
                updated += 1

        if not dry_run and updated:
            db.session.commit()

        click.echo(f"Scanned {scanned} row(s). {'Set' if not dry_run else 'Would set'} oracle_id on {updated}.")
        if updated == 0:
            click.echo("Tip: If these are very new prints, re-download bulk, then:")
            click.echo("  flask --app app:create_app fetch-scryfall-bulk --progress")
            click.echo("  flask --app app:create_app refresh-scryfall")
            click.echo("  flask --app app:create_app repair-oracle-ids-advanced")

    @app.cli.command("rulings-stats")
    def rulings_stats_cmd():
        stats = sc.cache_stats().get("rulings", {})
        size = stats.get("size_bytes") or 0

        def _hb(size_bytes):
            units = ["B", "KB", "MB", "GB", "TB"]
            index = 0
            while size_bytes >= 1024 and index < len(units) - 1:
                size_bytes /= 1024.0
                index += 1
            return f"{size_bytes:.1f} {units[index]}"

        click.echo(f"Rulings file: {stats.get('file')}")
        click.echo(f"Exists: {stats.get('exists')}  Size: {_hb(size)}")
        click.echo(f"Entries (loaded): {stats.get('entries')}  Oracle keys: {stats.get('oracle_keys')}")
        click.echo(f"Stale: {stats.get('stale')}")
