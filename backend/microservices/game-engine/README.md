# Game Engine Service

Flask microservice scaffold for authoritative Magic game state and rules resolution.

- Base path: `/v1`
- Health: `/healthz`, `/readyz`
- Game lifecycle:
  - `POST /v1/games` create a new game session
  - `GET /v1/games/<game_id>` fetch current game summary/state
  - `POST /v1/games/<game_id>/actions` submit a player action (stub)
  - `GET /v1/games/<game_id>/events` stream/poll events (stub)

## Deck Sync
- `POST /v1/decks/from-folder` with `{ "folder_id": 123 }` creates/refreshes a deck snapshot
- `GET /v1/decks/<deck_id>` returns the stored decklist

When submitting `load_deck` actions, you can pass `{ "deck_id": <id> }` and the
service will expand that deck into a full library payload (server-authoritative shuffle).

This service is the foundation for full rules coverage. It will own:
- Deterministic game state
- Stack/priority handling
- Card/ability resolution
- Rules enforcement and prompts for choices

## Auth
Clients should send an API token issued by DragonsVault.

Headers:
- `Authorization: Bearer <token>` (preferred)
- or `X-Api-Token: <token>`

The service validates tokens against the `users` table (configurable via
`AUTH_SCHEMA`/`AUTH_TABLE` env vars).
