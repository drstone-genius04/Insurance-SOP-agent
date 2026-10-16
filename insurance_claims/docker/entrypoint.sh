#!/bin/sh
set -eu
mkdir -p "${DATA_DIR:-/srv/data}"
chown -R appuser:appuser "${DATA_DIR:-/srv/data}"
exec su appuser -s /bin/sh -c 'exec uvicorn app.main:app --host 0.0.0.0 --port 8000'
