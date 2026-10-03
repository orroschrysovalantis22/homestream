"""Unit tests: audio pipeline, players on every OS (with fakes), settings and startup checks."""

import asyncio
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from homestream import audio as audio_stream, preflight
from homestream.audio import QUEUE_FRAMES, AudioBroadcaster, Listener, make_encoder, mp3_frame_info, split_mp3_frames
from homestream.capture import RATE, CaptureError, GeneratedSource, open_source
from homestream.players import ControlError, DryRunController, make_controller

ROOT = Path(__file__).resolve().parent.parent
mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS backend")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses a shell-script fake")
spotify_running = sys.platform == "darwin" and subprocess.run(["pgrep", "-xq", "Spotify"]).returncode == 0


# --- capture and encoding ------------------------------------------------------------

def test_test_source_is_real_time_48k_stereo():
    source = GeneratedSource("test-tone")
    start = time.monotonic()
    data = bytearray()
    for block in source.blocks():
        data += block
        if len(data) >= RATE * 4 // 5:  # 0.2 s of 16-bit stereo
            break
    source.stop()
    elapsed = time.monotonic() - start
    assert len(data) == RATE * 4 // 5
    assert 0.15 < elapsed < 0.5  # paced like a live capture, not dumped at once


def test_encoder_produces_whole_mp3_frames():
    source = GeneratedSource("test-signal")
    encoder = make_encoder(source.rate, 192)
    mp3 = bytearray()
    for i, block in enumerate(source.blocks()):
        mp3 += encoder.encode(block)
        if i == 49:  # 1 s
            break
    source.stop()
    frames = split_mp3_frames(mp3)
    assert 38 <= len(frames) <= 42  # 1152 samples per frame at 48 kHz = 24 ms
    assert {len(f) for f, _ in frames} <= {576, 577}  # 192 kbps at 48 kHz
    assert all(abs(d - 1152 / 48000) < 1e-9 for _, d in frames)


def test_test_devices_need_no_hardware():
    assert isinstance(open_source("test-tone"), GeneratedSource)
    assert isinstance(open_source("test-signal"), GeneratedSource)


@mac_only
def test_missing_mac_device_is_a_clear_error():
    with pytest.raises(CaptureError, match="not found"):
        open_source("No Such Device 123")


# --- audio fan-out -----------------------------------------------------------------------

def test_capture_runs_only_while_someone_listens(monkeypatch):
    monkeypatch.setattr(audio_stream, "IDLE_STOP_SECONDS", 0.3)

    async def scenario():
        b = AudioBroadcaster("test-tone")
        assert not b.status()["capturing"]
        listener = b.listen()
        assert await asyncio.wait_for(listener.__anext__(), 5)
        assert b.status()["capturing"]
        await listener.aclose()
        assert b.status()["listeners"] == 0
        for _ in range(30):
            await asyncio.sleep(0.1)
            if not b.status()["capturing"]:
                break
        assert not b.status()["capturing"]
        await b.close()

    asyncio.run(scenario())


def test_end_streams_finishes_every_listener():
    async def scenario():
        b = AudioBroadcaster("test-tone")
        received = []

        async def consume():
            async for chunk in b.listen():
                received.append(chunk)

        tasks = [asyncio.create_task(consume()) for _ in range(2)]
        while len(received) < 4:
            await asyncio.sleep(0.05)
        b.end_streams()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        await b.close()

    asyncio.run(scenario())


def test_split_mp3_frames_keeps_partial_frames_for_later():
    header = bytes([0xFF, 0xFB, 0xB0, 0x00])  # MPEG-1 Layer III, 192 kbps, 44.1 kHz, no padding
    length, duration = mp3_frame_info(header)
    assert length == 626 and abs(duration - 1152 / 44100) < 1e-9
    frame = header + bytes(length - 4)
    buffer = bytearray(b"junk" + frame + frame + frame[:100])
    frames = split_mp3_frames(buffer)
    assert [f for f, _ in frames] == [frame, frame]
    assert bytes(buffer) == frame[:100]  # the incomplete third frame waits for more data


