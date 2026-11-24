#!/usr/bin/env bash
#
# Write a local .env from the variables of a Railway service.
#
# For maintainers with access to the project's Railway account. Everyone else
# should copy .env.example to .env and fill in their own keys.
#
# Usage: scripts/railway-fetch-env.sh [--force]
#
# The Railway project is the one linked to this directory (`railway link`), or
# the one named by RAILWAY_PROJECT_ID. Override the service and environment
# with RAILWAY_SERVICE and RAILWAY_ENVIRONMENT.
#
# Variable values are never printed, only their names.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
SERVICE="${RAILWAY_SERVICE:-autogkb-api}"
ENVIRONMENT="${RAILWAY_ENVIRONMENT:-production}"
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help)
      sed -n '3,14s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

fallback_hint() {
  echo "No Railway access? Copy .env.example to .env and set your own keys." >&2
}

for command_name in railway python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    fallback_hint
    exit 1
  fi
done

if [[ -e "$ENV_FILE" && "$FORCE" -ne 1 ]]; then
  echo "$ENV_FILE already exists. Re-run with --force to overwrite it." >&2
  exit 1
fi

railway_args=(variable list --service "$SERVICE" --environment "$ENVIRONMENT" --json)
if [[ -n "${RAILWAY_PROJECT_ID:-}" ]]; then
  railway_args+=(--project "$RAILWAY_PROJECT_ID")
fi

umask 077
error_log="$(mktemp)"
tmp_env="$(mktemp)"
trap 'rm -f "$error_log" "$tmp_env"' EXIT

if ! variables_json="$(railway "${railway_args[@]}" 2>"$error_log")"; then
  echo "Could not read variables for service '$SERVICE' ($ENVIRONMENT) from Railway:" >&2
  sed 's/^/  /' "$error_log" >&2
  echo "Log in with 'railway login', then run 'railway link' here or set RAILWAY_PROJECT_ID." >&2
  fallback_hint
  exit 1
fi

read -r -d '' WRITE_ENV <<'PY' || true
import json
import sys

out_path, service, environment = sys.argv[1:4]
variables = json.load(sys.stdin)

# Deployment-only settings that make no sense on a developer machine.
# VITE_API_URL is left out so dev-server.sh points the app at the local API.
SKIP = {"NO_CACHE", "VITE_API_URL"}


def quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


sealed = sorted(k for k, v in variables.items() if v is None)
values = {
    k: v
    for k, v in variables.items()
    if v is not None and not k.startswith("RAILWAY_") and k not in SKIP
}

# Railway's DATABASE_URL uses a private hostname that only resolves inside
# Railway, so use the public proxy URL locally.
uses_public_db = bool(values.get("DATABASE_PUBLIC_URL"))
if uses_public_db:
    values["DATABASE_URL"] = values["DATABASE_PUBLIC_URL"]

lines = [f"# Fetched by scripts/railway-fetch-env.sh from Railway service {service} ({environment})."]
if "DATABASE_URL" in values:
    lines.append(f"# DATABASE_URL points at the {environment} database.")
lines += [f"{k}={quote(v)}" for k, v in sorted(values.items())]

with open(out_path, "w") as f:
    f.write("\n".join(lines) + "\n")

print("Variables written:")
for k in sorted(values):
    print(f"  {k}")
if sealed:
    print("Sealed in Railway, set these by hand: " + ", ".join(sealed))
if "DATABASE_URL" in values:
    print()
    print(f"WARNING: DATABASE_URL points at the {environment} database.")
    print("Anything you create, edit or delete locally changes that data.")
PY

printf '%s' "$variables_json" | python3 -I -c "$WRITE_ENV" "$tmp_env" "$SERVICE" "$ENVIRONMENT"
mv "$tmp_env" "$ENV_FILE"
echo
echo "Wrote $ENV_FILE"
