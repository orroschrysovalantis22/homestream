# HomeStream

[![CI](https://github.com/orroschrysovalantis22/homestream/actions/workflows/ci.yml/badge.svg)](https://github.com/orroschrysovalantis22/homestream/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![macOS · Windows · Linux](https://img.shields.io/badge/computer-macOS%20·%20Windows%20·%20Linux-lightgrey)
![iPhone · Android](https://img.shields.io/badge/phone-iPhone%20·%20Android-lightgrey)
[![Sponsor](https://img.shields.io/badge/sponsor-%E2%9D%A4-ea4aaa?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/orroschrysovalantis22)

Listen to whatever your computer is playing from your phone, anywhere, and control it: play, pause, skip.

Play Spotify, YouTube, Apple Music or anything else on your Mac, Windows or Linux computer. On your phone, over Wi-Fi or cellular data, open one page. You hear the computer about half a second behind live, with the album art and buttons a real player has, including on the lock screen. Nothing is exposed to the public internet: the phone reaches the computer over [Tailscale](https://tailscale.com), a free private network.

<p align="center"><img src="docs/screenshot.png" width="620" alt="The HomeStream phone page in dark and light mode: album artwork, track title and artist, a progress bar, previous / play-pause / next controls and a Listen on this phone button"></p>

* **Any phone, no app.** A web page you add to your Home Screen, tested on iPhone (Safari) and Android (Chrome). It keeps playing with the screen locked, and the lock screen's buttons work.
* **About half a second behind live,** so pressing ⏭ feels immediate.
* **Works with whatever is playing.** Track info and controls come from the system's own media controls (the same ones as Control Center on Mac, the volume flyout on Windows, and MPRIS on Linux), so any app works: browsers, Spotify, Apple Music, VLC…
* **Lives in your menu bar / system tray.** Start at login, and a "Connect a phone" page with a QR code. No terminal needed.
* **No password on your own devices.** Tailscale already proves the phone is yours. Anything else, like a device on the computer's Wi-Fi, needs a token.
* **Clean audio.** Captured and MP3-encoded inside the app (PortAudio/WASAPI/PulseAudio + LAME), verified gap-free by an automated test signal.

## Get started

You need a free [Tailscale](https://tailscale.com/download) account, with the app on the computer and on your phone, signed in to the same account.

### Download the app

From the [latest release](https://github.com/orroschrysovalantis22/homestream/releases/latest):

| | Download | Also needed |
|---|---|---|
| **macOS** 13+ (Apple Silicon) | `HomeStream-macOS.zip`, then move HomeStream to Applications | [BlackHole 2ch](https://existential.audio/blackhole/) (macOS has no built-in way to record what's playing). For track info and controls with any player: `brew install media-control` |
| **Windows** 10/11 | `HomeStream-Windows.zip`, then run `HomeStream.exe` | nothing |
| **Linux** (PulseAudio or PipeWire) | `HomeStream-Linux.tar.gz`, then run `HomeStream` | `playerctl` for controls (e.g. `sudo apt install playerctl`) |

The apps aren't signed yet, so the first time:
* **macOS:** right-click HomeStream → **Open** → **Open**.
* **Windows:** if SmartScreen appears, **More info** → **Run anyway**. Then Windows asks whether HomeStream may use the network: **Allow**, that's how your phone reaches it.

HomeStream appears in the menu bar (Mac) or system tray (Windows, Linux) and opens the **Connect a phone** page. Scan its QR code with your phone, tap **Share → Add to Home Screen**, and from then on it's an app on your phone. Play something on the computer and tap **Listen on this phone**.

> **Windows status:** tested on Windows 11 (in a virtual machine): capture verified gap-free with the test signal, controls with Media Player, the tray app and `HomeStream.exe`. Not yet on a physical PC with Spotify; reports welcome.

### Or run from source

```bash
git clone https://github.com/orroschrysovalantis22/homestream.git && cd homestream
./setup.sh                                       # macOS and Linux
powershell -ExecutionPolicy Bypass -File setup.ps1   # Windows
```

Setup installs what's needed (on Mac: BlackHole and media-control via Homebrew; on Linux: playerctl and the PulseAudio client library; on Windows: Python if missing), creates `.venv`, writes your settings with a random token, and offers to install Tailscale. Then:

```bash
.venv/bin/homestream tray          # menu bar / system tray (Windows: .venv\Scripts\homestream tray)
./scripts/start-relay.sh           # or in a terminal, with a QR code (Windows: scripts\start-relay.cmd)
```

## How each computer captures and controls

| | Captures | Controls and track info |
|---|---|---|
| **macOS** | The BlackHole virtual device. HomeStream switches the sound output to it while running and back afterwards. | macOS Now Playing via `media-control`: any app. Without it: the Spotify app (AppleScript), or media keys (needs Accessibility). |
| **Windows** | What the speakers play (WASAPI loopback). No driver. | Global System Media Transport Controls: anything in the volume flyout. |
| **Linux** | The monitor of the default PulseAudio/PipeWire output. No driver. | MPRIS via `playerctl`: Spotify, Firefox, Chrome, VLC, mpv… |

**macOS permissions.** The first time, macOS asks to let HomeStream (or your terminal) use the **microphone**. Allow it: that's how it hears BlackHole. To hear the music on the Mac as well, create a **Multi-Output Device** in Audio MIDI Setup (speakers + BlackHole), select it, and set `HOMESTREAM_AUTO_ROUTE=0`.

## Settings

Settings are `HOMESTREAM_*` variables in a settings file: `.env` in a source checkout, otherwise per user, e.g. `~/Library/Application Support/HomeStream/homestream.env`, `%APPDATA%\HomeStream\homestream.env` or `~/.config/homestream/homestream.env`. `homestream config` prints the path. The useful ones:

| Setting | Default | |
|---|---|---|
| `HOMESTREAM_AUDIO_DEVICE` | per OS | what to capture; `homestream doctor` lists what's available |
| `HOMESTREAM_PLAYER` | `auto` | per OS: `nowplaying`/`spotify`/`browser` (Mac), `mpris` (Linux), `windows`; `dryrun` for development |
| `HOMESTREAM_BITRATE` | `192k` | 192 kbps is about 86 MB per hour; `128k` is about 58 MB |
| `HOMESTREAM_TRUST_TAILSCALE` | `1` | `0` requires the token from everyone |
| `HOMESTREAM_TOKEN` | random | password for devices that aren't on Tailscale |
| `HOMESTREAM_PORT` | `8765` | |
| `HOMESTREAM_AUTO_ROUTE` | `1` | macOS: switch the output to BlackHole while running |
| `HOMESTREAM_SNAPCAST` | `0` | also run a [Snapcast](https://github.com/badaix/snapcast) server (macOS/Linux) |

## How it works

```
 Computer                                                          Phone (anywhere)
┌──────────────────────────────────────────────────────┐          ┌──────────────────────────┐
│ any player ──► speakers / BlackHole                   │          │  one web page:           │
│                    │ capture (PortAudio/WASAPI/Pulse) │ Tailscale│   • live audio, ~0.5 s   │
│                    ▼                                  │ ◄──────► │   • artwork, title, time │
│ HomeStream: LAME → MP3 frames → /stream.mp3           │  private │   • ⏮ ⏯ ⏭, lock screen   │
│   /events (now playing) ◄── system media controls     │  network │   • Home Screen icon     │
└──────────────────────────────────────────────────────┘          └──────────────────────────┘
```

* **Capture and encoding** run in-process on a background thread: PCM from the OS's capture API, MP3 via LAME. The server cuts the stream into whole MP3 frames (24 ms each). A listener that falls behind loses whole frames, which play on cleanly, never half a frame, which would decode as noise. Capture only runs while someone listens.
* **Playback on the phone** uses Media Source Extensions (`ManagedMediaSource` on iPhone). The page fetches the stream itself and keeps about 0.5 s buffered. Left alone, iPhone Safari waits for about 5 s and stays that far behind. A dropped connection only restarts the fetch, so playback survives the phone being locked.
* **Now playing** is pushed to the page over server-sent events the moment the track changes.

## Security

* **Tailscale devices** get in without a password: only when both ends of the connection are Tailscale addresses, so a device on the local network can't pass itself off as one.
* **Everything else** needs the token: an `Authorization: Bearer <token>` header, or the cookie set when it's entered on the page. Links of the form `/#token=…` keep it in the URL fragment, which never reaches servers or logs.
* The **Connect a phone** page (`/pair`) is only served to the computer's own browser.
* Don't port-forward 8765 to the internet; traffic is plain HTTP. For access beyond your tailnet, put HTTPS in front (`tailscale serve` / `funnel`).

## API

| Route | |
|---|---|
| `GET /` | the phone page |
| `GET /status`, `GET /events` | player, track, progress and stream state (once / pushed on change) |
| `POST /play` `/pause` `/toggle` `/next` `/prev` | playback control (409 with a reason if the player can't act) |
| `GET /stream.mp3?id=…` | live audio |
| `GET /stream-info?id=…` | when that connection's audio was captured, for measuring delay |
| `GET /artwork?v=…` | current album art |
| `GET /pair` | "Connect a phone" page (this computer only) |
| `POST /auth`, `POST /logout` | set / clear the token cookie |

```bash
curl -X POST http://100.x.y.z:8765/next                                     # from a device on your tailnet
curl -H "Authorization: Bearer $TOKEN" http://192.168.1.20:8765/status      # anywhere else
```

## Troubleshooting

`homestream doctor` checks capture, controls and Tailscale and says what's wrong.

| Symptom | Fix |
|---|---|
| The page asks for a token | That device isn't coming through Tailscale. Switch Tailscale on in its app and reload. |
| A name like `my-pc:8765` doesn't load | Browsers treat a bare name as a search. Use the numeric address (`100.x.y.z:8765`) or type `http://` first. |
| Windows: the phone can't connect | You clicked Cancel when Windows asked about the network. Windows Security → Firewall & network protection → Allow an app through firewall → tick HomeStream (or Python). |
| *Mac unreachable* | Tailscale must be connected on both devices, and the computer awake with HomeStream running. |
| No sound | macOS: the output must be BlackHole (HomeStream switches it unless `HOMESTREAM_AUTO_ROUTE=0`); allow the microphone permission. Linux: `homestream doctor` should list a "Monitor of …". |
| No track info / buttons do nothing | macOS: `brew install media-control`. Linux: install `playerctl`. |
| No sound on the Mac after a crash | Pick your speakers again in System Settings → Sound. |

## Development

```bash
pip install -e ".[dev,tray]"
HOMESTREAM_AUDIO_DEVICE=test-tone HOMESTREAM_PLAYER=dryrun homestream start   # no audio hardware needed
python -m pytest                                                               # ~70 tests, ~10 s
tools/linux/test.sh        # Linux end to end in Docker: mpv → PulseAudio → HomeStream → clean stream + MPRIS
pyinstaller packaging/homestream.spec                                          # build the app for this OS
```

Add `?debug` to the page's address to see the measured delay. CI runs the tests on macOS, Linux and Windows, plus the Linux end-to-end test. Tagging `v*` builds and publishes the three apps.

**On real phone browsers.** iPhone Safari and Android Chrome behave differently from desktop browsers, so they're tested in the iOS Simulator and an Android emulator:
* `tools/harness.sh` starts a test server playing `test-signal`, a tone with a 1 kHz beep every second.
* Record what the simulated phone plays with `python -m homestream.capture --seconds 15 "BlackHole 2ch"` and check it with `tools/analyze_test_signal.py`. Clean means beeps 1000 ms apart and no gaps or noise.
* `tools/android.sh` drives the emulator, including the phone's media buttons and what its lock screen shows.

### Layout

```
homestream/cli.py           the `homestream` command: start, serve, doctor, tray, config
homestream/server.py        web app: page, access rules, API, events, stream, /pair
homestream/audio.py         capture → LAME → MP3 frames → listeners
homestream/capture.py       per-OS capture (PortAudio, WASAPI loopback, PulseAudio monitor) and test signals
homestream/players/         per-OS now playing and controls (macos.py, linux.py, windows.py)
homestream/tray.py          menu bar / system tray app;  autostart.py: start at login per OS
homestream/system.py        keep awake, macOS output routing (macaudio.py), Tailscale and addresses
homestream/web/             the phone page (one file, no build step), /pair page, icons
packaging/                  PyInstaller recipe and app icons
tests/  tools/              tests; Linux, iPhone and Android test harnesses
```

## Support

HomeStream is free and open source. If it's useful to you, you can support its development on [GitHub Sponsors](https://github.com/sponsors/orroschrysovalantis22). Stars, bug reports and pull requests help too.

## License

MIT © Orros Chrysovalantis ([@orroschrysovalantis22](https://github.com/orroschrysovalantis22)), see [LICENSE](LICENSE). BlackHole, media-control, Snapcast and Tailscale are separate projects under their own licenses and aren't bundled.
