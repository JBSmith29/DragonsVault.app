# EDHREC Service

Flask microservice that fetches and caches EDHREC commander/theme data for deck
analysis and recommendations.

- Base path: `/v1`
- Health: `/healthz`, `/readyz`
- Commander data:
  - `GET /v1/edhrec/commanders/<slug>` (query: `theme`, `force=1`, `max_age_hours`)
  - `POST /v1/edhrec/commanders` (JSON: `name`, optional `theme`, `force`, `max_age_hours`)
- Theme data:
  - `GET /v1/edhrec/themes/<slug>` (query: `force=1`, `max_age_hours`)
  - `POST /v1/edhrec/themes` (JSON: `name`, optional `force`, `max_age_hours`)
- Cache stats:
  - `GET /v1/edhrec/stats`
- Bulk refresh:
  - `POST /v1/edhrec/refresh` (JSON: `commanders`, `themes`, optional `force`, `max_age_hours`)

## Environment
- `DATABASE_URL` (required)
- `DATABASE_SCHEMA` (default: `edhrec_service`)
- `EDHREC_CACHE_TTL_HOURS` (default: `72`)
- `EDHREC_REQUEST_TIMEOUT` (seconds, default: `30`)
- `EDHREC_USER_AGENT` (default: `DragonsVault/6 (+https://dragonsvault.app)`)
- `EDHREC_HTTP_RETRIES` (default: `2`)
- `EDHREC_REFRESH_CONCURRENCY` (default: `4`)
