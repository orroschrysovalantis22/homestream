"""Captures Mac audio with ffmpeg and fans it out to HTTP listeners as MP3.

ffmpeg only runs while someone is listening (plus a short grace period), so
the Mac isn't capturing audio, or showing the orange mic indicator, when
nobody is tuned in.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

log = logging.getLogger("homestream.audio")

READ_SIZE = 4096
# Per-listener backlog. MP3 frames are ~600 bytes at 192 kbps, so this is a
# couple of seconds. A listener that falls further behind loses its oldest
# audio instead of drifting away from live.
QUEUE_CHUNKS = 96
IDLE_STOP_SECONDS = 15
TEST_TONE = "test-tone"


class AudioBroadcaster:
    def __init__(self, device: str, bitrate: str = "192k") -> None:
        self.device = device
        self.bitrate = bitrate
        self.last_error: str | None = None
        self._listeners: set[asyncio.Queue[bytes | None]] = set()
        self._proc: asyncio.subprocess.Process | None = None
        self._pump_task: asyncio.Task | None = None
        self._idle_stop: asyncio.TimerHandle | None = None

    def ffmpeg_args(self) -> list[str]:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        if self.device == TEST_TONE:
            source = ["-re", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-af", "volume=0.2"]
        else:
            source = ["-f", "avfoundation", "-i", f":{self.device}"]
        return [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            *source,
            "-ac", "2", "-ar", "44100",
            "-c:a", "libmp3lame", "-b:a", self.bitrate,
            "-flush_packets", "1",
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

    async def listen(self):
        """Async iterator of MP3 bytes for one listener."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=QUEUE_CHUNKS)
        self._add(queue)
        try:
            while (chunk := await queue.get()) is not None:
                yield chunk
        finally:
            self._remove(queue)

    def _add(self, queue: asyncio.Queue[bytes | None]) -> None:
        self._listeners.add(queue)
        if self._idle_stop:
            self._idle_stop.cancel()
            self._idle_stop = None
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump())

    def _remove(self, queue: asyncio.Queue[bytes | None]) -> None:
        self._listeners.discard(queue)
        if not self._listeners and self._idle_stop is None:
            self._idle_stop = asyncio.get_running_loop().call_later(IDLE_STOP_SECONDS, self._stop_if_idle)

    def _stop_if_idle(self) -> None:
        self._idle_stop = None
        if not self._listeners and self._proc and self._proc.returncode is None:
            log.info("no listeners for %ss, stopping capture", IDLE_STOP_SECONDS)
            self._proc.terminate()

    def _broadcast(self, chunk: bytes | None) -> None:
        for queue in self._listeners:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(chunk)

    async def _pump(self) -> None:
        backoff = 1.0
        loop = asyncio.get_running_loop()
        while self._listeners:
            args = self.ffmpeg_args()
            log.info("starting capture: %s", " ".join(args))
            started = loop.time()
            try:
                self._proc = proc = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
            except FileNotFoundError:
                self.last_error = "ffmpeg not found; run setup.sh"
                log.error(self.last_error)
                await asyncio.sleep(5)
                continue
            stderr_task = asyncio.create_task(self._watch_stderr(proc))
            got_audio = False
            while chunk := await proc.stdout.read(READ_SIZE):
                if not got_audio:
                    got_audio = True
                    self.last_error = None
                self._broadcast(chunk)
            await proc.wait()
            await stderr_task
            self._proc = None
            if not self._listeners:
                break
            if loop.time() - started > 30:
                backoff = 1.0
            log.warning("capture exited (%s); restarting in %.0fs", proc.returncode, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

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
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
        if self._pump_task:
            await asyncio.gather(self._pump_task, return_exceptions=True)
