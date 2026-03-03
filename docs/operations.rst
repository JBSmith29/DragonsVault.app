Operations
==========

Common Commands
---------------

.. list-table::
   :header-rows: 1

   * - Command
     - Purpose
   * - ``hatch run test``
     - Run backend pytest suite (excluding UI-marked tests).
   * - ``hatch run frontend-ci``
     - Install frontend deps, build app, and run frontend tests.
   * - ``docker compose exec web flask db upgrade``
     - Apply Alembic migrations.
   * - ``docker compose exec web flask fts-reindex``
     - Rebuild full-text search index.

Build Documentation
-------------------

.. code-block:: bash

   python -m pip install "virtualenv<21" hatch
   hatch run docs-build

HTML output is written to ``docs/_build/html``.

Serve Docs Locally
------------------

.. code-block:: bash

   hatch run docs-serve

Open ``http://localhost:8000``.

Publishing
----------

The repository includes ``.github/workflows/docs.yml`` to build and publish
Sphinx output to GitHub Pages on pushes to ``main``.
