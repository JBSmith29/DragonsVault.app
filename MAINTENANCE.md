# Maintenance & Troubleshooting

Use these commands from the project root (where `docker-compose.yml` lives).
Note: this compose stack is treated as dev/staging; production uses separate deployment config.

## Stack Map (services + routing)
Compose services:
- `nginx` — reverse proxy on port 80 for `/`, `/api*`, `/api-next`, and `/static`.
- `ui` — Vite SPA dev server (proxied at `/` by nginx).
- `web` — Flask monolith (legacy UI + `/api` routes + admin) with SQLAlchemy.
- `worker` — RQ worker for background jobs and long-running tasks.
- `scheduler` — weekly refresh loop for Scryfall/Spellbook/EDHREC (queues RQ jobs or runs inline).
- `user-manager` — auth/user microservice scaffold (health + ping only right now).
- `card-data` — oracle-level Scryfall data + annotations (`card_data` schema).
- `folder-service` — folder/deck microservice scaffold (not wired in nginx yet).
- `price-service` — MTGJSON pricing normalizer (`price_service` schema).
- `edhrec-service` — EDHREC fetch + cache (`edhrec_service` schema, internal only).
- `django-api` — experimental DRF service at `/api-next`.
- `postgres`, `pgbouncer`, `redis`, `pgmaintenance` — primary data stores and maintenance loop.

Nginx routing (exact):
- `/` -> `ui`
- `/static/*` -> `backend/static` (served directly by nginx)
- `/healthz` -> nginx itself
- `/readyz` -> `web` `/ops/health` (overall readiness)
- `/api/user/*` -> `user-manager`
- `/api/cards/*` -> `card-data`
- `/api/prices/*` -> `price-service`
- `/api/folders/*` -> `web` (legacy; `folder-service` not wired yet)
- `/api-next/*` -> `django-api`
- `/api/*` -> `web` (legacy Flask API + server-rendered pages)

## Data Operations (pipelines + storage)
Storage + caching:
- Postgres is the system of record (`dragonsvault`), fronted by PgBouncer. Service schemas are created via `backend/scripts/init_service_schemas.sql` (`user_manager`, `card_data`, `folder_service`, `price_service`, `edhrec_service`); the monolith uses `public`.
- Redis DBs: `0` for RQ jobs (`REDIS_URL`), `1` for rate limiting (`RATELIMIT_STORAGE_URI`), `2` for Flask cache (`CACHE_REDIS_URL`).
- Local disk: `instance/data` for Scryfall bulk + rulings caches, `data/spellbook_combos.json` for Commander Spellbook data, `instance/cache` + `instance/jinja_cache` for filesystem and template caches.

Pipelines:
- Scryfall cache (monolith): `flask fetch-scryfall-bulk` writes `instance/data/scryfall_default_cards.json`; `flask refresh-scryfall` loads in-memory indexes; `run_scryfall_refresh_inline('rulings')` writes `instance/data/scryfall_rulings.json`.
- Card-data oracle sync: `POST /api/cards/v1/scryfall/sync` downloads Scryfall bulk, collapses prints into oracle rows, and upserts into `card_data` tables. Uses `SCRYFALL_DATA_DIR` (default `/tmp/scryfall`) and honors `?force=1`.
- Commander Spellbook combos: `flask sync-spellbook-combos` writes `data/spellbook_combos.json` (or `SCRYFALL_DATA_DIR`); used by commander bracket scoring and deck views.
- Oracle tagging/roles: `flask refresh-oracle-tags` and `flask refresh-oracle-tags-full` rebuild tag tables from the Scryfall cache; `flask refresh-card-roles` recomputes roles from card rows when the cache is missing.
- Pricing: price-service fetches MTGJSON GraphQL and caches in `price_service.print_prices` (TTL `PRICE_CACHE_TTL`); web/worker cache service responses for `PRICE_SERVICE_CACHE_TTL`.
- EDHREC: edhrec-service fetches EDHREC data and caches JSON payloads in `edhrec_service`; web/worker call it via `EDHREC_SERVICE_URL`.
- FTS: `flask fts-ensure` creates FTS tables/triggers; `flask fts-reindex` rebuilds after large data changes.
- Postgres maintenance: `pgmaintenance` runs `vacuumdb --all --analyze-in-stages` weekly; `flask vacuum` only applies to SQLite deployments.
- Weekly refresh scheduler: `scheduler` runs every Sunday at 00:00 UTC by default (set `SCHEDULE_REFRESH_TZ`, `SCHEDULE_REFRESH_WEEKDAY`, `SCHEDULE_REFRESH_HOUR`, `SCHEDULE_REFRESH_MINUTE` to change; `SCHEDULE_REFRESH_MODE=rq|inline`, `SCHEDULE_REFRESH_ENABLED=0` to disable; ensure `worker` is running when using `rq` mode).

