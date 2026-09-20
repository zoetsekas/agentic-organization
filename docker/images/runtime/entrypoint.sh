#!/bin/sh
# Entry point for one tenant's runtime container.
#
# `serve` is the normal case; anything else is passed to the CLI unchanged, so
# the same image runs `records validate` or a one-off command without a second
# image to keep in step. The project is not installed in the image — PYTHONPATH
# is — so the CLI is always invoked as a module.
set -eu

DB="${ORGAGENTS_DB:-/data/runtime.db}"
HOST="${ORGAGENTS_HOST:-0.0.0.0}"
PORT="${ORGAGENTS_PORT:-8000}"
BASE_URL="${ORGAGENTS_BASE_URL:-http://localhost:${PORT}}"

if [ "${1:-serve}" = "serve" ]; then
    echo "tenant runtime ${ORGAGENTS_TENANT:-unassigned} listening on ${HOST}:${PORT}"
    exec python -m orgagents.cli --db "$DB" --base-url "$BASE_URL" \
        serve --host "$HOST" --port "$PORT"
fi

exec python -m orgagents.cli --db "$DB" --base-url "$BASE_URL" "$@"
