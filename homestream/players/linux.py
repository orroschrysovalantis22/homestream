"""Playback control on Linux: MPRIS, via playerctl.

MPRIS is the standard D-Bus interface for media players on Linux. Spotify,
Firefox, Chrome/Chromium/Brave, VLC, mpv (with mpv-mpris) and most others
implement it, so this works with whatever is playing.

HOMESTREAM_MPRIS_PLAYER pins one player by name (e.g. "spotify", "firefox")
when several are running; otherwise playerctl uses the most recent one.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import shutil
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import ControlError, NoControlController, PlayerStatus, log, to_float

FIELDS = ("status", "playerName", "xesam:title", "xesam:artist", "xesam:album", "mpris:artUrl", "mpris:length", "position")
FORMAT = "\t".join("{{%s}}" % f for f in FIELDS)

APP_NAMES = {"chromium": "Chromium", "chrome": "Chrome", "firefox": "Firefox", "brave": "Brave", "spotify": "Spotify",
             "vlc": "VLC", "mpv": "mpv", "rhythmbox": "Rhythmbox", "elisa": "Elisa", "strawberry": "Strawberry"}


class MprisController:
    name = "mpris"

    _verbs = {"play": "play", "pause": "pause", "toggle": "play-pause", "next": "next", "prev": "previous"}

    def __init__(self, binary: str, player: str | None = None) -> None:
        self.binary = binary
        self.player = player
        self.info: dict = {}
        self.artwork: tuple[str, bytes] | None = None
        self.artwork_id: str | None = None
        self.artwork_url: str | None = None
        self._changed = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None

    def _cmd(self, *args: str) -> list[str]:
        return [self.binary, *([f"--player={self.player}"] if self.player else []), *args]

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._watch())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._proc and self._proc.returncode is None:
            self._proc.kill()

    async def wait_changed(self) -> None:
        await self._changed.wait()

    async def _watch(self) -> None:
        backoff = 1.0
        while True:
            self._proc = proc = await asyncio.create_subprocess_exec(
                *self._cmd("--follow", "metadata", "--format", FORMAT),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            started = time.monotonic()
            async for line in proc.stdout:
                self.update(line.decode(errors="replace").rstrip("\n"))
            await proc.wait()
            if time.monotonic() - started > 30:
                backoff = 1.0
            log.warning("playerctl --follow exited (%s); restarting in %.0fs", proc.returncode, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def update(self, line: str) -> None:
        """One line of `playerctl --follow` output; an empty line means the player went away."""
        values = line.split("\t")
        info = dict(zip(FIELDS, values)) if len(values) == len(FIELDS) and values[2] else {}
        self._set_artwork(info.get("mpris:artUrl", ""))
        info["_at"] = time.time()
        self.info = info
        self._changed.set()
        self._changed = asyncio.Event()

    def _set_artwork(self, url: str) -> None:
        self.artwork_url = None
        if url.startswith(("http://", "https://")):
            self.artwork_url, self.artwork, self.artwork_id = url, None, None
        elif url.startswith("file://"):
            path = Path(unquote(urlparse(url).path))
            try:
                data = path.read_bytes()
            except OSError:
                return
            artwork_id = hashlib.sha1(data).hexdigest()[:12]
            if artwork_id != self.artwork_id:
                mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
                self.artwork, self.artwork_id = (mime, data), artwork_id
        else:
            self.artwork = self.artwork_id = None

    async def command(self, cmd: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            *self._cmd(self._verbs[cmd]), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            raise ControlError("playerctl timed out")
        message = err.decode(errors="replace").strip()
        if proc.returncode != 0 or message.startswith("No player"):
            raise ControlError(message or f"playerctl exited {proc.returncode}")

    async def status(self) -> PlayerStatus:
        info = self.info
        if not info.get("xesam:title"):
            return PlayerStatus(self.name, state="idle")
        player = info.get("playerName", "").split(".")[0]
        if self.artwork_url:
            artwork = self.artwork_url
        elif self.artwork_id:
            artwork = f"/artwork?v={self.artwork_id}"
        else:
            artwork = None
        return PlayerStatus(
            self.name,
            state=(info.get("status") or "unknown").lower(),
            title=info.get("xesam:title"),
            artist=info.get("xesam:artist") or None,
            album=info.get("xesam:album") or None,
            artwork=artwork,
            app=APP_NAMES.get(player.lower(), player.capitalize() or None),
            duration=to_float(info.get("mpris:length"), scale=1e6),
            elapsed=to_float(info.get("position"), scale=1e6),
            elapsed_at=info.get("_at"),
        )


def make_linux_controller(kind: str):
    if kind not in ("auto", "mpris"):
        raise SystemExit(f"HOMESTREAM_PLAYER must be auto, mpris or dryrun on Linux (got {kind!r})")
    binary = shutil.which("playerctl")
    if not binary:
        reason = "Playback control needs playerctl: e.g. sudo apt install playerctl"
        if kind == "mpris":
            raise SystemExit(reason)
        log.warning("%s; streaming audio without controls", reason)
        return NoControlController("mpris", reason)
    return MprisController(binary, os.environ.get("HOMESTREAM_MPRIS_PLAYER") or None)
