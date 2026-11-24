#!/usr/bin/env bash

set -Eeuo pipefail

# `wait -n` and empty-array expansion under `set -u` need bash 4.4+.
# macOS ships 3.2 as /bin/bash.
if (( BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4) )); then
  echo "This script requires bash 4.4 or newer (found $BASH_VERSION)." >&2
  echo "On macOS: brew install bash" >&2
  exit 1
fi

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
else
  echo "No .env found. Copy .env.example to .env, or run scripts/railway-fetch-env.sh if you have Railway access." >&2
fi
API_PORT="${API_PORT:-8001}"
APP_PORT="${APP_PORT:-5173}"
CHILD_PIDS=()

cleanup() {
  trap - EXIT INT TERM

  for pid in "${CHILD_PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done

  wait "${CHILD_PIDS[@]}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

for command_name in uv npm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

APP_DIR="$REPO_ROOT/packages/app"
# npm rewrites node_modules/.package-lock.json on every install, so a manifest
# newer than it means dependencies changed since the last install.
INSTALL_STAMP="$APP_DIR/node_modules/.package-lock.json"
if [[ ! -d "$APP_DIR/node_modules" ]]; then
  echo "Frontend dependencies are missing. Installing..."
  npm install --prefix "$APP_DIR"
elif [[ "$APP_DIR/package.json" -nt "$INSTALL_STAMP" || "$APP_DIR/package-lock.json" -nt "$INSTALL_STAMP" ]]; then
  echo "Frontend dependencies are out of date. Installing..."
  npm install --prefix "$APP_DIR"
fi

echo "Starting API at http://localhost:$API_PORT"
(
  cd "$REPO_ROOT/packages/api"
  exec uv run uvicorn src.main:app --reload --port "$API_PORT"
) &
CHILD_PIDS+=("$!")

echo "Starting app at http://localhost:$APP_PORT"
(
  cd "$REPO_ROOT/packages/app"
  export VITE_API_URL="${VITE_API_URL:-http://localhost:$API_PORT}"
  exec npm run dev -- --port "$APP_PORT" --strictPort
) &
CHILD_PIDS+=("$!")

set +e
wait -n "${CHILD_PIDS[@]}"
exit_status=$?
set -e

exit "$exit_status"
