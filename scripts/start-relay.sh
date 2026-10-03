#!/usr/bin/env bash
# Starts HomeStream from a source checkout: same as `homestream start`.
# (setup.sh creates .venv; pass --skip-checks to skip the startup checks.)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "error: Python environment missing - run ./setup.sh first" >&2; exit 1; }
cd "$ROOT"
exec "$PY" -m homestream start "$@"