def test_slow_listener_drops_whole_frames_and_counts_them():
    b = AudioBroadcaster("test-tone")
    listener = Listener("slow")
    b._listeners.add(listener)
    for i in range(QUEUE_FRAMES + 10):
        b._broadcast(bytes([i]), 0.024)
    assert listener.queue.qsize() == QUEUE_FRAMES
    assert listener.queue.get_nowait()[1] == bytes([10])
    assert abs(listener.dropped_seconds - 10 * 0.024) < 1e-9


# --- players: shared -------------------------------------------------------------------

def test_unknown_player_is_a_config_error():
    with pytest.raises(SystemExit, match="HOMESTREAM_PLAYER"):
        make_controller("winamp")


def test_dryrun_tracks_state():
    async def scenario():
        c = DryRunController()
        await c.command("play")
        assert (await c.status()).state == "playing"
        await c.command("toggle")
        assert (await c.status()).state == "paused"
        await c.command("next")
        status = await c.status()
        assert status.state == "paused" and status.title == "Test tone 2"

    asyncio.run(scenario())


# --- players: macOS (Now Playing via a fake media-control; Spotify) ----------------------

FAKE_MEDIA_CONTROL = str(Path(__file__).parent / "fakes" / "media-control")


@posix_only
def test_now_playing_reads_track_info_and_sends_commands(tmp_path, monkeypatch):
    from homestream.players.macos import NowPlayingController

    log = tmp_path / "commands.log"
    monkeypatch.setenv("FAKE_MEDIA_LOG", str(log))

    async def scenario():
        c = NowPlayingController(FAKE_MEDIA_CONTROL)
        await c.start()
        await asyncio.wait_for(c.wait_changed(), 5)
        s = await c.status()
        assert (s.state, s.title, s.artist, s.album) == ("playing", "First Song", "Test Artist", "Test Album")
        assert s.duration == 200.5 and s.elapsed == 12.0 and s.elapsed_at == 1767225600.0
        assert s.app == "FakePlayer"
        assert s.artwork == f"/artwork?v={c.artwork_id}" and c.artwork == ("image/jpeg", b"\xff\xd8fake-jpeg-bytes")

        await c.command("pause")
        await asyncio.wait_for(c.wait_changed(), 5)
        s = await c.status()
        assert (s.state, s.title) == ("paused", "Second Song")
        for cmd in ("next", "prev", "toggle", "play"):
            await c.command(cmd)
        await c.close()

    asyncio.run(scenario())
    assert log.read_text().split() == ["pause", "next-track", "previous-track", "toggle-play-pause", "play"]


def test_now_playing_with_nothing_playing_is_idle():
    from homestream.players.macos import NowPlayingController

    c = NowPlayingController(FAKE_MEDIA_CONTROL)
    c._update({})
    status = asyncio.run(c.status())
    assert status.state == "idle" and status.title is None and status.artwork is None


@mac_only
@pytest.mark.skipif(spotify_running, reason="Spotify is running")
def test_spotify_not_running():
    from homestream.players.macos import SpotifyController

    async def scenario():
        status = await SpotifyController().status()
        assert status.state == "not-running"
        with pytest.raises(ControlError, match="not running"):
            await SpotifyController().command("next")

    asyncio.run(scenario())


# --- players: Linux (MPRIS) ----------------------------------------------------------------

def mpris_line(**fields):
    from homestream.players.linux import FIELDS

    return "\t".join(fields.get(f, "") for f in FIELDS)


