#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Pre-warm database and FTS structures inside the container.
cd "$ROOT_DIR"
export FLASK_APP=app
export PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:$PYTHONPATH}"

echo "Running migrations..."
flask db upgrade

echo "Ensuring FTS indexes..."
flask fts-ensure
