#!/usr/bin/env bash

set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
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

if [[ ! -d "$REPO_ROOT/packages/app/node_modules" ]]; then
  echo "Frontend dependencies are missing. Run: npm install --prefix packages/app" >&2
  exit 1
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
