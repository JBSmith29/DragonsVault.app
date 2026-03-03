Getting Started
===============

Prerequisites
-------------

- Docker (Desktop or Engine)
- Git (optional)

Quickstart
----------

1. Clone the repository.
2. Start Postgres, PgBouncer, Redis, and maintenance services.
3. Apply migrations.
4. Start application services.

.. code-block:: bash

   git clone https://github.com/JBSmith29/DragonsVault.git
   cd DragonsVault

   cp infra/env.postgres.example .env
   docker compose --env-file .env up -d postgres pgbouncer redis pgmaintenance
   docker compose --env-file .env run --rm web flask db upgrade
   docker compose --env-file .env up -d web worker nginx

Open ``http://localhost`` after services are healthy.

First-Time Data Setup
---------------------

Run these after the stack is running to prime card/rules/symbol data.

.. code-block:: bash

   docker compose exec web flask fetch-scryfall-bulk --progress
   docker compose exec web flask refresh-scryfall
   docker compose exec web flask sync-spellbook-combos