@posix_only
def test_mpris_reads_playerctl_output(tmp_path):
    from homestream.players.linux import MprisController

    art = tmp_path / "cover.png"
    art.write_bytes(b"\x89PNG fake")
    c = MprisController("playerctl")
    c.update(mpris_line(**{
        "status": "Playing", "playerName": "spotify", "xesam:title": "Song", "xesam:artist": "Band",
        "xesam:album": "Album", "mpris:artUrl": f"file://{art}", "mpris:length": "215000000", "position": "12500000",
    }))
    s = asyncio.run(c.status())
    assert (s.state, s.title, s.artist, s.album, s.app) == ("playing", "Song", "Band", "Album", "Spotify")
    assert s.duration == 215.0 and s.elapsed == 12.5 and s.elapsed_at
    assert s.artwork == f"/artwork?v={c.artwork_id}" and c.artwork == ("image/png", b"\x89PNG fake")


def test_mpris_passes_web_artwork_through_and_goes_idle():
    from homestream.players.linux import MprisController

    c = MprisController("playerctl")
    c.update(mpris_line(**{"status": "Paused", "playerName": "firefox.instance_1_23", "xesam:title": "Video",
                           "mpris:artUrl": "https://i.example.com/cover.jpg"}))
    s = asyncio.run(c.status())
    assert (s.state, s.app, s.artwork) == ("paused", "Firefox", "https://i.example.com/cover.jpg")
    c.update("")  # the player went away
    assert asyncio.run(c.status()).state == "idle"


@posix_only
def test_mpris_commands_use_playerctl_verbs(tmp_path):
    from homestream.players.linux import MprisController

    log = tmp_path / "log"
    fake = tmp_path / "playerctl"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    fake.chmod(0o755)
    c = MprisController(str(fake), player="spotify")

    async def scenario():
        for cmd in ("play", "pause", "toggle", "next", "prev"):
            await c.command(cmd)

    asyncio.run(scenario())
    assert log.read_text().splitlines() == [
        "--player=spotify play", "--player=spotify pause", "--player=spotify play-pause",
        "--player=spotify next", "--player=spotify previous",
    ]


# --- players: Windows (with fake WinRT objects) ----------------------------------------

def test_windows_session_is_read_into_plain_values():
    from homestream.players.windows import read_session

    updated = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = SimpleNamespace(
        source_app_user_model_id="Spotify.exe",
        get_playback_info=lambda: SimpleNamespace(playback_status=4),
        get_timeline_properties=lambda: SimpleNamespace(
            start_time=timedelta(0), end_time=timedelta(seconds=200), position=timedelta(seconds=42),
            last_updated_time=updated),
    )
    props = SimpleNamespace(title="Song", artist="Band", album_title="", thumbnail=object())
    info = read_session(session, props)
    assert info == {"title": "Song", "artist": "Band", "album": None, "state": "playing", "app": "Spotify",
                    "duration": 200.0, "elapsed": 42.0, "elapsed_at": updated.timestamp(), "has_thumbnail": True}


@pytest.mark.parametrize("app_id,name", [
    ("Spotify.exe", "Spotify"), ("Chrome", "Chrome"), ("MSEdge", "Edge"), ("Brave", "Brave"),
    ("308046B0AF4A39CB", "Firefox"), ("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic", "Media Player"),
    ("SomeNewPlayer.exe", "Somenewplayer"), (None, None),
])
def test_windows_app_names(app_id, name):
    from homestream.players.windows import app_name

    assert app_name(app_id) == name


# --- settings and addresses ------------------------------------------------------------

def test_settings_file_is_created_with_a_private_random_token(tmp_path):
    from homestream.config import ensure_env_file, load_env_file

    path = ensure_env_file(tmp_path / "homestream.env")
    text = path.read_text()
    token = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("HOMESTREAM_TOKEN="))
    assert len(token) == 48
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600
    assert ensure_env_file(path).read_text() == text  # never overwritten
    assert load_env_file(path) == path


