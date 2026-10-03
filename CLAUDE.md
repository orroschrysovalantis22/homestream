# HomeStream

Streams what a computer plays (macOS, Windows, Linux) to a phone web page over Tailscale, with play/pause/skip. Python 3.10+, FastAPI, one package: `homestream/`. README.md "Technical details" has the architecture.

## Commands

```bash
pip install -e ".[dev,tray]"
python -m pytest                    # ~10 s, no audio hardware needed
ruff check --select E9,F .          # what CI enforces
HOMESTREAM_AUDIO_DEVICE=test-tone HOMESTREAM_PLAYER=dryrun homestream start   # run without audio or a player
pyinstaller packaging/homestream.spec   # build the app for this OS
tools/linux/test.sh                 # Linux end to end, in Docker
```

## Where things go

- Per-OS code sits behind one seam each: capture in `capture.py` (`open_source`), now playing and controls in `players/` (`make_controller`), start at login in `autostart.py`, keep awake and addresses in `system.py`. Import OS-only modules inside functions so the package imports on every OS.
- The phone page is one file, `web/index.html`, with no build step.

## Rules

- Never commit `.env`, `homestream.log`, tokens, real IP addresses, hostnames or personal paths. Tests use example addresses (`100.101.102.103`, `192.168.1.x`).
- HomeStream is for listening to your own computer from anywhere. Don't describe it as a way around ads or subscriptions.
- Text files: always `encoding="utf-8"` (Windows defaults to cp1252).
- The tray app may run with no console (`sys.stdout is None` on Windows) and must touch AppKit/GTK only via `on_ui_thread`.
- Audio is sent as whole MP3 frames; never cut one. Check audio changes with the `test-signal` device and `tools/analyze_test_signal.py`, not by ear.
- Commit messages: a short summary line, then what changed and why. No private data.
