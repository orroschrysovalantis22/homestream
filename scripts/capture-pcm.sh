#!/usr/bin/env bash
# Writes raw 48 kHz/16-bit/stereo PCM of the capture device to stdout. Started by snapserver.
set -euo pipefail
DEVICE="${HOMESTREAM_AUDIO_DEVICE:-BlackHole 2ch}"
if [[ "$DEVICE" == "test-tone" ]]; then
  INPUT=(-re -f lavfi -i "sine=frequency=440:sample_rate=48000")
else
  INPUT=(-f avfoundation -i ":$DEVICE")
fi
exec "${HOMESTREAM_FFMPEG:-ffmpeg}" -hide_banner -loglevel error -nostdin "${INPUT[@]}" \
  -ac 2 -ar 48000 -f s16le pipe:1
