#!/usr/bin/env sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO_ROOT"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required. Install Docker Engine or Docker Desktop first." >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose is required: the 'docker compose' command is unavailable." >&2
    exit 1
fi

if [ ! -f .env ]; then
    echo "Missing .env. Run 'cp .env.example .env' and fill in the service settings." >&2
    exit 1
fi

mkdir -p data/source data/output

if [ "${SYNTH_BUILD:-1}" = "1" ]; then
    docker compose build synth
fi

exec docker compose run --rm synth "$@"
