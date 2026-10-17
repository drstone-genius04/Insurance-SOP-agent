#!/bin/sh
set -eu
mkdir -p "${DATA_DIR:-/srv/data}"
chown -R appuser:appuser "${DATA_DIR:-/srv/data}"
# Render and other hosts inject PORT. Local Docker / Compose keep 8000.
PORT="${PORT:-8000}"
exec su appuser -s /bin/sh -c "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"