def test_phone_address_prefers_tailscale(monkeypatch):
    from homestream import system

    monkeypatch.setattr(system, "tailscale_status", lambda: {"ip": "100.64.0.10", "name": "my-mac"})
    assert system.phone_address(8765) == {"via": "tailscale", "url": "http://100.64.0.10:8765",
                                          "typed": "100.64.0.10:8765", "name": "my-mac"}
    monkeypatch.setattr(system, "tailscale_status", lambda: {})
    monkeypatch.setattr(system, "lan_ip", lambda: "192.168.1.20")
    monkeypatch.setenv("HOMESTREAM_TOKEN", "t" * 20)
    assert system.phone_address(8765)["url"] == "http://192.168.1.20:8765/#token=" + "t" * 20


def test_cli_version_and_config(tmp_path, monkeypatch, capsys):
    from homestream.cli import main

    with pytest.raises(SystemExit):
        main(["--version"])
    assert "homestream" in capsys.readouterr().out
    monkeypatch.setenv("HOMESTREAM_ENV_FILE", str(tmp_path / "x.env"))
    assert main(["config"]) == 0
    assert (tmp_path / "x.env").exists()


def test_cli_runs_without_a_console(tmp_path, monkeypatch):
    # pythonw (start at login) and the packaged Windows app have no stdout/stderr, and
    # uvicorn's logging calls sys.stdout.isatty(): that crashed the tray app on Windows.
    from homestream.cli import main
    from homestream.server import make_server

    monkeypatch.setenv("HOMESTREAM_ENV_FILE", str(tmp_path / "x.env"))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert main(["config"]) == 0
    make_server(log_level="warning")


# --- startup checks --------------------------------------------------------------------

def test_preflight_passes_with_test_tone(capsys):
    assert preflight.run("test-tone", "dryrun") is True
    assert "audio source: test-tone" in capsys.readouterr().out


@mac_only
def test_preflight_missing_device_fails_without_opening_settings(monkeypatch, capsys):
    opened = []
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[0] == "open":
            opened.append(cmd)
            return None
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(preflight.subprocess, "run", run)
    assert preflight.check_capture("No Such Device 123") is False
    assert "not found" in capsys.readouterr().out
    assert opened == []


def test_test_signal_analyser_on_generated_audio():
    sys.path.insert(0, str(ROOT / "tools"))
    import numpy as np
    from analyze_test_signal import analyze

    source = GeneratedSource("test-signal")
    pcm = bytearray()
    for block in source.blocks():
        pcm += block
        if len(pcm) >= RATE * 4 * 3:
            break
    source.stop()
    stereo = np.frombuffer(bytes(pcm), dtype="<i2").reshape(-1, 2)
    mono_44k = stereo[:, 0][:: 1]  # analyser assumes 44.1 kHz; resample by index
    idx = (np.arange(int(len(mono_44k) * 44100 / RATE)) * RATE / 44100).astype(int)
    report = analyze(mono_44k[idx])
    assert report["beeps"] >= 2 and not report["timing_jumps"] and not report["tone_dropouts_at"]


# --- start at login ----------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="the Windows version uses the registry")
def test_start_at_login_toggles(tmp_path, monkeypatch):
    from homestream import autostart

    monkeypatch.setattr(autostart.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    assert autostart.is_enabled()
    if sys.platform == "darwin":
        import plistlib

        plist = plistlib.loads((tmp_path / "Library/LaunchAgents/com.homestream.tray.plist").read_bytes())
        assert plist["RunAtLoad"] is True and plist["ProgramArguments"][-3:] == ["-m", "homestream", "tray"]
    else:
        text = (tmp_path / ".config/autostart/homestream.desktop").read_text()
        assert "Exec=" in text and "homestream tray" in text
    autostart.set_enabled(False)
    assert not autostart.is_enabled()


def test_autostart_command_uses_this_python():
    from homestream import autostart

    cmd = autostart.command()
    assert cmd[-3:] == ["-m", "homestream", "tray"]


@mac_only
def test_mac_output_devices_are_readable():
    from homestream import macaudio

    outputs = macaudio.output_devices()
    if not outputs:
        pytest.skip("no audio outputs (e.g. a CI machine)")
    assert macaudio.default_output() in outputs
    assert macaudio.set_default_output("No Such Output 123") is False
