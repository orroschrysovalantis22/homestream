"""Capturing what the computer is playing, as raw PCM: 16-bit stereo, 48 kHz.

  macOS    the BlackHole virtual device, via PortAudio (sounddevice). macOS has
           no built-in way to record what's playing. (ffmpeg's own macOS
           capture silently loses ~12% of the audio, heard as crackle.)
  Windows  "loopback" of the default output (WASAPI), via soundcard. No driver.
  Linux    the "monitor" of the default PulseAudio/PipeWire output, via
           soundcard. No driver.

Two test devices need no audio hardware: "test-tone" (440 Hz) and
"test-signal" (440 Hz with a 1 kHz beep at the start of every second, for
measuring delay and gaps; see tools/analyze_test_signal.py).

    python -m homestream.capture [DEVICE] [--seconds N] > out.raw   # 48 kHz s16le stereo
    python -m homestream.capture --list                              # what can be captured
"""

from __future__ import annotations

import argparse
import math
import queue
import sys
import threading
import time
from array import array
from typing import Iterator

RATE = 48000
CHANNELS = 2
BLOCK = 960  # frames: 20 ms
TEST_DEVICES = ("test-tone", "test-signal")


class CaptureError(RuntimeError):
    pass


class Source:
    """Yields blocks of 16-bit little-endian interleaved stereo PCM at `rate`."""

    rate = RATE

    def __init__(self) -> None:
        self._stop = threading.Event()

    def blocks(self) -> Iterator[bytes]:
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()


class GeneratedSource(Source):
    """Generated in real time, so it behaves like a live capture."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def _sample(self, t: float) -> float:
        tone = 0.2 * math.sin(2 * math.pi * 440 * t)
        if self.kind == "test-tone":
            return tone
        phase = t % 1.0
        value = tone if phase < 0.9 else 0.0
        if phase < 0.05:
            value += 0.5 * math.sin(2 * math.pi * 1000 * t)
        return value

    def blocks(self) -> Iterator[bytes]:
        start = time.monotonic()
        frame = 0
        while not self._stop.is_set():
            pcm = array("h")
            for i in range(frame, frame + BLOCK):
                v = int(self._sample(i / RATE) * 32767)
                pcm.append(v)
                pcm.append(v)
            if sys.byteorder == "big":
                pcm.byteswap()
            frame += BLOCK
            wait = start + frame / RATE - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            yield pcm.tobytes()


class PortAudioSource(Source):
    """A named input device (macOS: BlackHole), via sounddevice."""

    def __init__(self, device: str) -> None:
        super().__init__()
        import sounddevice as sd

        self.device = device
        try:
            info = sd.query_devices(device, "input")
        except (ValueError, sd.PortAudioError) as e:
            raise CaptureError(f"Audio device not found: {device}") from e
        self.rate = RATE if self._rate_ok(sd, RATE) else int(info["default_samplerate"])

    def _rate_ok(self, sd, rate: int) -> bool:
        try:
            sd.check_input_settings(device=self.device, samplerate=rate, channels=CHANNELS, dtype="int16")
            return True
        except Exception:
            return False

    def blocks(self) -> Iterator[bytes]:
        import sounddevice as sd

        chunks: queue.Queue[bytes] = queue.Queue(maxsize=500)

        def on_audio(data, frames, when, status):
            try:
                chunks.put_nowait(bytes(data))
            except queue.Full:
                pass

        try:
            with sd.RawInputStream(device=self.device, samplerate=self.rate, channels=CHANNELS,
                                   dtype="int16", blocksize=BLOCK, callback=on_audio):
                while not self._stop.is_set():
                    try:
                        yield chunks.get(timeout=0.5)
                    except queue.Empty:
                        continue
        except sd.PortAudioError as e:
            raise CaptureError(f"Can't capture {self.device!r}: {e}") from e


class LoopbackSource(Source):
    """What the speakers are playing (Windows: WASAPI loopback; Linux: the output's monitor), via soundcard."""

    def __init__(self, device: str) -> None:
        super().__init__()
        try:
            import soundcard as sc
        except Exception as e:  # missing library, or no PulseAudio/PipeWire on Linux
            raise CaptureError(f"Can't capture audio: {e}") from e
        self.sc = sc
        try:
            if device in ("", "default"):
                self.speaker = sc.default_speaker()
                self.mic = sc.get_microphone(id=str(self.speaker.name), include_loopback=True)
            else:
                self.speaker = None
                self.mic = sc.get_microphone(id=device, include_loopback=True)
        except Exception as e:
            raise CaptureError(f"Audio device not found: {device} ({e})") from e

    def _keep_output_running(self) -> None:
        # Windows' loopback only delivers audio while something is playing; playing
        # silence underneath keeps it flowing, so the stream doesn't stall between songs.
        import numpy as np

        silence = np.zeros((BLOCK, CHANNELS), dtype="float32")
        try:
            with self.speaker.player(samplerate=RATE, channels=CHANNELS) as player:
                while not self._stop.is_set():
                    player.play(silence)
        except Exception:
            pass

    def blocks(self) -> Iterator[bytes]:
        import numpy as np

        if sys.platform == "win32" and self.speaker is not None:
            threading.Thread(target=self._keep_output_running, daemon=True).start()
        try:
            with self.mic.recorder(samplerate=RATE, channels=CHANNELS, blocksize=BLOCK) as recorder:
                while not self._stop.is_set():
                    data = recorder.record(numframes=BLOCK)
                    if data.ndim == 1:
                        data = np.column_stack([data, data])
                    yield (np.clip(data[:, :CHANNELS], -1, 1) * 32767).astype("<i2").tobytes()
        except Exception as e:
            raise CaptureError(f"Can't capture audio: {e}") from e


def open_source(device: str) -> Source:
    if device in TEST_DEVICES:
        return GeneratedSource(device)
    if sys.platform == "darwin":
        return PortAudioSource(device)
    return LoopbackSource(device)


def list_devices() -> list[str]:
    if sys.platform == "darwin":
        import sounddevice as sd

        return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
    import soundcard as sc

    return [m.name for m in sc.all_microphones(include_loopback=True)]


def main() -> int:
    from .config import default_audio_device

    parser = argparse.ArgumentParser(description="Write live audio to stdout as 48 kHz 16-bit stereo PCM.")
    parser.add_argument("device", nargs="?", default=default_audio_device())
    parser.add_argument("--seconds", type=float, help="stop after this much audio")
    parser.add_argument("--list", action="store_true", help="list devices that can be captured")
    args = parser.parse_args()
    if args.list:
        print("\n".join(list_devices()))
        return 0
    try:
        source = open_source(args.device)
    except CaptureError as e:
        print(e, file=sys.stderr)
        return 2
    remaining = int(args.seconds * source.rate) * CHANNELS * 2 if args.seconds else None
    out = sys.stdout.buffer
    try:
        for block in source.blocks():
            if remaining is not None:
                block = block[:remaining]
                remaining -= len(block)
            out.write(block)
            out.flush()
            if remaining is not None and remaining <= 0:
                break
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    except CaptureError as e:
        print(e, file=sys.stderr)
        return 1
    finally:
        source.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
