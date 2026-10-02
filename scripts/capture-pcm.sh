#!/usr/bin/env bash
# Writes raw 48 kHz/16-bit/stereo PCM of the capture device to stdout. Started by snapserver.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${HOMESTREAM_PYTHON:-$ROOT/control-server/.venv/bin/python}"
FFMPEG="${HOMESTREAM_FFMPEG:-ffmpeg}"
DEVICE="${HOMESTREAM_AUDIO_DEVICE:-BlackHole 2ch}"
if [[ "$DEVICE" == test-tone || "$DEVICE" == test-signal ]]; then
  exec "$FFMPEG" -hide_banner -loglevel error -nostdin -re -f lavfi -i "sine=frequency=440:sample_rate=48000" \
    -ac 2 -ar 48000 -f s16le pipe:1
fi
# PortAudio capture (ffmpeg's own macOS capture drops audio), resampled to what snapserver expects.
RATE="$("$PY" "$ROOT/control-server/capture.py" --info "$DEVICE")"
"$PY" "$ROOT/control-server/capture.py" "$DEVICE" |
  exec "$FFMPEG" -hide_banner -loglevel error -f s16le -ar "$RATE" -ac 2 -i pipe:0 -ac 2 -ar 48000 -f s16le pipe:1
