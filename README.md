# HomeStream

[![CI](https://github.com/orroschrysovalantis22/homestream/actions/workflows/ci.yml/badge.svg)](https://github.com/orroschrysovalantis22/homestream/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![macOS](https://img.shields.io/badge/macOS-13%2B-lightgrey?logo=apple)

Listen to whatever your home Mac is playing from your phone, anywhere, and control it: play, pause, skip.

Play Spotify Web, YouTube, Apple Music or anything else on the Mac. On your phone, over cellular data, open one page. You hear the Mac about half a second behind live, and you get the album art and buttons a real player has, including on the lock screen. Nothing is exposed to the public internet: the phone reaches the Mac over [Tailscale](https://tailscale.com), a free private network.

<p align="center"><img src="docs/screenshot.png" width="620" alt="The HomeStream phone page in dark and light mode: album artwork, track title and artist, a progress bar, previous / play-pause / next controls and a Listen on this phone button"></p>

```
 Mac (home)                                                       Phone (anywhere)
┌─────────────────────────────────────────────────────┐          ┌──────────────────────────┐
│ Browser / Spotify / Music ──► BlackHole (virtual out)│          │  one web page:           │
│                                   │                  │          │   • live audio, ~0.5 s   │
│                         PortAudio capture → MP3      │ Tailscale│   • artwork, title, time │
│                                   ▼                  │ ◄──────► │   • ⏮ ⏯ ⏭                │
│  control server (FastAPI):  /stream.mp3  /events     │  private │   • lock-screen controls │
│     /play /pause /next ──► macOS Now Playing ──► app │  network │   • Home Screen icon     │
└─────────────────────────────────────────────────────┘          └──────────────────────────┘
```

* **Any phone, no app.** A web page you add to your Home Screen, tested on iPhone (Safari) and Android (Chrome). It keeps playing with the screen locked, and the lock screen shows the track with working buttons.
* **About half a second behind live.** Fast enough that pressing ⏭ feels immediate (plus whatever time the music service takes to load the next song).
* **Works with any player.** Controls and track info come from macOS's own Now Playing, the same thing Control Center shows: browser tabs, the Spotify app, Apple Music and more. No special permissions.
* **No password on your own devices.** Tailscale already proves the phone is yours. Anything else, like a device on the Mac's Wi-Fi, needs the token.
* **Clean audio.** Capture goes through PortAudio. ffmpeg's built-in macOS capture drops about 12% of the audio, which sounds like crackle.
* **Light on the Mac.** It only captures while someone is listening.
* Optional **Snapcast** server, for the Snapdroid Android app or multi-room setups.

## Requirements

* macOS 13+ (Apple Silicon or Intel) with [Homebrew](https://brew.sh)
* A free Tailscale account, with the Tailscale app on the Mac and on your phone

## Setup

```bash
git clone https://github.com/orroschrysovalantis22/homestream.git && cd homestream
./setup.sh
```

`setup.sh` is safe to re-run. It:

1. installs `ffmpeg`, `media-control`, `switchaudio-osx` and `qrencode` with Homebrew
2. offers to install [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole), the virtual sound card that carries the Mac's audio to the relay. It's a driver, so macOS asks for your password. If it isn't picked up straight away, restart the Mac (or run `sudo killall coreaudiod`).
3. creates a Python environment in `control-server/.venv`
4. writes `.env` with a random token, used only for devices that aren't on Tailscale
5. offers to install Tailscale and tells you when you still need to sign in

Then install Tailscale on your phone and sign in with the same account.

## Use

```bash
./scripts/start-relay.sh
```

The relay switches the Mac's sound output to BlackHole and checks permissions, so any macOS prompts appear now, while you're at the Mac. Then it keeps the Mac awake and prints your Mac's Tailscale address with a QR code:

```
  On your phone, with Tailscale switched on, scan the code below or type:
      100.x.y.z:8765
```

On your phone, scan the code or type the address, tap **Share → Add to Home Screen**, and from then on HomeStream is an icon. Play something on the Mac and tap **Listen on this phone**. Press Ctrl+C on the Mac to stop; your normal sound output comes back.

### macOS permissions (asked once)

| Permission | For your terminal app | Why |
|---|---|---|
| **Microphone** | click Allow on the prompt | reading the BlackHole input. Required. |
| **Accessibility** | only for `HOMESTREAM_PLAYER=browser` | sends media keys. The default doesn't need it. |
| **Automation → Spotify** | only for `HOMESTREAM_PLAYER=spotify` | AppleScript control of the Spotify app |

`start-relay.sh` triggers these on startup and tells you what's missing. Changes take effect after you restart the terminal. Set `HOMESTREAM_SKIP_PREFLIGHT=1` to skip the checks.

### Choosing what to control

`HOMESTREAM_PLAYER` in `.env`:

| Value | Controls | Track info |
|---|---|---|
| `auto` (default) | macOS Now Playing when `media-control` is installed, otherwise the Spotify app or media keys | full |
| `nowplaying` | whatever Control Center's Now Playing shows: any browser tab, Spotify, Music… | title, artist, album, artwork, progress |
| `spotify` | Spotify desktop app, via AppleScript | full |
| `browser` | the system media keys. Needs Accessibility; play and pause both toggle. | none |
| `dryrun` | a pretend player, for development | fake |

### Hearing it on the Mac too

By default the Mac goes silent while relaying, because its output is BlackHole. To hear it at home as well, create a **Multi-Output Device** in Audio MIDI Setup containing your speakers and BlackHole, select it as the output, and set `HOMESTREAM_AUTO_ROUTE=0` in `.env`.

### Tips

* **Data use:** 192 kbps is about 86 MB per hour. Set `HOMESTREAM_BITRATE=128k` for about 58 MB per hour.
* **Lock screen:** ⏮ ⏯ ⏭ there control the Mac. Pausing also stops the download; play picks up at live.
* **Network drops** (lifts, tunnels, switching cells) reconnect on their own, without touching the player, so it works while the phone is locked.
* **Sleep:** the relay keeps the Mac from idling to sleep, but closing a MacBook's lid still sleeps it.

## How it works

* **Capture.** `control-server/capture.py` reads BlackHole with PortAudio and pipes PCM into ffmpeg, which encodes MP3. The server cuts the MP3 into whole frames (about 26 ms each) and fans them out to listeners. A listener that falls behind loses whole frames, which play on cleanly. It never gets half a frame, which would decode as noise.
* **Playback.** The page fetches `/stream.mp3` itself and feeds the frames to an `<audio>` element through Media Source Extensions (`ManagedMediaSource` on iPhone). That lets it keep only about 0.5 s buffered. Left alone, iPhone Safari waits until it has about 5 s, and stays that far behind for the whole session. Browsers without MSE fall back to a plain `<audio src>`.
* **Now playing.** `media-control stream` reports track changes the moment they happen. The server pushes them to the page over server-sent events (`/events`), so the title and artwork change before the audio does.

## Optional: Snapcast

```bash
./setup.sh --snapcast        # installs snapcast and sets HOMESTREAM_SNAPCAST=1
./scripts/start-relay.sh     # also starts snapserver (Opus, port 1704; web UI on 1780)
```

Point [Snapdroid](https://github.com/badaix/snapdroid) (Android) or any `snapclient` at the Mac's Tailscale IP. The phone page still provides the controls. Snapcast has no authentication, so keep it on Tailscale.

## Security

* **Tailscale devices** get in without a password. A request counts only if both ends of the connection are Tailscale addresses (the phone's, and the Mac's that it connected to), so a device on the local network can't pass itself off as one. Set `HOMESTREAM_TRUST_TAILSCALE=0` to require the token from everyone.
* **Everything else**, such as a device on the Mac's Wi-Fi, needs the token from `.env`: an `Authorization: Bearer <token>` header, or the cookie set when you enter it on the page. Links of the form `/#token=…` carry it in the URL fragment, which browsers never send to servers or logs.
* Don't port-forward 8765 to the internet. Traffic is plain HTTP and the token is the only protection outside Tailscale. For access beyond your tailnet, put HTTPS in front, e.g. `tailscale serve` or `tailscale funnel`.
* Treat `.env` like a password. To revoke every token-based device, change `HOMESTREAM_TOKEN` and restart. To revoke a Tailscale device, remove it from your tailnet.

## API

```bash
curl http://100.x.y.z:8765/status               # from a device on your tailnet
curl -X POST http://100.x.y.z:8765/next
curl -H "Authorization: Bearer $TOKEN" http://192.168.1.20:8765/status   # anywhere else
```

| Route | |
|---|---|
| `GET /` | the phone page (public) |
| `GET /status` | player state, track info and progress, stream state, and how you got in (`access`) |
| `GET /events` | the same as server-sent events, pushed on every change |
| `GET /artwork?v=…` | current album art |
| `POST /play` `/pause` `/toggle` `/next` `/prev` | playback control. Returns 409 with a reason if it can't act. |
| `GET /stream.mp3?id=…` | live audio (MP3) |
| `GET /stream-info?id=…` | when that connection's audio was captured, for measuring delay |
| `POST /auth` `{"token": "…"}`, `POST /logout` | set or clear the token cookie |

## Troubleshooting

| Symptom | Fix |
|---|---|
| The page asks for a token | That device isn't coming through Tailscale. Switch Tailscale on in its app and reload. |
| A name like `my-mac:8765` doesn't load | Safari treats a bare name as a search. Use the numeric address (`100.x.y.z:8765`) or `http://my-mac:8765`. |
| *Mac unreachable* | Tailscale must be connected on both devices, and the Mac awake with the relay running. |
| Listen spins but there's no sound | The Mac's output must be BlackHole: `SwitchAudioSource -c`. Capture errors show at the bottom of the page. |
| `audio device 'BlackHole 2ch' not found` | Restart the Mac after installing BlackHole, or run `sudo killall coreaudiod`. |
| No sound on the Mac after a crash | Switch the output back in System Settings → Sound, or `SwitchAudioSource -s "MacBook Pro Speakers"`. |

## Development

You don't need BlackHole or real playback to work on the code:

```bash
HOMESTREAM_AUDIO_DEVICE=test-tone HOMESTREAM_PLAYER=dryrun ./scripts/start-relay.sh
```

Add `?debug` to the page's address to see the measured delay. Tests start a real server; they take a few seconds, need ffmpeg, and also run in CI:

```bash
control-server/.venv/bin/pip install -r control-server/requirements-dev.txt
control-server/.venv/bin/python -m pytest
```

### Testing on a (simulated) iPhone

iPhone Safari buffers and plays differently from desktop browsers, so the real check is the iOS Simulator (Xcode → Settings → Platforms → iOS):

1. `tools/harness.sh` starts a test relay on port 8766. It plays `test-signal`: a 440 Hz tone with a 1 kHz beep at the start of every second.
2. In the Simulator, open `http://localhost:8766/?debug`, with the Mac's sound output set to BlackHole, and tap Listen. The page reports its delay into the relay's log (`/tmp/homestream-harness.log`).
3. Record what the simulated iPhone plays, and check it for gaps, noise and timing:

```bash
control-server/.venv/bin/python control-server/capture.py --seconds 15 "BlackHole 2ch" |
  ffmpeg -f s16le -ar 48000 -ac 2 -i - -ac 1 -ar 44100 -f s16le rec.raw
control-server/.venv/bin/python tools/analyze_test_signal.py rec.raw   # beeps 1000 ms apart = clean
```

### Testing on a (simulated) Android phone

`tools/android.sh` drives an Android emulator. It can press the phone's media buttons and read what Android's media controls show, so lock-screen behaviour is testable too:

```bash
brew install --cask android-commandlinetools
sdkmanager --licenses
sdkmanager "platform-tools" "emulator" "system-images;android-36;google_apis_playstore;arm64-v8a"
tools/android.sh create && tools/android.sh boot
tools/harness.sh
tools/android.sh open "http://10.0.2.2:8766/?debug#token=harness-token-0123456789"   # 10.0.2.2 = the Mac
tools/android.sh key next     # the media "next" button; check the test relay's /status
tools/android.sh media        # title, playing state and available actions, as the lock screen sees them
```

The emulator's sound link to the Mac is unreliable, so on Android check playback with `adb shell dumpsys audio` (Chrome's player should be `state:started`) rather than by recording.

### Layout

```
control-server/main.py          FastAPI app: page, access rules, API, events, stream
control-server/audio_stream.py  capture → MP3 frames → listeners
control-server/capture.py       PortAudio capture (ffmpeg's macOS capture drops audio)
control-server/mac_control.py   Now Playing, Spotify (AppleScript) and media-key backends
control-server/preflight.py     startup permission checks
web/index.html                  the phone page: one file, no build step
web/static/                     icons and web app manifest
scripts/start-relay.sh          routing, permissions, caffeinate, optional snapserver, server
config/                         env.example, snapserver.conf.template
tests/                          pytest suite (API, access, streaming, shutdown, backends)
tools/                          iPhone test harness and recording analyser
```

## License

MIT © Orros Chrysovalantis ([@orroschrysovalantis22](https://github.com/orroschrysovalantis22)), see [LICENSE](LICENSE). BlackHole, Snapcast, ffmpeg, media-control and Tailscale are separate projects under their own licenses. Setup installs them from Homebrew; this repo doesn't bundle them.
