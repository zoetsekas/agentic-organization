#!/bin/sh
# Entry point for the designer container.
#
# `serve` is the normal case; anything else is passed to the CLI unchanged, so
# the same image runs `compile`, `records validate` or a shell for debugging
# without a second image to keep in step.
set -eu

DB="${ORGAGENTS_DB:-/data/designer.db}"
HOST="${ORGAGENTS_HOST:-0.0.0.0}"
PORT="${ORGAGENTS_PORT:-8000}"
BASE_URL="${ORGAGENTS_BASE_URL:-http://localhost:${PORT}}"

if [ "${1:-serve}" = "serve" ]; then
    # Seeding is opt-in: a container restart must never overwrite or duplicate
    # what is already in the volume.
    if [ "${ORGAGENTS_SEED:-0}" = "1" ] && [ ! -f "$DB" ]; then
        echo "seeding a demo organization into $DB"
        python -m orgagents.cli --db "$DB" seed
    fi
    echo "designer listening on ${HOST}:${PORT} (UI at ${BASE_URL}/ui/)"
    exec python -m orgagents.cli --db "$DB" --base-url "$BASE_URL" \
        serve --host "$HOST" --port "$PORT"
fi

exec python -m orgagents.cli --db "$DB" --base-url "$BASE_URL" "$@"
