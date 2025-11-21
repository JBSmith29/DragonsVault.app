# DragonsVault v6 – Code Review & Recommendations

This review focuses on correctness, security, performance, and maintainability while keeping your current UI (hamburger drawer + theme toggle) intact.

## TL;DR (Top Priorities)

1. **Scryfall bulk cache robustness** *(Implemented)* – Shared `requests.Session`, retry/backoff, TLS verification, and persisted ETags prevent redundant downloads; CLI/Admin refreshes now report "already current" when 304s occur.
2. **SQLite PRAGMAs & event listeners** *(Implemented)* – A single `_apply_sqlite_pragmas` helper now runs the WAL/foreign-key tuning only for sqlite3 connections.
3. **CSRF protection for forms** – `/import`, `/admin`, `/list-checker` still accept POST without CSRF; wire up `Flask-WTF`’s `CSRFProtect` and add `{{ csrf_token() }}` to each POST form.
4. **Model vs migration drift** *(Implemented)* – `models/card.py` exposes `type_line`, `rarity`, `color_identity`, and `color_identity_mask`, and imports hydrate those fields so routes no longer rely on `hasattr` checks.
5. **Config hardening** – Provide a safe dev DB URI (`sqlite:///instance/app.db`) and enforce `SECRET_KEY` for production deployments.
6. **Cache backend** *(Implemented)* – Cache configuration now comes from Flask settings (`CACHE_TYPE`, `CACHE_DEFAULT_TIMEOUT`, `CACHE_DIR`, `CACHE_REDIS_URL`) so prod can switch to Redis/FileSystem caches while dev keeps `SimpleCache`.
7. **Static JS duplication** *(Implemented)* – `static/js/theme-toggle.js` initializes once (guarded IIFE + localStorage version), eliminating double-binding flicker.

## Potential Updates & Upgrades (Nov 2025 Review)

1. **Observability & health endpoints** *(Implemented)* – Added `/healthz`, `/readyz`, and `/metrics` in `routes/ops.py`; structured JSON logging now includes `request_id` headers, and every response echoes `X-Request-ID` for tracing.
2. **Background jobs for heavy work** *(Implemented)* - CSV imports and Scryfall refreshes now enqueue on a Redis/RQ queue (`enqueue_csv_import`, `enqueue_scryfall_refresh`) so requests return immediately while workers emit progress over the existing websocket channel.
3. **Offline-first Scryfall search** *(Implemented)* - `/scryfall` now consults the local Scryfall bulk cache first (`search_local_cards`) and only falls back to the live API if needed; remote calls keep TLS verification enabled.
4. **Authentication & multi-user support** *(Implemented)* – Added Flask-Login sessions + API tokens, guarded `/admin` + `/import`, folder/user ownership links with ACL enforcement, and audit logs for privileged actions.
5. **Componentize filter/dropdown UI** *(Implemented)* - Added a shared `per_page_select` macro so Cards/Scryfall reuse identical `dv-select` markup; future tweaks to the dropdown or filter chips happen in one place.
6. **Expand automated tests** *(Implemented)* - Added isolated pytest fixtures + factories, service tests for CSV importer events, live-update queues, EDHREC + Spellbook helpers, and a Playwright regression that ensures the Scryfall drawer behaves like the Cards view.

## Findings & Fixes

### 1) App factory & configuration (`app.py`, `config.py`) *(Implemented)*
- `config._select_config()` now refuses to boot in production when `SECRET_KEY` is unset/`"dev"`, preventing accidental deployments with insecure cookies.
- `create_app()` still seeds a default SQLite URI when none is provided, but runtime schema creation (`db.create_all`, helper backfills, FTS setup) now only runs in debug or when `ALLOW_RUNTIME_INDEX_BOOTSTRAP=1`, so production servers rely on migrations instead of on-boot DDL.
- CSP work remains a future enhancement once static hosting allowances are cataloged.

### 2) CSRF protection (forms)
- Add `Flask-WTF`’s `CSRFProtect` extension and include `{{ csrf_token() }}` in every POST form (`/admin`, `/import`, `/list-checker`, folder actions). This protects against cross-site POSTs, especially once auth arrives.

### 3) Frontend polish (lazy media, reduced motion) *(Implemented)*
- Offcanvas card art images in Cards, Scryfall Browser, and folder drawers now use `loading="lazy"` so large previews don’t block initial paint.
- `prefers-reduced-motion` is respected for tile and opening-hand hover states, disabling translate/transition effects when users opt out of motion.
- The List Checker HTMX form triggers with `hx-trigger="input changed delay:300ms"`, debouncing live updates while users paste or type lists.

### 4) CSV import UX & safety *(Implemented)*
- Header validation now raises a friendly error listing which required columns (“Card Name”, “Set Code”, “Collector Number”) are missing; the `/import` preview/confirm flow catches it and flashes actionable guidance instead of a stack trace, and the CLI surfaces the same message via `click.ClickException`.
- The importer logs a structured diff summary (added/updated/skipped/errors plus top folders) for every run, complementing the on-screen flash messages and making CLI/cron usage auditable.

### 5) Pagination & performance (Cards/Scryfall) *(Implemented)*
- `/cards` now uses SQLAlchemy’s `paginate()` helper (with the loader-only column list) so templates receive a real `pagination` object alongside `page`, `pages`, etc.; manual start/end math and custom limit/offset logic are gone.
- Collector-number sorting uses the numeric portion plus the suffix (`_collector_number_numeric`, lowercase CN) so `001b` and `10a` order naturally, and set sorts inherit that tie-breaker.

### 6) Security hardening *(Implemented)*
- `Flask-Talisman` now wraps the app (configurable via `ENABLE_TALISMAN`) with a sane default CSP, HSTS, and cookie hardening tied to the production config; `SESSION_COOKIE_SECURE` is forced on in production.
- `Flask-Limiter` is initialised globally, with `/import` and `/admin` POST actions capped (5/min and 15/min respectively) and defaults set via `RATELIMIT_DEFAULT`/`RATELIMIT_STORAGE_URI`.

### 7) Repo hygiene & env separation *(Implemented)*
- `.gitignore` already covered `venv/`, `__pycache__/`, and `instance/`; added a simple `requirements-dev.txt` (pytest, ruff, coverage) so tooling installs stay separate from runtime dependencies.
