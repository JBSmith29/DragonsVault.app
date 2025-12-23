# Maintenance & Troubleshooting

Use these commands from the project root (where `docker-compose.yml` lives).

## Quick Health Checks
- `docker ps` — verify containers are running.
- `docker compose ps` — check statuses and health indicators.
- Inspect last exit/error per container:  
  - `docker inspect dragonsvaultapp-web-1 --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}'`  
  - `docker inspect dragonsvaultapp-worker-1 --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}'`  
  - `docker inspect dragonsvaultapp-nginx-1 --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}'`  
  - `docker inspect dragonsvaultapp-pgmaintenance-1 --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}'`  
  - `docker inspect dragonsvaultapp-pgbouncer-1 --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}'`  
  - `docker inspect dragonsvaultapp-redis-1 --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}'`
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
  - `curl http://localhost/api/folders/v1/ping`
  - `curl http://localhost/api/prices/v1/ping`

## Logs (recent)
- `docker compose logs web --tail=200`
- `docker compose logs user-manager --tail=200`
- `docker compose logs card-data --tail=200`
- `docker compose logs folder-service --tail=200`
- `docker compose logs price-service --tail=200`
- `docker compose logs worker --tail=200`
- `docker compose logs pgbouncer --tail=200`
- `docker compose logs nginx --tail=200`
- `docker compose logs postgres --tail=200`

## Live Logs (follow)
- `docker compose logs -f web worker nginx pgbouncer postgres` — watch multiple services live.

## Restart / Recreate
- `docker compose restart web worker nginx` — fast restart of app-facing services.
- `docker compose up -d` — recreate/start everything using current images/config.

## App Maintenance Commands
Run inside the web container:
- `docker compose exec web flask fetch-scryfall-bulk --progress` — download Scryfall bulk.
- `docker compose exec web flask refresh-scryfall` — load bulk into cache/index.
- `docker compose exec web flask sync-spellbook-combos` — download Commander Spellbook combos (use `--progress/--no-progress`; bump `--concurrency` to speed up; optionally `--skip-existing` to avoid reprocessing already written combos).
- `docker compose exec web flask refresh-oracle-tags` — recompute oracle deck tags and evergreen keywords from the Scryfall cache.
- `docker compose exec web flask refresh-oracle-tags-full` — recompute oracle roles, keywords, typal tags, deck tags, and evergreen keywords.
- `docker compose exec web flask fts-ensure` — ensure FTS table and triggers exist.
- `docker compose exec web flask fts-reindex` — rebuild FTS index.
- `docker compose exec web flask analyze` — run `ANALYZE` on the DB.
- `docker compose exec web flask vacuum` — run `VACUUM` (SQLite) / maintenance helper.
- `docker compose exec web flask db upgrade` — apply migrations (after code updates).

Inline helpers (copy/paste as shown):
- `docker compose exec web flask shell <<'PY'`  
  `from services.jobs import run_scryfall_refresh_inline`  
  `run_scryfall_refresh_inline('rulings')`  
  `exit()`  
  `PY`
- `docker compose exec web flask shell <<'PY'`  
  `from services.symbols_cache import ensure_symbols_cache`  
  `ensure_symbols_cache(force=True)`  
  `exit()`  
  `PY`

Card Data service (oracle-level DB):
- Trigger sync: `curl -X POST http://localhost/api/cards/v1/scryfall/sync`
- Force re-sync: `curl -X POST http://localhost/api/cards/v1/scryfall/sync?force=1`
- Status: `curl http://localhost/api/cards/v1/scryfall/status`

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
4) If caches are stale: run `fetch-scryfall-bulk`, `refresh-scryfall`, then `sync-spellbook-combos`.  
5) If jobs stuck: restart worker (`docker compose restart worker`) and re-run the command.  
6) After code updates: `docker compose exec web flask db upgrade` and consider `fts-reindex`.
