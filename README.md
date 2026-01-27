# DragonsVault

![python](https://img.shields.io/badge/python-3.11%2B-blue)
![Docker](https://img.shields.io/badge/docker-required-blue)
![License](https://img.shields.io/badge/license-Unlicense-green)

DragonsVault is a Docker-first **Magic: The Gathering** collection manager for cards, decks, and games. Track ownership, build decks, log pods, and explore data powered by Scryfall, EDHREC, and more.

## Highlights

- Collection browser with filters, art thumbnails, color identity, owned counts, and wishlist badges.
- Deck tools: commander picker, mana curve, pip breakdown, role insights, and CSV export.
- Build-A-Deck sandbox with EDHREC integration, role filters, list/gallery views, and persistent panels.
- Games workflow: unified dashboard, streamlined pod management, auto deck assignment, and a 3-step quick log.
- Metrics and leaderboards with quick filters, exports, and admin tooling.
- CSV/Excel imports (ManaBox, Moxfield), list checker, and export templates.
- Admin/ops: cache refresh, folder categories, health checks, and bulk actions.

## Feature tour

### Collection & Cards
- Filterable cards table with art thumbnails, deck context, color identity, and wishlist badges.
- Card detail with owned print metadata, alternate arts, tokens, rulings, and external links.
- Collection insights with bucket-level stats (e.g., Mythic, Lands) and type breakdown tiles.

### Decks & Building
- Deck detail with commander picker, mana curve, pip breakdown, CSV export, and folder insights.
- Build-A-Deck sandbox with EDHREC integrations, role filters, list/gallery toggle, and stateful panels.
- List checker to compare pasted deck lists against ownership and export results.

### Games & Analytics
- Unified games dashboard with quick metrics, recent activity, and admin panel.
- Streamlined pod management with quick pods, invitations, templates, and bulk operations.
- Auto deck assignment based on player ownership and preferences.
- 3-step quick log wizard with advanced mode toggle.
- Enhanced metrics with quick filters, leaderboards, and export tools.
- Admin endpoints for system stats, cache management, and health checks.

### Imports, Exports, and Lists
- CSV/Excel import with preview, quantity modes, and templates.
- Wishlist tracking with status transitions, inline edits, and export.
- Scryfall browser with live API search and offline owned counts.

### Integrations & Data
- Scryfall bulk cache for offline browsing, rulings, and symbols.
- Commander Spellbook combo sync for deck insights.
- MTGJSON pricing (price-service) and EDHREC data (edhrec-service).

### Admin & Ops
- Admin tools for cache refresh, folder categories, and stats inspection.
- Health checks, structured logs, and background job visibility.

## Architecture at a glance

- Flask monolith + RQ worker, plus Vite/React UI (dev profile).
- Postgres + PgBouncer, Redis, and nginx reverse proxy.
- Microservices for card-data, price-service, edhrec-service, and user-manager scaffolding.
- Experimental Django API at `/api-next` for migration testing.

## Prerequisites

- Docker (Desktop or Engine)
- Git (optional, for cloning)

## Quickstart

### 1. Clone the repository

```bash
git clone https://github.com/JBSmith29/DragonsVault.git
cd DragonsVault
```

> Optional: Create a `.env` file in the project root for secrets or overrides. Any variables in this file are passed into the containers.

### 2. Start the stack (Postgres + PgBouncer)

```bash
cp infra/env.postgres.example .env

docker compose --env-file .env up -d postgres pgbouncer redis pgmaintenance
```

### 3. Initialize the database

```bash
docker compose --env-file .env run --rm web flask db upgrade
```

### 4. Start the app services

```bash
docker compose --env-file .env up -d web worker nginx
```

> Optional (dev UI): `docker compose --env-file .env --profile dev up -d ui`

### 5. Open the app

Visit `http://localhost` (or your LAN IP / Cloudflare hostname).

## After launch

- Run app commands: `docker compose exec web flask [COMMAND]`
- Stop the stack: `docker compose down`
- Rebuild/recreate: `docker compose up -d --build`
- Restart a service: `docker compose restart web worker nginx`

## First-time data setup (recommended)

Run the following commands against your running instance using `docker compose exec`:

```bash
docker compose exec web flask fetch-scryfall-bulk --progress
docker compose exec web flask refresh-scryfall
docker compose exec web flask shell <<'PY'
from shared.jobs.jobs import run_scryfall_refresh_inline
run_scryfall_refresh_inline('rulings')
exit()
PY
docker compose exec web flask shell <<'PY'
from core.shared.utils.symbols_cache import ensure_symbols_cache
ensure_symbols_cache(force=True)
exit()
PY
docker compose exec web flask sync-spellbook-combos
```

> Note: `flask shell` does not support `-c`; use the heredoc pattern above.

## Importing custom data

### CSV / Excel import format

Recognized headers include `folder`, `name`, `set_code`, `collector_number`, `quantity`, `lang`, `foil`. Case and spacing are forgiving; the importer normalizes common variants.

- ManaBox exports are supported: `Binder Name` maps to folders and `Binder Type` (Deck/Binder) automatically sets each folder to `deck` or `collection`.
- Moxfield exports are supported: `Count` -> quantity, `Name` -> card name, `Edition` -> set code, `Language`, `Foil`, and `Proxy` are respected. Unused columns (Tags, Alter, Purchase Price, etc.) are ignored.

```csv
folder,name,set_code,collector_number,quantity,foil,lang
Collection,Sol Ring,2xm,229,1,0,en
Mono-Red,Lightning Bolt,m11,146,4,0,en
Bulk Rares,Golos, Tireless Pilgrim,m20,226,1,0,en
```

- Excel (`.xlsx`, `.xlsm`) files are supported; only the first worksheet is read.
- `quantity_mode` option (`new_only`) creates only brand-new rows. Combine with `--overwrite` when you need to wipe everything and rebuild from a fresh spreadsheet.

### Collection export

- Cards list: `/cards/export`
- Wishlist: `/wishlist/export`
- List checker results: `/list-checker/export`
- Import template: `/import/template.csv`

All exports include a UTF-8 BOM for compatibility with Excel.

## Authentication & API tokens

- Create users: `docker compose exec web flask users create USERNAME EMAIL --admin` (or use Admin -> Create User). Usernames must be unique and logins accept either email or username; passwords are prompted interactively.
- Sign in: visit `/login` to access Import/Admin links plus the account menu.
- Generate tokens: use `/account/api-token` or `docker compose exec web flask users token you@example.com` (Bearer token shown once).
- Use tokens: add `Authorization: Bearer <token>` when calling protected endpoints (query params are rejected).
- Audit trail: logins, admin actions, imports, and token rotations are stored in `audit_logs`.

## Command reference

| **Command** | **Purpose** |
| - | - |
| `flask db upgrade` | Apply database migrations. |
| `flask import-csv PATH [--dry-run] [--default-folder NAME] [--overwrite] [--quantity-mode {delta,new_only}]` | CLI importer mirroring the web importer. |
| `flask fetch-scryfall-bulk [--progress]` | Download the Scryfall `default_cards` bulk file. |
| `flask refresh-scryfall` | Load the downloaded bulk file into memory and build indexes. |
| `flask sync-spellbook-combos [--card-count N ...]` | Pull Commander Spellbook combos into `data/spellbook_combos.json`. |
| `flask repair-oracle-ids-advanced [--dry-run]` | Fill missing `oracle_id` values via Scryfall cache lookups. |
| `flask dedupe-cards` | Detect duplicate prints within folders. |
| `flask fts-ensure` | Ensure the FTS table & triggers exist. |
| `flask fts-reindex` | Rebuild the FTS index. |
| `flask analyze` | Run `ANALYZE` on the SQLite database (SQLite only). |
| `flask vacuum` | Run `VACUUM` on the SQLite database (SQLite only). |

A complete list lives near the bottom of `backend/app.py`.

## Tests

Run the test suite with:

```bash
pytest
```

CI is configured via GitHub Actions (`.github/workflows/python-tests.yml`). Extend the suite as you add features.

## Configuration

Most commonly tuned settings:

- `POSTGRES_PASSWORD` (set in `.env`)
- `SECRET_KEY` / `SECRET_KEY_FILE`
- `CACHE_TYPE` + `CACHE_REDIS_URL`
- `EDHREC_SERVICE_URL`, `PRICE_SERVICE_URL`
- `FLASK_ENV=development` for hot reload

Key environment variables (defaults in parentheses):

| **Variable** | **Default** | **Description** |
| -- | - | -- |
| `SECRET_KEY` | `"dev"` | Flask session/CSRF signing key. Replace in production. |
| `SECRET_KEY_FILE` | `""` | Path to a file containing the secret key (used when `SECRET_KEY` is unset). |
| `FLASK_ENV` | `production` | Set to `development` for debug mode + auto reload. |
| `WEB_TIMEOUT` | `180` | Gunicorn worker timeout in seconds (adjust if workloads need longer). |
| `WEB_CONCURRENCY` | `12` | Gunicorn worker processes (tune per CPU). |
| `WEB_THREADS` | `3` | Threads per worker (tune per workload). |
| `DATABASE_URL` | `postgresql+psycopg2://dvapp:<password>@pgbouncer:6432/dragonsvault` | SQLAlchemy database URI (PgBouncer in front of Postgres). |
| `SCRYFALL_DATA_DIR` | `instance/data` | Storage path for Scryfall bulk cache. |
| `UPLOAD_FOLDER` | `instance/uploads` | Temp directory for uploaded spreadsheets. |
| `MAX_CONTENT_LENGTH` | `64 * 1024 * 1024` | Upload limit in bytes (default 64 MB). |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` behind HTTPS. |
| `ALLOW_RUNTIME_INDEX_BOOTSTRAP` | `0` | Enable only if you want runtime DB bootstrap in production. |
| `CACHE_TYPE` | `RedisCache` | Cache backend (`RedisCache` recommended; set `CACHE_REDIS_URL`). |
| `CACHE_DEFAULT_TIMEOUT` | `600` | Cache TTL in seconds. |
| `CACHE_REDIS_URL` | `""` | Redis connection string (used when `CACHE_TYPE=RedisCache`). |
| `CACHE_DIR` | `instance/cache` | Filesystem cache directory (used when `CACHE_TYPE=FileSystemCache`). |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection used for rate limiting and background jobs. |
| `COMMANDER_SPELLBOOK_TIMEOUT` | `120` | Seconds to wait before spellbook downloads time out (adjust if the API is slow). |
| `PRICE_SERVICE_URL` | `""` | Internal URL for the price microservice (e.g., `http://price-service:5000`). |
| `PRICE_SERVICE_HTTP_TIMEOUT` | `3` | Seconds to wait for the price microservice. |
| `PRICE_SERVICE_CACHE_TTL` | `300` | Seconds to cache price service responses in the web/worker processes. |
| `EDHREC_SERVICE_URL` | `""` | Internal URL for the EDHREC microservice (e.g., `http://edhrec-service:5000`). |
| `EDHREC_SERVICE_HTTP_TIMEOUT` | `5` | Seconds to wait for EDHREC microservice requests. |
| `EDHREC_SERVICE_CACHE_TTL` | `600` | Seconds to cache EDHREC service responses in the web/worker processes. |
| `MTGJSON_GRAPHQL_URL` | `https://graphql.mtgjson.com/` | MTGJSON GraphQL endpoint for price-service. |
| `MTGJSON_API_TOKEN` | `""` | MTGJSON API token (required for price-service data access). |
| `PRICE_CACHE_TTL` | `43200` | Seconds to cache MTGJSON prices inside price-service. |
| `PRICE_REQUEST_TIMEOUT` | `20` | Seconds to wait on MTGJSON GraphQL requests. |
| `PRICE_PROVIDER_PREFERENCE` | `tcgplayer,cardmarket,cardkingdom,mtgstocks` | Provider priority for normalized prices. |
| `PRICE_LISTTYPE_PREFERENCE` | `retail,market` | Price list priority for normalized prices. |
| `EDHREC_CACHE_TTL_HOURS` | `72` | Hours to cache EDHREC payloads inside the edhrec-service. |
| `EDHREC_REQUEST_TIMEOUT` | `30` | Seconds to wait on EDHREC page fetches inside the edhrec-service. |
| `EDHREC_HTTP_RETRIES` | `2` | Retry count for EDHREC HTTP fetches inside the edhrec-service. |
| `EDHREC_REFRESH_CONCURRENCY` | `4` | Parallel fetches used by the EDHREC refresh endpoint. |

`.env` files are loaded automatically by `python-dotenv`.

## Project layout

```bash
DragonsVault/
+-- backend/              # Flask app + workers + microservices
|   +-- app.py            # Flask application factory + CLI commands
|   +-- config.py         # Config classes & defaults
|   +-- extensions.py     # Shared extension instances
|   +-- models/           # SQLAlchemy models
|   +-- routes/           # Blueprint routes & helpers
|   +-- services/         # CSV importer, Scryfall cache, stats helpers
|   +-- templates/        # Jinja templates
|   +-- static/           # CSS, JS, mana symbol assets
|   +-- scripts/          # Operational scripts + helpers
|   +-- migrations/       # Alembic migration scripts
|   +-- microservices/    # user-manager, card-data, folder-service, price-service, edhrec-service
|   +-- requirements.txt  # Python dependency lock
|   +-- django_api/       # Django + DRF API (migration work)
+-- frontend/             # SPA front end (Vite + React)
+-- infra/                # Docker, nginx, postgres configs
+-- instance/             # SQLite database, downloads, Jinja cache (gitignored)
+-- tests/                # Pytest suite
+-- docker-compose.yml    # Local stack definition
+-- README.md             # Project documentation
```

## Experimental Django API

The Django + DRF service runs alongside Flask at `/api-next`. It currently requires an API token (`Authorization: Bearer <token>`). Use it for migration testing while the legacy `/api` routes remain on Flask.

## Troubleshooting

| **Symptom** | **Resolution** |
| - | - |
| OperationalError: no such column: folder.category | Run `flask db upgrade`. If upgrading from a very old DB, stamp to the latest seen revision before running upgrade. |
| Scryfall-owned counts show zero | Ensure collection folders are marked as `collection` in Admin -> Folder Categories. |
| Mana symbols missing from card text | Run the symbols cache refresh (see First-time data setup) or use the Admin button. |
| Migration history references missing revisions | The repo is rebased to a single `0001_initial` migration. Delete your local DB (`instance/*.db`) and rerun `docker compose run --rm web ./backend/scripts/bootstrap.sh` (or `flask db upgrade`). |
| Gunicorn worker timeout on startup | Pre-warm inside a container: `docker compose run --rm web ./backend/scripts/bootstrap.sh`. If still slow, raise `WEB_TIMEOUT` before `docker compose up`. |
| CSV import fails with Unsupported file type | Confirm the file is `.csv`, `.xlsx`, or `.xlsm`. The importer checks extensions. |
| FTS search returns stale results | Run `flask fts-reindex`. |
| SQLite locks / slow queries | Limit concurrent writers, run `flask analyze`/`flask vacuum`, and consider migrating to PostgreSQL if concurrency needs grow. |

## License

This project is released under the Unlicense, which dedicates it to the public domain. Card data and imagery are provided courtesy of Scryfall and remain copyright Wizards of the Coast.