## Quick Health Checks
- `docker ps` — verify containers are running.
- `docker compose ps` — check statuses and health indicators.
- Inspect last exit/error per container (replace `<service>`):  
  - `docker inspect $(docker compose ps -q <service>) --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}'`  
  - Core: `web`, `worker`, `scheduler`, `nginx`, `ui`, `postgres`, `pgbouncer`, `redis`, `pgmaintenance`  
  - Microservices: `user-manager`, `card-data`, `folder-service`, `price-service`, `edhrec-service`, `django-api`
- `curl http://localhost/healthz` — nginx health endpoint.
- `curl http://localhost/readyz` — overall readiness (web aggregates all services).
- `curl http://localhost/api-next/healthz` — django-api health (if enabled).
- `curl http://localhost/api/ops/health` — overall readiness (JSON detail).
- `curl http://localhost/metrics` — Prometheus metrics (queue depth + app counts; use `?format=json` for JSON).
- `docker compose exec web python - <<'PY'`  
  `import urllib.request; req=urllib.request.Request('http://localhost:5000/healthz', headers={'X-Forwarded-Proto':'https'});`  
  `resp=urllib.request.urlopen(req, timeout=5); print(resp.status, resp.read().decode())`  
  `PY` — app health endpoint (bypasses HTTPS redirect).
- `docker compose exec worker python - <<'PY'`  
  `import redis; print(redis.Redis.from_url('redis://redis:6379/0').ping())`  
  `PY` — confirm Redis connectivity from the worker.
- Microservice pings (through nginx):  
  - `curl http://localhost/api/user/v1/ping`  
  - `curl http://localhost/api/cards/v1/ping`  
  - `curl http://localhost/api/prices/v1/ping`
- Internal-only ping (not exposed via nginx):  
  - `docker compose exec edhrec-service curl -s http://localhost:5000/v1/ping`
- Folder APIs (`/api/folders/*`) are currently served by the monolith and require auth (no public ping).

## Logs (recent)
- `docker compose logs web --tail=200`
- `docker compose logs user-manager --tail=200`
- `docker compose logs card-data --tail=200`
- `docker compose logs folder-service --tail=200`
- `docker compose logs price-service --tail=200`
- `docker compose logs edhrec-service --tail=200`
- `docker compose logs django-api --tail=200`
- `docker compose logs ui --tail=200`
- `docker compose logs worker --tail=200`
- `docker compose logs scheduler --tail=200`
- `docker compose logs redis --tail=200`
- `docker compose logs pgbouncer --tail=200`
- `docker compose logs nginx --tail=200`
- `docker compose logs postgres --tail=200`
- `docker compose logs pgmaintenance --tail=200`

## Observability
- Logs are JSON lines with `request_id`, `path`, and `method` fields; responses echo `X-Request-ID`.
- `/metrics` returns Prometheus text; add `?format=json` when you need the legacy JSON payload.
- Queue metrics default to the `default` RQ queue; set `RQ_QUEUES=default,high,low` to include others.
- Static CDN support: set `STATIC_ASSET_BASE_URL=https://cdn.example.com/static` (Django uses `STATIC_URL` if needed).
- Overall health probes `USER_MANAGER_URL`, `CARD_DATA_URL`, `FOLDER_SERVICE_URL`, `PRICE_SERVICE_URL`, `EDHREC_SERVICE_URL`, `DJANGO_API_URL` (defaults to docker service names in compose).

## Live Logs (follow)
- `docker compose logs -f web worker nginx pgbouncer postgres redis` — watch core services live.
- `docker compose logs -f scheduler user-manager card-data folder-service price-service edhrec-service django-api ui` — watch API/UI services live.

## Restart / Recreate
- `docker compose restart web worker nginx` — fast restart of app-facing services.
- `docker compose restart scheduler user-manager card-data folder-service price-service edhrec-service django-api ui` — restart API/UI services.
- `docker compose up -d` — recreate/start everything using current images/config.

