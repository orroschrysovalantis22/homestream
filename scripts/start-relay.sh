#!/usr/bin/env bash
# Starts HomeStream: routes Mac audio into BlackHole, keeps the Mac awake,
# optionally starts Snapcast, then runs the control server in the foreground.
# Ctrl+C stops everything and restores your sound output.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "error: $*" >&2; exit 1; }

[[ -f .env ]] || die "no .env found - run ./setup.sh first"
# Load .env, letting variables already set in the environment win
# (e.g. HOMESTREAM_AUDIO_DEVICE=test-tone ./scripts/start-relay.sh).
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^[[:space:]]*([A-Z_][A-Z0-9_]*)=(.*)$ ]] || continue
  key="${BASH_REMATCH[1]}" value="${BASH_REMATCH[2]}"
  [[ -n "${!key+set}" ]] && continue
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  export "$key=$value"
done < .env

PY="$ROOT/control-server/.venv/bin/python"
[[ -x "$PY" ]] || die "Python environment missing - run ./setup.sh first"
HOMESTREAM_FFMPEG="$(command -v ffmpeg || true)"
[[ -n "$HOMESTREAM_FFMPEG" ]] || die "ffmpeg not found - run ./setup.sh first"
export HOMESTREAM_FFMPEG

DEVICE="${HOMESTREAM_AUDIO_DEVICE:-BlackHole 2ch}"
PORT="${HOMESTREAM_PORT:-8765}"
STATE="$ROOT/.homestream"
mkdir -p "$STATE"

PIDS=()
PREV_OUTPUT=""
cleanup() {
  for pid in "${PIDS[@]:-}"; do [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true; done
  if [[ -n "$PREV_OUTPUT" ]]; then
    SwitchAudioSource -t output -s "$PREV_OUTPUT" >/dev/null && echo "Sound output restored to: $PREV_OUTPUT"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# --- 1. Audio routing -------------------------------------------------------
if [[ "$DEVICE" != "test-tone" ]]; then
  if ! ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 | grep -qF "] $DEVICE"; then
    die "audio device '$DEVICE' not found. Install BlackHole with ./setup.sh (a reboot may be needed afterwards) or set HOMESTREAM_AUDIO_DEVICE in .env"
  fi
  if [[ "${HOMESTREAM_AUTO_ROUTE:-1}" == 1 ]] && command -v SwitchAudioSource >/dev/null; then
    CURRENT="$(SwitchAudioSource -t output -c)"
    if [[ "$CURRENT" != "$DEVICE" ]]; then
      SwitchAudioSource -t output -s "$DEVICE" >/dev/null
      PREV_OUTPUT="$CURRENT"
      echo "Sound output: $CURRENT -> $DEVICE (switched back when you stop the relay)"
    fi
  fi
fi

# --- 2. Permission checks (so macOS prompts appear now, not mid-workout) -----
if [[ "${HOMESTREAM_SKIP_PREFLIGHT:-0}" != 1 ]]; then
  "$PY" control-server/preflight.py || die "fix the audio capture problem above and run this again"
  echo
fi

# --- 3. Keep the Mac awake while relaying -----------------------------------
caffeinate -i -w $$ &
PIDS+=($!)

# --- 4. Optional Snapcast server --------------------------------------------
if [[ "${HOMESTREAM_SNAPCAST:-0}" == 1 ]]; then
  command -v snapserver >/dev/null || die "snapserver not found - run ./setup.sh --snapcast"
  CAPTURE="$ROOT/scripts/capture-pcm.sh"
  if [[ "$CAPTURE" == *" "* ]]; then
    # snapserver's process:// URI can't take a path with spaces.
    cp "$CAPTURE" "${TMPDIR:-/tmp}/homestream-capture-pcm.sh"
    CAPTURE="${TMPDIR:-/tmp}/homestream-capture-pcm.sh"
  fi
  mkdir -p "$STATE/snapserver"
  sed -e "s|__STATE_DIR__|$STATE|" \
      -e "s|__SNAPWEB_DIR__|$(brew --prefix)/share/snapserver/snapweb|" \
      -e "s|__CAPTURE_SCRIPT__|$CAPTURE|" \
      config/snapserver.conf.template > "$STATE/snapserver.conf"
  snapserver -c "$STATE/snapserver.conf" > "$STATE/snapserver.log" 2>&1 &
  PIDS+=($!)
  echo "Snapcast server running (stream port 1704, web 1780). Log: .homestream/snapserver.log"
fi

# --- 5. How to connect ------------------------------------------------------
tailscale_ip() {
  local cli
  for cli in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale; do
    if command -v "$cli" >/dev/null 2>&1 || [[ -x "$cli" ]]; then
      "$cli" ip -4 2>/dev/null | head -1 && return
    fi
  done
}
TS_IP="$(tailscale_ip || true)"
HOST_FOR_PHONE="${TS_IP:-$(ipconfig getifaddr en0 2>/dev/null || echo localhost)}"
URL="http://$HOST_FOR_PHONE:$PORT/#token=$HOMESTREAM_TOKEN"

echo
echo "HomeStream is starting."
echo "  On this Mac: http://localhost:$PORT/#token=$HOMESTREAM_TOKEN"
if [[ -z "$TS_IP" ]]; then
  echo "  Tailscale isn't connected, so this only works on your home Wi-Fi:"
fi
echo "  On your phone: $URL"
if command -v qrencode >/dev/null; then
  echo
  qrencode -t ANSIUTF8 -m 2 "$URL"
fi
if [[ "${HOMESTREAM_SNAPCAST:-0}" == 1 ]]; then
  echo "  Snapcast clients: connect to $HOST_FOR_PHONE (port 1704)"
fi
echo
echo "Play something on the Mac, then tap Listen on the phone. Ctrl+C to stop."
echo

# --- 6. Control server (foreground) -----------------------------------------
"$PY" control-server/main.py
