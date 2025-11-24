# DragonsVault ??

![python](https://img.shields.io/badge/python-3.11%2B-blue)
![Docker](https://img.shields.io/badge/docker-required-blue)
![License](https://img.shields.io/badge/license-Unlicense-green)

DragonsVault is a **Magic: The Gathering** collection manager, powered by Docker. Collect your cards, vault your desks and conquer your opponents from within a single app!

## ?? Features

| **Page / Feature** | **Highlights** |
| -- | - |
| **Dashboard** | Overall stats, deck tiles with commander art, collection totals, quick actions. |
| **Cards** | Filterable table with art thumbnails, deck context, color identity, wishlist badges. |
| **Collection** | Bucket-level stats (e.g., Mythic, Lands), type breakdown tiles linking back into the list view. |
| **Deck detail** | Commander picker, mana curve, pip breakdown, export to CSV, folder insights. |
| **Build-A-Deck** | Persistent sandbox with EDHREC integrations, gallery/list view toggle, role filters, and stateful panels. |
| **Card detail** | Owned print metadata, alternate arts, tokens, rulings, external links. |
| **Scryfall browser** | Live API search with offline owned counts, jump into print detail or the local card view. |
| **Wishlist** | Track requested quantities, status transitions, inline edits, CSV export. |
| **Import/Export** | Preview uploads, configure quantity mode, download templates or filtered exports. |
| **List Checker** | Compare a pasted deck list against ownership, highlight missing cards, export results. |
| **Admin** | Refresh Scryfall caches, manage folder categories, clear caches, inspect stats. |

## ?? Prerequisites

- ?? [Docker](https://www.docker.com/get-started/)

## ? Quickstart Guide

### 1. Clone the Repository

```bash
git clone https://github.com/JBSmith29/DragonsVault.git
cd DragonsVault
```

> **Optional**: Create a `.env` file within the projects root directory for secrets or overrides. Any variables in this file are passed into the container.

### 2. Verify that Docker is Running

```bash
docker info
```

> If you get an error, make sure Docker Desktop or your Docker service is running.

### 3. Deploy the DragonsVault Server with Docker

```bash
docker compose up --build
```

### 4. Access DragonsVault through your Web Browser

Within a web browser, navigate to [http://localhost:8000](http://localhost:8000) to access your vault! The Flask debug reloader is enabled, so code changes on your host refresh automatically.

### 5. Interacting with your DragonsVault Instance after it's Launched 

- **Modifying my Container**: Within another command-line terminal, navigate to the root directory of your DragonsVault application. Then run `docker compose exec web flask [COMMAND]` to execute additional configuration commands.
- **Stopping my Container**: Within the original command-line terminal that your used to launch your instance, press <kbd>Ctrl</kbd> + <kbd>C</kbd>.
- **Rebuilding my Container**: If you need to rebuild your DragonsVault instance for any reason, run `docker compose down` to remove your existing installation file.
- **Restarting your Container**: To restart your existing DragonsVault instance, run `docker compose up`.

### 6. Configure your DragonsVault Instance 

### 6.1 Download the Scryfall Bulk Data Collection

Run the following commands against your running DragonsVault instance using `docker compose exec`. The shell snippets use a heredoc so the embedded Python executes in a single command (PowerShell users can copy/paste the inline `python - <<'PY' … PY` block instead).

   ```bash
   docker compose exec web flask fetch-scryfall-bulk --progress
   docker compose exec web flask refresh-scryfall
   docker compose exec web flask shell <<'PY'
   from services.jobs import run_scryfall_refresh_inline
   run_scryfall_refresh_inline('rulings')
   exit()
   PY
   docker compose exec web flask shell <<'PY'
   from services.symbols_cache import ensure_symbols_cache
   ensure_symbols_cache(force=True)
   exit()
   PY
   docker compose exec web flask sync-spellbook-combos
   ```

> `flask shell` does not support `-c`; you must drop into the shell (or use the heredoc above) and run the Python import manually.

> This step is optional, but highly recommended for offline browsing.

### 6.2 Classify Folders

Navigate to `Admin` ? `Folder Categories`. Mark your bulk collection bins (e.g., Lands, Mythic) as `collection` and keep actual decks as `deck`.

### 6.3 Import Existing Cards

Use the `/import` or the `docker compose exec web flask import-csv` CLI. Once the import finishes, the app automatically redirects to the Folder Categories screen so you can classify any newly created folders before continuing.

### 6.4 Verify Dashboards

Manually check that the main dashboard, cards list, deck pages, and wishlist to confirm data looks correct.

### 6.5 Re-Index FTS (Optional)

After performing a large import, execute `docker compose exec web flask fts-reindex` against your application to improve the performance.

## ?? Importing Custom Data

### CSV / Excel Import Format

Recognized headers include `folder`, `name`, `set_code`, `collector_number`, `quantity`, `lang`, `foil`. Case and spacing are forgiving; the importer normalizes common variants.

- ManaBox exports are supported: `Binder Name` maps to folders and `Binder Type` (Deck/Binder) automatically sets each folder to `deck` or `collection`. An example `csv` file is listed below.

   ```csv
   folder,name,set_code,collector_number,quantity,foil,lang
   Collection,Sol Ring,2xm,229,1,0,en
   Mono-Red,Lightning Bolt,m11,146,4,0,en
   Bulk Rares,Golos, Tireless Pilgrim,m20,226,1,0,en
   ```

- Excel (`.xlsx`, `.xlsm`) files are supported; only the first worksheet is read.
- `quantity_mode` option (`delta` or `new_only`) controls whether imports add to existing totals or only create brand-new rows. Combine with `--overwrite` when you need to wipe everything and rebuild from a fresh spreadsheet.

### Collection Export

- Cards list: `/cards/export`
- Wishlist: `/wishlist/export`
- List checker results: `/list-checker/export`
- Import template: `/import/template.csv`

All exports include a UTF-8 BOM for compatibility with Excel.

## ?? Authentication & API Tokens

- **Create users**  run `docker compose exec web flask users create USERNAME EMAIL --admin` (or use the Admin ? Create User form). Usernames must be unique and logins accept either email or username; passwords are prompted interactively.
- **Sign in**  visit `/login` to access Import/Admin links plus the account menu.
- **Generate tokens**  use the `/account/api-token` page or `docker compose exec web flask users token you@example.com` to print a new Bearer token (shown once).
- **Use tokens**  add `Authorization: Bearer <token>` when calling protected endpoints from scripts/CI pipelines (query params are rejected).
- **Audit trail**  logins, admin actions, imports, and token rotations are stored in `audit_logs` for traceability.

## ?? Command Reference

| **Command** | **Purpose** |
| - | - |
| `flask db upgrade` | Apply database migrations. |
| `flask import-csv PATH [--dry-run] [--default-folder NAME] [--overwrite] [--quantity-mode {delta,new_only}]` | CLI importer mirroring the web importer. |
| `flask fetch-scryfall-bulk [--progress]` | Download the Scryfall `default_cards` bulk file. |
| `flask refresh-scryfall` | Load the downloaded bulk file into memory and build indexes. |
| `flask shell -c "from services.jobs import run_scryfall_refresh_inline; run_scryfall_refresh_inline('rulings')"` | Download rulings bulk data (inline helper). |
| `flask shell -c "from services.symbols_cache import ensure_symbols_cache; ensure_symbols_cache(force=True)"` | Refresh mana symbol JSON/SVGs. |
| `flask sync-spellbook-combos [--card-count N ...]` | Pull Commander Spellbook combos into `data/spellbook_combos.json`. |
| `flask repair-oracle-ids-advanced [--dry-run]` | Fill missing `oracle_id` values via Scryfall cache lookups. |
| `flask dedupe-cards` | Detect duplicate prints within folders. |
| `flask fts-ensure` | Ensure the FTS table & triggers exist. |
| `flask fts-reindex` | Rebuild the FTS index. |
| `flask analyze` | Run `ANALYZE` on the SQLite database. |
| `flask vacuum` | Run `VACUUM` on the SQLite database. |

A complete list lives near the bottom of `app.py`.

## ?? Tests

Run the test suite with:

```bash
pytest
```

CI is configured via GitHub Actions (`.github/workflows/python-tests.yml`). Extend the suite as you add features.

## ?? Configuration

Key environment variables (defaults in parentheses):

| **Variable** | **Default** | **Description** |
| -- | - | -- |
| `SECRET_KEY` | `"dev"` | Flask session/CSRF signing key. Replace in production. |
| `FLASK_ENV` | `production` | Set to `development` for debug mode + auto reload. |
| `DATABASE_URL` | `sqlite:///instance/database.db` | SQLAlchemy database URI. |
| `SCRYFALL_DATA_DIR` | `instance/data` | Storage path for Scryfall bulk cache. |
| `UPLOAD_FOLDER` | `instance/uploads` | Temp directory for uploaded spreadsheets. |
| `MAX_CONTENT_LENGTH` | `64 * 1024 * 1024` | Upload limit in bytes (default 64 MB). |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` behind HTTPS. |
| `ALLOW_RUNTIME_INDEX_BOOTSTRAP` | `0` | Enable only if you want runtime DB bootstrap in production. |
| `CACHE_TYPE` | `SimpleCache` | Cache backend (`SimpleCache`, `RedisCache`, `FileSystemCache`, etc.). |
| `CACHE_DEFAULT_TIMEOUT` | `600` | Cache TTL in seconds. |
| `CACHE_REDIS_URL` | `""` | Redis connection string (used when `CACHE_TYPE=RedisCache`). |
| `CACHE_DIR` | `instance/cache` | Filesystem cache directory (used when `CACHE_TYPE=FileSystemCache`). |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection used for rate limiting and background jobs. |
| `COMMANDER_SPELLBOOK_TIMEOUT` | `120` | Seconds to wait before spellbook downloads time out (adjust if the API is slow). |

`.env` files are loaded automatically by `python-dotenv`.

## ?? Project Layout

```bash
DragonsVault/
+-- app.py                # Flask application factory + CLI commands
+-- config.py             # Config classes & defaults
+-- extensions.py         # Shared extension instances
+-- models/               # SQLAlchemy models
+-- routes/               # Blueprint routes & helpers
+-- services/             # CSV importer, Scryfall cache, stats helpers
+-- templates/            # Jinja templates
+-- static/               # CSS, JS, mana symbol assets
+-- instance/             # SQLite database, downloads, Jinja cache (gitignored)
+-- migrations/           # Alembic migration scripts
+-- tests/                # Pytest suite
+-- requirements.txt      # Python dependency lock
+-- README.md             # Project documentation
```

## ??? Troubleshooting

| **Symptom** | **Resolution** |
| - | - |
| OperationalError: no such column: folder.category | Run `flask db upgrade`. If upgrading from a very old DB, stamp to the latest seen revision before running upgrade. |
| Scryfall-owned counts show zero | Ensure collection folders are marked as `collection` in Admin ? Folder Categories. |
| Mana symbols missing from card text | Run `flask shell -c "from services.symbols_cache import ensure_symbols_cache; ensure_symbols_cache(force=True)"` or use the Admin button to re-fetch symbology. |
| CSV import fails with Unsupported file type | Confirm the file is `.csv`, `.xlsx`, or `.xlsm`. The importer checks extensions. |
| FTS search returns stale results | Run `flask fts-reindex`. |
| SQLite locks / slow queries | Limit concurrent writers, run `flask analyze`/`flask vacuum`, and consider migrating to PostgreSQL if concurrency needs grow. |

## ?? License

This project is released under the [Unlicense](https://unlicense.org/), which dedicates it to the public domain. Card data and imagery are provided courtesy of [Scryfall](https://scryfall.com/) and remain  Wizards of the Coast.




