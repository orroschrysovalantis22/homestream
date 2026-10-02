"""Unit tests for the audio fan-out, the player backends and the preflight checks."""

import asyncio
import shutil
import subprocess

import pytest

import audio_stream
import preflight
from audio_stream import QUEUE_FRAMES, AudioBroadcaster
from mac_control import ControlError, DryRunController, SpotifyController, make_controller

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
spotify_running = subprocess.run(["pgrep", "-xq", "Spotify"]).returncode == 0


# --- audio fan-out ---------------------------------------------------------------

@needs_ffmpeg
def test_capture_runs_only_while_someone_listens(monkeypatch):
    monkeypatch.setattr(audio_stream, "IDLE_STOP_SECONDS", 0.3)

    async def scenario():
        b = AudioBroadcaster("test-tone")
        assert not b.status()["capturing"]
        listener = b.listen()
        assert await listener.__anext__()
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


@needs_ffmpeg
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


def test_real_devices_are_captured_by_portaudio_and_encoded_by_ffmpeg():
    b = AudioBroadcaster("BlackHole 2ch", "128k")
    capture = b.capture_command()
    assert capture[-2:] == [str(audio_stream.CAPTURE_SCRIPT), "BlackHole 2ch"]
    args = b.ffmpeg_args(48000)
    assert "avfoundation" not in args  # ffmpeg's own capture drops audio
    assert args[args.index("-i") + 1] == "pipe:0"
    assert args[args.index("-b:a") + 1] == "128k"
    assert args[-2:] == ["mp3", "pipe:1"]
    assert AudioBroadcaster("test-tone").capture_command() is None


# --- player backends ---------------------------------------------------------------

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
        assert (await c.status()).state == "paused"

    asyncio.run(scenario())


@pytest.mark.skipif(spotify_running, reason="Spotify is running")
def test_spotify_not_running():
    async def scenario():
        status = await SpotifyController().status()
        assert status.state == "not-running"
        with pytest.raises(ControlError, match="not running"):
            await SpotifyController().command("next")

    asyncio.run(scenario())


# --- preflight -------------------------------------------------------------------------

def test_preflight_passes_with_test_tone(monkeypatch, capsys):
    monkeypatch.setenv("HOMESTREAM_AUDIO_DEVICE", "test-tone")
    monkeypatch.setenv("HOMESTREAM_PLAYER", "dryrun")
    assert preflight.main() == 0
    assert "audio source: test-tone" in capsys.readouterr().out


@needs_ffmpeg
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
    assert "Audio device not found" in capsys.readouterr().out
    assert opened == []


# --- MP3 framing ---------------------------------------------------------------------

def test_split_mp3_frames_keeps_partial_frames_for_later():
    from audio_stream import mp3_frame_info, split_mp3_frames

    header = bytes([0xFF, 0xFB, 0xB0, 0x00])  # MPEG-1 Layer III, 192 kbps, 44.1 kHz, no padding
    length, duration = mp3_frame_info(header)
    assert length == 626 and abs(duration - 1152 / 44100) < 1e-9
    frame = header + bytes(length - 4)
    buffer = bytearray(b"junk" + frame + frame + frame[:100])
    frames = split_mp3_frames(buffer)
    assert [f for f, _ in frames] == [frame, frame]
    assert bytes(buffer) == frame[:100]  # the incomplete third frame waits for more data


def test_slow_listener_drops_whole_frames_and_counts_them():
    from audio_stream import Listener

    b = AudioBroadcaster("test-tone")
    listener = Listener("slow")
    b._listeners.add(listener)
    for i in range(QUEUE_FRAMES + 10):
        b._broadcast(bytes([i]), 0.026)
    assert listener.queue.qsize() == QUEUE_FRAMES
    assert listener.queue.get_nowait()[1] == bytes([10])
    assert abs(listener.dropped_seconds - 10 * 0.026) < 1e-9


# --- Now Playing backend (with a fake media-control) ---------------------------------

FAKE_MEDIA_CONTROL = str(__import__("pathlib").Path(__file__).parent / "fakes" / "media-control")


def test_now_playing_reads_track_info_and_sends_commands(tmp_path, monkeypatch):
    from mac_control import NowPlayingController

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
    from mac_control import NowPlayingController

    c = NowPlayingController(FAKE_MEDIA_CONTROL)
    c._update({})
    status = asyncio.run(c.status())
    assert status.state == "idle" and status.title is None and status.artwork is None
