# Card Data Service

Flask microservice scaffold for oracle-level Scryfall data plus global annotations.

- Base path: `/v1`
- Health: `/healthz`, `/readyz`
- Sync:
  - `POST /v1/scryfall/sync` (optional `force=1`)
  - `GET /v1/scryfall/status`
  - `GET /v1/oracles/<oracle_id>`
