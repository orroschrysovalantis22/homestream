"""Unit tests for the audio fan-out, the player backends and the preflight checks."""

import asyncio
import shutil
import subprocess

import pytest

import audio_stream
import preflight
from audio_stream import QUEUE_CHUNKS, AudioBroadcaster
from mac_control import ControlError, DryRunController, SpotifyController, make_controller

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
spotify_running = subprocess.run(["pgrep", "-xq", "Spotify"]).returncode == 0


# --- audio fan-out ---------------------------------------------------------------

def test_slow_listener_keeps_the_newest_audio():
    b = AudioBroadcaster("test-tone")
    queue = asyncio.Queue(maxsize=QUEUE_CHUNKS)
    b._listeners.add(queue)
    for i in range(QUEUE_CHUNKS + 50):
        b._broadcast(bytes([i % 256]))
    assert queue.qsize() == QUEUE_CHUNKS
    assert queue.get_nowait() == bytes([50])


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


def test_ffmpeg_args_for_a_device():
    args = AudioBroadcaster("BlackHole 2ch", "128k").ffmpeg_args()
    assert args[args.index("-i") + 1] == ":BlackHole 2ch"
    assert args[args.index("-b:a") + 1] == "128k"
    assert args[-2:] == ["mp3", "pipe:1"]


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
    assert "test tone" in capsys.readouterr().out


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
