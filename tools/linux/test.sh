#!/usr/bin/env bash
# Build the Linux test container from the working tree and run the end-to-end test in it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTEXT="$(mktemp -d)"
trap 'rm -rf "$CONTEXT"' EXIT
# The working tree without local-only files (.venv, .git, .env, caches).
tar -C "$ROOT" --exclude=.venv --exclude=.git --exclude=.env --exclude=.homestream \
    --exclude=__pycache__ --exclude='*.egg-info' --exclude=.pytest_cache -cf - . | tar -C "$CONTEXT" -xf -
docker build -q -t homestream-linux-test -f "$CONTEXT/tools/linux/Dockerfile" "$CONTEXT" >/dev/null
docker run --rm homestream-linux-test
