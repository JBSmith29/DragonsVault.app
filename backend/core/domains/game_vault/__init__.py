"""Game Vault — a fully self-contained Magic game logger.

This domain is intentionally independent of the rest of the application:

* Its tables (all prefixed ``gv_``) carry **no foreign keys into any existing
  app table**. The only link to the host app is a plain, un-constrained
  ``owner_user_id`` integer used to scope a vault to the signed-in account.
* It never imports from other ``core.domains`` packages. It relies solely on
  shared infrastructure (the SQLAlchemy ``db`` handle, the HTTP client, and
  Flask-Login for access control).
* It ships its own blueprint, services, templates, CSS and JS.

The practical upshot: you can delete this package and drop the ``gv_`` tables
without touching the rest of the app, and vice-versa.
"""
