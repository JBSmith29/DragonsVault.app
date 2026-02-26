# Card Data Service

Flask microservice scaffold for oracle-level Scryfall data plus global annotations.

- Base path: `/v1`
- Health: `/healthz`, `/readyz`
- Sync:
  - `POST /v1/scryfall/sync` (optional `force=1`, restricted to private allowlist unless `CARD_DATA_SYNC_TOKEN` + `X-Card-Data-Token` are configured)
  - `GET /v1/scryfall/status`
  - `GET /v1/oracles/<oracle_id>`
