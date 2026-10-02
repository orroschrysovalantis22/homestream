# HomeStream

[![CI](https://github.com/orroschrysovalantis22/homestream/actions/workflows/ci.yml/badge.svg)](https://github.com/orroschrysovalantis22/homestream/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![macOS](https://img.shields.io/badge/macOS-13%2B-lightgrey?logo=apple)

Listen to whatever your home Mac is playing from your phone, anywhere, and control it: play, pause, skip.

Play Spotify Web, YouTube or anything else on the Mac. Open one page on your phone over cellular data and you hear it, with real buttons and lock-screen controls. Nothing is exposed to the public internet. The phone reaches the Mac over [Tailscale](https://tailscale.com), a free private network.

<p align="center"><img src="docs/screenshot.png" width="620" alt="The HomeStream phone page in dark and light mode: now-playing card, a big Listen button, and previous / play-pause / next controls"></p>

```
 Mac (home)                                                   Phone (anywhere)
┌────────────────────────────────────────────────┐            ┌──────────────────────┐
│ Browser / Spotify ──► BlackHole (virtual out)  │            │                      │
│                           │                    │  Tailscale │  one web page:       │
│                           ▼                    │ ◄────────► │   • Listen (MP3)     │
│ control server (FastAPI) ◄── ffmpeg capture    │  private   │   • ⏮ ⏯ ⏭             │
│   GET /stream.mp3   POST /play /pause /next …  │  network   │   • lock-screen keys │
│   └─► media keys / AppleScript ──► the player  │            │                      │
└────────────────────────────────────────────────┘            └──────────────────────┘
```

* **Any phone, no app.** The audio is a plain HTTP MP3 stream, so it plays in Safari or Chrome and keeps playing with the screen locked.
* **One address.** The page, the audio and the controls are all served by the same small server on port 8765.
* **Remote control** of the Spotify desktop app (AppleScript, with track info and artwork) or of any browser player (system media keys).
* **Private by default.** A shared token guards every route, and Tailscale keeps the server off the public internet.
* **Lazy capture.** ffmpeg only runs while someone is listening.
* Optional **Snapcast** server, for the Snapdroid Android app or multi-room setups.

## Requirements

* macOS 13+ (Apple Silicon or Intel) with [Homebrew](https://brew.sh)
* A Tailscale account (free), with the Tailscale app on the Mac and on your phone

## Setup

```bash
git clone https://github.com/orroschrysovalantis22/homestream.git && cd homestream
./setup.sh
```

`setup.sh` is safe to re-run. It:

1. installs `ffmpeg`, `switchaudio-osx` and `qrencode` with Homebrew
2. offers to install [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole), the virtual sound card that carries the Mac's audio to the relay. It's a driver, so macOS asks for your password and you may need to restart.
3. creates a Python environment in `control-server/.venv`
4. writes `.env` with a random access token
5. offers to install Tailscale and tells you when you still need to sign in

Then install Tailscale on your phone and sign in with the same account.

## Use

```bash
./scripts/start-relay.sh
```

It switches the Mac's sound output to BlackHole and checks permissions, so any macOS prompts appear now, while you're at the Mac. Then it keeps the Mac awake and prints a QR code. Scan the code with your phone. It opens the page already signed in, and you can save it to your home screen. Play something on the Mac and tap **Listen**. Press Ctrl+C to stop; your normal sound output comes back.

### macOS permissions (asked once)

`start-relay.sh` triggers each of these on startup and tells you what's missing:

| Permission | For your terminal app | Why |
|---|---|---|
| **Microphone** | click Allow on the prompt | ffmpeg reads the BlackHole input device. Required. |
| **Accessibility** | switch it on in the System Settings window that opens | sends the play/pause/next keys to browser players |
| **Automation → Spotify** | click OK on the prompt | only when controlling the Spotify desktop app |

Changes take effect after you restart the terminal. Set `HOMESTREAM_SKIP_PREFLIGHT=1` to skip the checks.

### Choosing what to control

Set `HOMESTREAM_PLAYER` in `.env`:

| Value | Controls | Track info | Notes |
|---|---|---|---|
| `auto` (default) | Spotify app if it's running, else media keys | when using Spotify app | |
| `spotify` | Spotify desktop app | title, artist, artwork | real play vs. pause |
| `browser` | whatever the system media keys control: Spotify Web, YouTube, Apple Music… | none | macOS has one play/pause key, so play and pause both toggle |

### Hearing it on the Mac too

By default the Mac goes silent while relaying, because its output is BlackHole. To hear it at home as well, create a **Multi-Output Device** in Audio MIDI Setup containing your speakers and BlackHole, select it as the output, and set `HOMESTREAM_AUTO_ROUTE=0` in `.env`.

### Tips

* **Data use:** 192 kbps is about 86 MB per hour. Set `HOMESTREAM_BITRATE=128k` for about 58 MB per hour.
* **Latency** is a few seconds. Skips show up on the phone after that delay.
* **Lock screen and headphone buttons** control the Mac. Pause also stops the stream (saving data), and play reconnects at the live edge.
* **Sleep:** the relay keeps the Mac from idling to sleep, but closing a MacBook's lid still sleeps it.
* Dropped connections (going underground, switching cells) reconnect automatically.

## Optional: Snapcast

```bash
./setup.sh --snapcast        # installs snapcast and sets HOMESTREAM_SNAPCAST=1
./scripts/start-relay.sh     # also starts snapserver (Opus, port 1704; web UI on 1780)
```

Point [Snapdroid](https://github.com/badaix/snapdroid) (Android) or any `snapclient` at the Mac's Tailscale IP. The phone page still provides the controls. Snapcast has no authentication, so keep it on Tailscale.

## Security

* Every route except the page itself requires the token, sent as `Authorization: Bearer <token>` or as the HttpOnly cookie that the pairing link sets.
* The pairing link carries the token in the URL fragment (`/#token=…`), which browsers never send to the server, so it doesn't end up in logs.
* The server listens on all interfaces by default. Set `HOMESTREAM_HOST` to your Tailscale IP to refuse your local network entirely.
* Don't port-forward 8765 to the internet. The token is the only protection and traffic is plain HTTP. If you need access from outside Tailscale, put HTTPS in front, e.g. `tailscale serve` or `tailscale funnel`.
* Treat `.env` like a password. To revoke every phone, change `HOMESTREAM_TOKEN` and restart.

## API

```bash
TOKEN=...   # from .env
curl -H "Authorization: Bearer $TOKEN" http://<mac>:8765/status
curl -X POST -H "Authorization: Bearer $TOKEN" http://<mac>:8765/next
```

| Route | |
|---|---|
| `GET /` | phone page (no token needed) |
| `POST /auth` `{"token": "…"}` | sets the session cookie |
| `POST /logout` | clears it |
| `GET /status` | player state, track info, stream state |
| `POST /play` `/pause` `/toggle` `/next` `/prev` | playback control. Returns 409 with a reason if it can't act (e.g. missing permission). |
| `GET /stream.mp3` | live audio |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Phone says *Mac unreachable* | Tailscale must be connected on both devices, and the Mac awake with the relay running. |
| Listen spins but there's no sound | The Mac's output must be BlackHole: `SwitchAudioSource -c`. The page footer shows capture errors. *Input/output error* usually means the terminal lacks Microphone permission. |
| Buttons do nothing (browser mode) | Grant Accessibility to your terminal app, then restart the relay. |
| `audio device 'BlackHole 2ch' not found` | Restart the Mac after installing BlackHole. |
| No sound on the Mac after a crash | Switch the output back in System Settings → Sound, or run `SwitchAudioSource -s "MacBook Pro Speakers"`. |

## Development

You don't need BlackHole or real playback to work on the code:

```bash
HOMESTREAM_AUDIO_DEVICE=test-tone HOMESTREAM_PLAYER=dryrun ./scripts/start-relay.sh
```

The relay then streams a 440 Hz tone and only logs commands. Environment variables override `.env`.

Tests start a real server against the test tone. They take a few seconds, need ffmpeg, and also run in CI:

```bash
control-server/.venv/bin/pip install -r control-server/requirements-dev.txt
control-server/.venv/bin/python -m pytest
```

```
control-server/main.py          FastAPI app: page, auth, API, stream
control-server/audio_stream.py  ffmpeg capture → MP3 fan-out to listeners
control-server/mac_control.py   Spotify (AppleScript) and media-key backends
control-server/preflight.py     startup permission checks
web/index.html                  the phone page (single file, no build step)
web/static/                     icons and web app manifest
scripts/start-relay.sh          routing, caffeinate, optional snapserver, server
scripts/capture-pcm.sh          PCM capture for snapserver
config/                         env.example, snapserver.conf.template
tests/                          pytest suite (API, streaming, shutdown, backends)
```

## License

MIT © Orros Chrysovalantis ([@orroschrysovalantis22](https://github.com/orroschrysovalantis22)), see [LICENSE](LICENSE). BlackHole, Snapcast, ffmpeg and Tailscale are separate projects under their own licenses. Setup installs them from Homebrew; this repo doesn't bundle them.
