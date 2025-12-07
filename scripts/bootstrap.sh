#!/usr/bin/env bash
set -euo pipefail

# Pre-warm database and FTS structures inside the container.
export FLASK_APP=app

echo "Running migrations..."
flask db upgrade

echo "Ensuring FTS indexes..."
flask fts-ensure
