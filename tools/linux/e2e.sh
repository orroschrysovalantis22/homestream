#!/usr/bin/env bash
# End-to-end test inside the tools/linux container: a real player (mpv) plays the beep
# test signal to PulseAudio; HomeStream records the speakers' monitor, streams it, and
# controls mpv over MPRIS. Prints PASS/FAIL per check; exits non-zero on any failure.
set -euo pipefail
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  export XDG_RUNTIME_DIR=/tmp/xdg-runtime
  mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
  exec dbus-run-session -- "$0" "$@"
fi
cd "$(dirname "$0")/../.."
FAILED=0
expect() { # expect "what" actual expected
  if [[ "$2" == "$3" ]]; then echo "PASS  $1"; else echo "FAIL  $1 (got: '$2', wanted: '$3')"; FAILED=1; fi
}
TOKEN=linux-e2e-token-0123456789
H="Authorization: Bearer $TOKEN"
API=http://127.0.0.1:8765

pulseaudio --start --exit-idle-time=-1 >/dev/null 2>&1
pactl load-module module-null-sink sink_name=speakers sink_properties=device.description=Speakers >/dev/null
pactl set-default-sink speakers

SIGNAL="0.2*sin(2*PI*440*t)*lt(mod(t\,1)\,0.9)+0.5*sin(2*PI*1000*t)*lt(mod(t\,1)\,0.05)"
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "aevalsrc=$SIGNAL:s=48000" -t 120 -ac 2 \
  -metadata title="Linux Test Signal" -metadata artist="HomeStream" -metadata album="Beeps" /tmp/one.mp3
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "aevalsrc=$SIGNAL:s=48000" -t 120 -ac 2 \
  -metadata title="Second Track" -metadata artist="HomeStream" /tmp/two.mp3
mpv --no-video --no-terminal --loop-playlist /tmp/one.mp3 /tmp/two.mp3 &
sleep 2

echo "--- homestream doctor"
.venv/bin/homestream doctor || true
echo "---"

HOMESTREAM_TOKEN=$TOKEN HOMESTREAM_HOST=127.0.0.1 HOMESTREAM_TRUST_TAILSCALE=0 \
  .venv/bin/homestream serve > /tmp/server.log 2>&1 &
for _ in $(seq 1 30); do curl -s -o /dev/null "$API/" && break; sleep 0.5; done

player() { curl -s -H "$H" "$API/status" | python3 -c "import json,sys; print(json.load(sys.stdin)['player']['$1'])"; }

sleep 1
expect "backend is MPRIS" "$(player backend)" mpris
expect "now playing title comes from mpv" "$(player title)" "Linux Test Signal"
expect "state is playing" "$(player state)" playing
expect "app is mpv" "$(player app)" mpv

curl -s -m 10 -H "$H" "$API/stream.mp3?id=linux" -o /tmp/stream.mp3 -w "stream: HTTP %{http_code}, %{size_download} bytes\n" || true
touch /tmp/stream.mp3
ffmpeg -hide_banner -loglevel error -y -i /tmp/stream.mp3 -ac 1 -ar 44100 -f s16le /tmp/stream.raw || true
echo "--- what HomeStream streamed (from the speakers' monitor)"
.venv/bin/python tools/analyze_test_signal.py /tmp/stream.raw > /tmp/analysis.txt || true
grep -E "seconds|beeps|interval|jumps|dropouts" /tmp/analysis.txt
expect "stream audio is clean (beeps 1000 ms apart, no gaps)" "$(grep -c "verdict: CLEAN" /tmp/analysis.txt)" 1

curl -s -X POST -H "$H" "$API/pause" >/dev/null; sleep 1
expect "pause from the phone pauses mpv" "$(playerctl status)" Paused
curl -s -X POST -H "$H" "$API/play" >/dev/null; sleep 1
expect "play resumes mpv" "$(playerctl status)" Playing
curl -s -X POST -H "$H" "$API/next" >/dev/null; sleep 1.5
expect "next skips to the second track" "$(player title)" "Second Track"
curl -s -X POST -H "$H" "$API/prev" >/dev/null; sleep 1.5
expect "previous goes back" "$(player title)" "Linux Test Signal"
expect "page is served" "$(curl -s -o /dev/null -w "%{http_code}" "$API/")" 200

if [[ $FAILED -ne 0 ]]; then echo "--- server log"; tail -30 /tmp/server.log; fi
exit $FAILED
