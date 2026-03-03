Architecture
============

High-Level Components
---------------------

- Flask monolith for web routes, templates, API endpoints, and admin tools.
- RQ worker for background work and periodic jobs.
- Vite/React frontend (dev profile).
- Postgres + PgBouncer for persistent storage.
- Redis for caching/rate limiting/queue broker.
- Nginx as reverse proxy.

Project Layout
--------------

- ``backend/``: Flask app, services, models, templates, and microservices.
- ``frontend/``: React SPA sources and build tooling.
- ``infra/``: Docker and infrastructure configuration.
- ``tests/``: Backend and UI automated tests.

Integrations
------------

- Scryfall data and symbols cache
- Commander Spellbook combo sync
- MTGJSON pricing service
- EDHREC recommendation/metadata service
