#!/usr/bin/env bash
# Runs a test relay (test signal + pretend player) for the iPhone-simulator harness and prints the
# delays the page reports. Usage: tools/harness.sh [extra VAR=value ...]   e.g. HOMESTREAM_BITRATE=320k
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${HARNESS_LOG:-/tmp/homestream-harness.log}"
# Only the process listening on the port: clients (browsers, emulators) are connected to it too.
lsof -ti tcp:8766 -sTCP:LISTEN | xargs kill 2>/dev/null || true
cd "$ROOT"
sleep 0.5
env HOMESTREAM_ENV_FILE=/nonexistent HOMESTREAM_TOKEN=harness-token-0123456789 \
    HOMESTREAM_AUDIO_DEVICE=test-signal HOMESTREAM_PLAYER=dryrun HOMESTREAM_HOST=0.0.0.0 HOMESTREAM_PORT=8766 "$@" \
    nohup "$ROOT/.venv/bin/python" -m homestream serve > "$LOG" 2>&1 &
for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:8766/ && break; sleep 0.3; done
echo "test relay on :8766 ($*), log: $LOG"
