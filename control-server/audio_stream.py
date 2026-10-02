"""Captures Mac audio, encodes it to MP3 with ffmpeg and fans it out to HTTP listeners.

ffmpeg only runs while someone is listening (plus a short grace period), so
the Mac isn't capturing audio, or showing the orange mic indicator, when
nobody is tuned in.

The stream is cut into whole MP3 frames (~26 ms each). A listener that falls
behind loses whole frames, which players skip cleanly; dropping arbitrary
bytes instead would make the decoder play noise.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("homestream.audio")

READ_SIZE = 4096
# Per-listener backlog in MP3 frames (~26 ms each): about 1.2 s. A listener
# that falls further behind loses its oldest audio instead of drifting away
# from live.
QUEUE_FRAMES = 48
IDLE_STOP_SECONDS = 15
TEST_TONE = "test-tone"
# A 440 Hz tone with a short 1 kHz beep at the start of every second, so
# delay and dropouts can be measured from a recording of what a phone plays.
TEST_SIGNAL = "test-signal"
# ffmpeg's own macOS capture loses ~12% of BlackHole's audio (heard as crackle),
# so real devices are captured by this PortAudio helper and piped into ffmpeg.
CAPTURE_SCRIPT = Path(__file__).with_name("capture.py")

_BITRATES = {  # kbps by bitrate index 1..14, for MPEG-1 and MPEG-2/2.5 Layer III
    3: (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    2: (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def mp3_frame_info(header: bytes) -> tuple[int, float] | None:
    """(frame length in bytes, frame duration in seconds) for a Layer III header."""
    if len(header) < 4 or header[0] != 0xFF or header[1] & 0xE0 != 0xE0:
        return None
    version = (header[1] >> 3) & 3  # 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5
    layer = (header[1] >> 1) & 3  # 1 = Layer III
    bitrate_index = header[2] >> 4
    rate_index = (header[2] >> 2) & 3
    if version == 1 or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
        return None
    kbps = _BITRATES[3 if version == 3 else 2][bitrate_index - 1]
    sample_rate = _SAMPLE_RATES[version][rate_index]
    samples = 1152 if version == 3 else 576
    length = samples // 8 * kbps * 1000 // sample_rate + ((header[2] >> 1) & 1)
    return length, samples / sample_rate


def split_mp3_frames(buffer: bytearray) -> list[tuple[bytes, float]]:
    """Remove and return the complete frames at the start of `buffer`."""
    frames = []
    i = 0
    while len(buffer) - i >= 4:
        info = mp3_frame_info(buffer[i:i + 4])
        if info is None:
            i += 1  # not a frame header: resync
            continue
        length, duration = info
        if len(buffer) - i < length:
            break
        frames.append((bytes(buffer[i:i + length]), duration))
        i += length
    del buffer[:i]
    return frames


@dataclass(eq=False)
class Listener:
    id: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_FRAMES))
    started_at: float | None = None  # capture time of the first frame sent
    dropped_seconds: float = 0.0


class AudioBroadcaster:
    def __init__(self, device: str, bitrate: str = "192k", prebuffer: float = 0.0) -> None:
        self.device = device
        self.bitrate = bitrate
        # Seconds of recent audio sent to a new listener straight away.
        self.prebuffer = prebuffer
        self._recent: collections.deque = collections.deque()
        self.last_error: str | None = None
        self._listeners: set[Listener] = set()
        # Browsers may open the same stream twice (Safari probes first), so the
        # timing for an id comes from whichever connection is actually receiving audio.
        self._by_id: dict[str, Listener] = {}
        self._proc: asyncio.subprocess.Process | None = None
        self._capture: asyncio.subprocess.Process | None = None
        self._pump_task: asyncio.Task | None = None
        self._idle_stop: asyncio.TimerHandle | None = None

    def capture_command(self) -> list[str] | None:
        """The PortAudio capture process feeding ffmpeg, or None for test sources."""
        if self.device in (TEST_TONE, TEST_SIGNAL):
            return None
        return [sys.executable, str(CAPTURE_SCRIPT), self.device]

    def ffmpeg_args(self, rate: int = 44100) -> list[str]:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        if self.device == TEST_TONE:
            source = ["-re", "-readrate_initial_burst", "0", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-af", "volume=0.2"]
        elif self.device == TEST_SIGNAL:
            expr = "0.2*sin(2*PI*440*t)*lt(mod(t\\,1)\\,0.9)+0.5*sin(2*PI*1000*t)*lt(mod(t\\,1)\\,0.05)"
            # No initial burst: real-time from the first sample, like a real capture.
            source = ["-re", "-readrate_initial_burst", "0", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=44100"]
        else:
            source = ["-f", "s16le", "-ar", str(rate), "-ac", "2", "-i", "pipe:0"]
        return [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            *source,
            "-ac", "2", "-ar", str(rate),
            "-c:a", "libmp3lame", "-b:a", self.bitrate,
            "-flush_packets", "1",
            # Bare frames: no ID3 tag or Xing header that could be mistaken for audio.
            "-id3v2_version", "0", "-write_xing", "0",
            "-f", "mp3", "pipe:1",
        ]

    def status(self) -> dict:
        return {
            "device": self.device,
            "bitrate": self.bitrate,
            "listeners": len(self._listeners),
            "capturing": self._proc is not None and self._proc.returncode is None,
            "error": self.last_error,
        }

    def listener_info(self, listener_id: str) -> dict | None:
        """Timing for one listener, so the page can work out its real delay."""
        listener = self._by_id.get(listener_id)
        if listener is None:
            return None
        return {"started_at": listener.started_at, "dropped_seconds": listener.dropped_seconds}

    async def listen(self, listener_id: str | None = None):
        """Async iterator of MP3 frames for one listener."""
        listener = Listener(listener_id or f"anon-{id(object())}-{time.monotonic_ns()}")
        self._add(listener)
        try:
            while (item := await listener.queue.get()) is not None:
                captured_at, frame = item
                if listener.started_at is None:
                    listener.started_at = captured_at
                    self._by_id[listener.id] = listener
                yield frame
        finally:
            self._remove(listener)

    def _add(self, listener: Listener) -> None:
        self._listeners.add(listener)
        for item in list(self._recent)[-QUEUE_FRAMES:]:
            listener.queue.put_nowait(item)
        if self._idle_stop:
            self._idle_stop.cancel()
            self._idle_stop = None
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump())

    def _remove(self, listener: Listener) -> None:
        self._listeners.discard(listener)
        if self._by_id.get(listener.id) is listener:
            del self._by_id[listener.id]
        if not self._listeners and self._idle_stop is None:
            self._idle_stop = asyncio.get_running_loop().call_later(IDLE_STOP_SECONDS, self._stop_if_idle)

    def _stop_if_idle(self) -> None:
        self._idle_stop = None
        if not self._listeners and self._proc and self._proc.returncode is None:
            log.info("no listeners for %ss, stopping capture", IDLE_STOP_SECONDS)
            self._terminate()

    def _terminate(self) -> None:
        for p in (self._capture, self._proc):
            if p and p.returncode is None:
                p.terminate()

    def _broadcast(self, frame: bytes | None, duration: float = 0.0) -> None:
        item = None if frame is None else (time.time(), frame)
        if item and self.prebuffer:
            self._recent.append(item)
            while self._recent and item[0] - self._recent[0][0] > self.prebuffer:
                self._recent.popleft()
        for listener in list(self._listeners):
            if listener.queue.full():
                listener.queue.get_nowait()
                listener.dropped_seconds += duration
            listener.queue.put_nowait(item)

    async def _pump(self) -> None:
        backoff = 1.0
        loop = asyncio.get_running_loop()
        while self._listeners:
            started = loop.time()
            try:
                proc = await self._start_processes()
            except (OSError, ValueError, ImportError) as e:
                self.last_error = str(e) or type(e).__name__
                log.error("can't start capture: %s", self.last_error)
                await asyncio.sleep(5)
                continue
            stderr_tasks = [asyncio.create_task(self._watch_stderr(p)) for p in (proc, self._capture) if p]
            pending = bytearray()
            got_audio = False
            while chunk := await proc.stdout.read(READ_SIZE):
                if not got_audio:
                    got_audio = True
                    self.last_error = None
                pending += chunk
                for frame, duration in split_mp3_frames(pending):
                    self._broadcast(frame, duration)
            await proc.wait()
            self._recent.clear()
            if self._capture and self._capture.returncode is None:
                self._capture.terminate()
            await asyncio.gather(*stderr_tasks)
            self._proc = self._capture = None
            if not self._listeners:
                break
            if loop.time() - started > 30:
                backoff = 1.0
            log.warning("capture exited (%s); restarting in %.0fs", proc.returncode, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _start_processes(self) -> asyncio.subprocess.Process:
        """Start ffmpeg (and the capture helper feeding it); return ffmpeg."""
        capture_cmd = self.capture_command()
        if capture_cmd is None:
            args = self.ffmpeg_args()
            log.info("starting encoder: %s", " ".join(args))
            self._proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            return self._proc

        from capture import device_rate

        try:
            rate = device_rate(self.device)
        except ValueError:
            raise ValueError(f"Audio device not found: {self.device}") from None
        args = self.ffmpeg_args(rate)
        log.info("starting capture of %r at %d Hz", self.device, rate)
        read_fd, write_fd = os.pipe()
        try:
            self._capture = await asyncio.create_subprocess_exec(
                *capture_cmd, stdin=asyncio.subprocess.DEVNULL, stdout=write_fd, stderr=asyncio.subprocess.PIPE
            )
            self._proc = await asyncio.create_subprocess_exec(
                *args, stdin=read_fd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        finally:
            os.close(read_fd)
            os.close(write_fd)
        return self._proc

    async def _watch_stderr(self, proc: asyncio.subprocess.Process) -> None:
        async for line in proc.stderr:
            text = line.decode(errors="replace").strip()
            if text:
                self.last_error = text
                log.warning("ffmpeg: %s", text)

    def end_streams(self) -> None:
        """Finish every listener's response so the server can shut down promptly."""
        self._broadcast(None)

    async def close(self) -> None:
        if self._idle_stop:
            self._idle_stop.cancel()
        self._listeners.clear()
        self._by_id.clear()
        self._terminate()
        if self._pump_task:
            await asyncio.gather(self._pump_task, return_exceptions=True)
