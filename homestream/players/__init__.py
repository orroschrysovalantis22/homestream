"""What's playing on this computer, and playback control. One backend per OS.

  macOS    macos.py    Now Playing (media-control), Spotify app, media keys
  Linux    linux.py    MPRIS, via playerctl: Spotify, browsers, VLC, ...
  Windows  windows.py  Global System Media Transport Controls: anything in the volume flyout

HOMESTREAM_PLAYER picks one; "auto" (the default) picks the best for this OS
and "dryrun" is a pretend player for development.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime

log = logging.getLogger("homestream.control")

COMMANDS = ("play", "pause", "toggle", "next", "prev")


@dataclass
class PlayerStatus:
    backend: str
    state: str = "unknown"  # playing | paused | stopped | idle | unknown | not-running
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    artwork: str | None = None  # URL
    warning: str | None = None
    app: str | None = None  # which app is playing, e.g. "Brave Browser"
    duration: float | None = None  # seconds
    elapsed: float | None = None  # seconds into the track...
    elapsed_at: float | None = None  # ...as of this Unix time

    def to_dict(self) -> dict:
        return asdict(self)


class ControlError(RuntimeError):
    pass


def to_float(value, scale: float = 1.0) -> float | None:
    try:
        return float(str(value).replace(",", ".")) / scale
    except (TypeError, ValueError):
        return None


def iso_to_epoch(timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class DryRunController:
    """A pretend player, so the page can be developed without real playback."""

    name = "dryrun"
    tracks = 3
    duration = 180.0

    def __init__(self) -> None:
        self._state = "paused"
        self._track = 1
        self._elapsed = 0.0
        self._since = time.time()

    def _position(self) -> float:
        if self._state == "playing":
            return self._elapsed + time.time() - self._since
        return self._elapsed

    async def command(self, cmd: str) -> None:
        log.info("dryrun: %s", cmd)
        self._elapsed, self._since = self._position(), time.time()
        if cmd == "toggle":
            cmd = "pause" if self._state == "playing" else "play"
        if cmd in ("play", "pause"):
            self._state = "playing" if cmd == "play" else "paused"
        elif cmd in ("next", "prev"):
            step = 1 if cmd == "next" else -1
            self._track = (self._track - 1 + step) % self.tracks + 1
            self._elapsed = 0.0

    async def status(self) -> PlayerStatus:
        return PlayerStatus(
            self.name, self._state, title=f"Test tone {self._track}", artist="HomeStream",
            app="Dry run", duration=self.duration, elapsed=self._position(), elapsed_at=time.time(),
        )


def make_controller(kind: str = "auto"):
    if kind == "dryrun":
        return DryRunController()
    if sys.platform == "darwin":
        from .macos import make_mac_controller

        return make_mac_controller(kind)
    if sys.platform.startswith("linux"):
        from .linux import make_linux_controller

        return make_linux_controller(kind)
    if sys.platform == "win32":
        from .windows import make_windows_controller

        return make_windows_controller(kind)
    raise SystemExit(f"Playback control isn't supported on {sys.platform}; use HOMESTREAM_PLAYER=dryrun")
