# Contributing to DragonsVault

Thanks for helping improve DragonsVault. This guide keeps contributions smooth and reviewable.

## Quick links

- Project overview: README.md
- Ops guidance: MAINTENANCE.md

## Development setup

### Option A: Docker (recommended)

```bash
cp infra/env.postgres.example .env

docker compose --env-file .env up -d postgres pgbouncer redis pgmaintenance

docker compose --env-file .env run --rm web flask db upgrade

docker compose --env-file .env up -d web worker nginx
```

Optional UI dev server:

```bash
docker compose --env-file .env --profile dev up -d ui
```

### Option B: Local Python (tests only)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
pytest -m "not ui"
```

### Frontend dev

```bash
cd frontend
npm install
npm run dev
```

## Pre-commit (recommended)

```bash
pip install pre-commit
pre-commit install
```

Run against all files when needed:

```bash
pre-commit run --all-files
```

## Tests

- Default tests: `pytest -m "not ui"`
- UI tests are marked `ui` and require Playwright + browsers.

## Code style

- Keep PRs focused and scoped to one logical change.
- Add or update tests for behavior changes.
- Update docs when you change workflows or commands.
- Do not commit secrets; use `.env` locally.

## Pull requests

- Describe the problem, approach, and any trade-offs.
- Include steps to verify the change.
- Make sure CI passes.

Thanks again for contributing.