## App Maintenance Commands
Run inside the web container:
- `docker compose exec web flask fetch-scryfall-bulk --progress` — download Scryfall bulk.
- `docker compose exec web flask refresh-scryfall` — load bulk into cache/index.
- `docker compose exec web flask sync-spellbook-combos` — download Commander Spellbook combos (use `--progress/--no-progress`; bump `--concurrency` to speed up; optionally `--skip-existing` to avoid reprocessing already written combos).
- `docker compose exec web flask refresh-oracle-tags` — recompute oracle core roles and evergreen tags from the Scryfall cache.
- `docker compose exec web flask refresh-oracle-tags-full` — recompute oracle roles, keywords, typal tags, core roles, deck tags, and evergreen tags.
- `docker compose exec web flask refresh-card-roles` — recompute card roles from oracle text (uses cache if available).
- `docker compose exec web flask fts-ensure` — ensure FTS table and triggers exist.
- `docker compose exec web flask fts-reindex` — rebuild FTS index.
- `docker compose exec web flask cache-stats` — Scryfall cache status (prints + rulings).
- `docker compose exec web flask rulings-stats` — Scryfall rulings file status.
- `docker compose exec web flask analyze` — run `ANALYZE` on the DB.
- `docker compose exec web flask vacuum` — run `VACUUM` (SQLite only).
- `docker compose exec web flask db upgrade` — apply migrations (after code updates).
- `docker compose exec web flask repair-oracle-ids-advanced --dry-run` — preview/fix missing `oracle_id` values.

Inline helpers (copy/paste as shown):
- `docker compose exec web flask shell <<'PY'`  
  `from shared.jobs.jobs import run_scryfall_refresh_inline`  
  `run_scryfall_refresh_inline('rulings')`  
  `exit()`  
  `PY`
- `docker compose exec web flask shell <<'PY'`  
  `from core.shared.utils.symbols_cache import ensure_symbols_cache`  
  `ensure_symbols_cache(force=True)`  
  `exit()`  
  `PY`

Card Data service (oracle-level DB):
- Trigger sync: `curl -X POST http://localhost/api/cards/v1/scryfall/sync`
- Force re-sync: `curl -X POST http://localhost/api/cards/v1/scryfall/sync?force=1`
- Status: `curl http://localhost/api/cards/v1/scryfall/status`
- Oracle detail: `curl http://localhost/api/cards/v1/oracles/<oracle_id>`

## Job Queue / Background Tasks
- `docker compose exec worker rq info` — inspect RQ queues.
- `docker compose logs worker --tail=200` — check for job failures.
- `docker compose exec worker rq requeue failed` — retry failed jobs (if appropriate).

## Database / PgBouncer
- `docker compose exec postgres pg_isready -U dvapp -d dragonsvault` — database readiness.
- `docker compose exec postgres psql -U dvapp -d dragonsvault -c "select now();"` — quick query check.
- Create service schemas (once): `docker compose exec -T postgres psql -U dvapp -d dragonsvault < backend/scripts/init_service_schemas.sql`
- `docker compose logs pgbouncer --tail=200` — connection pool issues.

## Nginx / Networking
- `docker compose logs nginx --tail=200` — TLS/redirect/gzip/static issues.
- If health checks fail with redirects, ensure `ENABLE_TALISMAN=1` and probes send `X-Forwarded-Proto: https` (health checks in compose already do this).

## Disk Space / Cache Size
- `df -h` — host disk usage.
- `docker system df` — images/volumes usage.
- `docker compose exec web du -sh /app/instance/data` — Scryfall cache size.
- `docker compose exec web du -sh /app/data` — app data (if present).

## After Pulling Updates
- `docker compose pull` — fetch new images (if using registry-built images).
- `docker compose up -d --build` — rebuild/recreate services with latest code.
- `docker compose exec web flask db upgrade` — run migrations.
- `docker compose exec web flask db current` — confirm migration head.
- Consider `docker compose exec web flask fts-reindex` after large data changes.

## Common Recovery Steps
1) Check container status (`docker ps`, `docker compose ps`).  
2) Tail web/worker logs for tracebacks.  
3) Confirm DB/Redis: `pg_isready`, Redis ping from worker.  
4) If caches are stale: run `fetch-scryfall-bulk`, `refresh-scryfall`, then `sync-spellbook-combos` (and `/api/cards/v1/scryfall/sync` if the card-data service is in use).  
5) If jobs stuck: restart worker (`docker compose restart worker`) and re-run the command.  
6) After code updates: `docker compose exec web flask db upgrade` and consider `fts-reindex`.
