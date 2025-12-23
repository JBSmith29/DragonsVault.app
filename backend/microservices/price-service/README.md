# Price Service

Flask microservice that fetches print pricing from MTGJSON GraphQL and normalizes it
into DragonsVault's price keys.

- Base path: `/v1`
- Health: `/healthz`, `/readyz`
- Prices:
  - `GET /v1/prices/<scryfall_id>` (optional `force=1` to bypass cache)

## Environment
- `MTGJSON_GRAPHQL_URL` (default: `https://graphql.mtgjson.com/`)
- `MTGJSON_API_TOKEN` (required for MTGJSON data access)
- `PRICE_CACHE_TTL` (seconds, default: `43200`)
- `PRICE_REQUEST_TIMEOUT` (seconds, default: `20`)
- `PRICE_PROVIDER_PREFERENCE` (comma list, default: `tcgplayer,cardmarket,cardkingdom,mtgstocks`)
- `PRICE_LISTTYPE_PREFERENCE` (comma list, default: `retail,market`)
