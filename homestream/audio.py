"""Captures what the computer is playing, encodes it to MP3 and fans it out to listeners.

Everything runs inside this process: capture (homestream.capture) and MP3
encoding (LAME, via lameenc) on a background thread, fan-out on the event
loop. Capture only runs while someone is listening (plus a short grace
period), so the computer isn't recording, or showing a microphone indicator,
when nobody is tuned in.

The stream is cut into whole MP3 frames (~24 ms each). A listener that falls
behind loses whole frames, which players skip cleanly; dropping arbitrary
bytes instead would make the decoder play noise.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
from dataclasses import dataclass, field

from .capture import CHANNELS, TEST_DEVICES, CaptureError, open_source

log = logging.getLogger("homestream.audio")

# Per-listener backlog in MP3 frames (~24 ms each): about 1.2 s. A listener
# that falls further behind loses its oldest audio instead of drifting away.
QUEUE_FRAMES = 48
IDLE_STOP_SECONDS = 15
TEST_TONE, TEST_SIGNAL = TEST_DEVICES

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


def make_encoder(rate: int, kbps: int):
    import lameenc

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(kbps)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(CHANNELS)
    encoder.set_quality(2)  # 2 = high quality; encoding is far faster than real time anyway
    return encoder


def parse_kbps(bitrate: str) -> int:
    return int(str(bitrate).lower().removesuffix("k"))


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
        self.last_error: str | None = None
        self.capture_starts = 0
        self._recent: collections.deque = collections.deque()
        self._listeners: set[Listener] = set()
        # Browsers may open the same stream twice (Safari probes first), so the
        # timing for an id comes from whichever connection is actually receiving audio.
        self._by_id: dict[str, Listener] = {}
        self._source = None
        self._pump_task: asyncio.Task | None = None
        self._idle_stop: asyncio.TimerHandle | None = None

    def status(self) -> dict:
        return {
            "device": self.device,
            "bitrate": self.bitrate,
            "listeners": len(self._listeners),
            "capturing": self._source is not None,
            "capture_starts": self.capture_starts,
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
        listener = Listener(listener_id or f"anon-{time.monotonic_ns()}")
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
        if not self._listeners and self._source is not None:
            log.info("no listeners for %ss, stopping capture", IDLE_STOP_SECONDS)
            self._source.stop()

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
                source = await asyncio.to_thread(open_source, self.device)
                encoder = make_encoder(source.rate, parse_kbps(self.bitrate))
            except (CaptureError, ImportError, ValueError) as e:
                self.last_error = str(e)
                log.error("can't start capture: %s", e)
                await asyncio.sleep(5)
                continue
            self._source = source
            self.capture_starts += 1
            log.info("capturing %r at %d Hz", self.device, source.rate)
            mp3_chunks: asyncio.Queue = asyncio.Queue()
            threading.Thread(target=self._encode, args=(source, encoder, loop, mp3_chunks), daemon=True).start()
            pending = bytearray()
            got_audio = False
            while (chunk := await mp3_chunks.get()) is not None:
                if isinstance(chunk, Exception):
                    self.last_error = str(chunk)
                    log.warning("capture failed: %s", chunk)
                    continue
                if not got_audio:
                    got_audio = True
                    self.last_error = None
                pending += chunk
                for frame, duration in split_mp3_frames(pending):
                    self._broadcast(frame, duration)
            self._source = None
            self._recent.clear()
            if not self._listeners:
                break
            if loop.time() - started > 30:
                backoff = 1.0
            log.warning("capture stopped; restarting in %.0fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    @staticmethod
    def _encode(source, encoder, loop: asyncio.AbstractEventLoop, out: asyncio.Queue) -> None:
        """Background thread: capture -> MP3, handing encoded bytes to the event loop."""
        try:
            for pcm in source.blocks():
                mp3 = encoder.encode(pcm)
                if mp3:
                    loop.call_soon_threadsafe(out.put_nowait, bytes(mp3))
        except Exception as e:
            loop.call_soon_threadsafe(out.put_nowait, e)
        finally:
            source.stop()
            try:
                loop.call_soon_threadsafe(out.put_nowait, None)
            except RuntimeError:
                pass  # the event loop has already shut down

    def end_streams(self) -> None:
        """Finish every listener's response so the server can shut down promptly."""
        self._broadcast(None)

    async def close(self) -> None:
        if self._idle_stop:
            self._idle_stop.cancel()
        self._listeners.clear()
        self._by_id.clear()
        if self._source is not None:
            self._source.stop()
        if self._pump_task:
            await asyncio.gather(self._pump_task, return_exceptions=True)
