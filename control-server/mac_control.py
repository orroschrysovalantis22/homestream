"""Playback control for macOS.

Two backends:
  * SpotifyController  - drives the Spotify desktop app through AppleScript.
                         Real play/pause, plus track info and artwork.
  * MediaKeyController - posts the system play/pause, next and previous media
                         keys, which browsers (Spotify Web, YouTube, ...) obey.
                         Needs the Accessibility permission, and macOS only has
                         one play/pause key, so play and pause both toggle.

HOMESTREAM_PLAYER picks one: "spotify", "browser", "auto" (Spotify when the
desktop app is running, media keys otherwise) or "dryrun" (log only).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import asdict, dataclass

log = logging.getLogger("homestream.control")

COMMANDS = ("play", "pause", "toggle", "next", "prev")


@dataclass
class PlayerStatus:
    backend: str
    state: str = "unknown"  # playing | paused | stopped | unknown | not-running
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    artwork: str | None = None
    warning: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class ControlError(RuntimeError):
    pass


async def _osascript(script: str, timeout: float = 5.0) -> str:
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise ControlError("osascript timed out (is a macOS permission prompt waiting?)")
    if proc.returncode != 0:
        raise ControlError(err.decode().strip() or f"osascript exited {proc.returncode}")
    return out.decode().strip()


async def spotify_running() -> bool:
    proc = await asyncio.create_subprocess_exec("pgrep", "-xq", "Spotify")
    return await proc.wait() == 0


class SpotifyController:
    name = "spotify"

    _verbs = {
        "play": "play",
        "pause": "pause",
        "toggle": "playpause",
        "next": "next track",
        "prev": "previous track",
    }

    # Guarded by "is running" so polling status never launches Spotify.
    _status_script = """
if application "Spotify" is running then
  tell application "Spotify"
    set s to player state as string
    if s is "stopped" then return "stopped"
    set t to current track
    return s & linefeed & (name of t) & linefeed & (artist of t) & linefeed & (album of t) & linefeed & (artwork url of t)
  end tell
else
  return "not-running"
end if
"""

    async def command(self, cmd: str) -> None:
        if not await spotify_running():
            raise ControlError("Spotify desktop app is not running")
        await _osascript(f'tell application "Spotify" to {self._verbs[cmd]}')

    async def status(self) -> PlayerStatus:
        # Also keeps osascript from compiling Spotify terms when it isn't installed.
        if not await spotify_running():
            return PlayerStatus(self.name, state="not-running", warning="Spotify desktop app is not running")
        try:
            out = await _osascript(self._status_script)
        except ControlError as e:
            return PlayerStatus(self.name, warning=str(e))
        lines = out.split("\n")
        if len(lines) < 5:
            return PlayerStatus(self.name, state=lines[0])
        state, title, artist, album, artwork = lines[:5]
        return PlayerStatus(self.name, state, title, artist, album, artwork or None)


NX_KEYTYPE_PLAY = 16
NX_KEYTYPE_NEXT = 17
NX_KEYTYPE_PREVIOUS = 18


def accessibility_trusted(prompt: bool = False) -> bool:
    """Whether this process may post synthetic input events.

    With prompt=True, macOS shows its "allow Accessibility" dialog if not.
    """
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    except ImportError:
        return False
    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))


def _post_media_key(key: int) -> None:
    # Imported lazily so the server still starts (in spotify/dryrun mode)
    # without PyObjC.
    import Quartz
    from AppKit import NSEvent

    for down in (True, False):
        flags = 0xA00 if down else 0xB00
        event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            14,  # NSEventTypeSystemDefined
            (0, 0),
            flags,
            0,
            0,
            None,
            8,  # NX_SUBTYPE_AUX_CONTROL_BUTTONS
            (key << 16) | flags,
            -1,
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event.CGEvent())


class MediaKeyController:
    name = "browser"

    _keys = {
        "play": NX_KEYTYPE_PLAY,
        "pause": NX_KEYTYPE_PLAY,
        "toggle": NX_KEYTYPE_PLAY,
        "next": NX_KEYTYPE_NEXT,
        "prev": NX_KEYTYPE_PREVIOUS,
    }

    async def command(self, cmd: str) -> None:
        if not accessibility_trusted():
            raise ControlError(
                "Accessibility permission missing: allow your terminal (or Python) in "
                "System Settings → Privacy & Security → Accessibility"
            )
        _post_media_key(self._keys[cmd])

    async def status(self) -> PlayerStatus:
        warning = None if accessibility_trusted() else "Accessibility permission missing"
        return PlayerStatus(self.name, warning=warning)


class DryRunController:
    name = "dryrun"

    def __init__(self) -> None:
        self._state = "paused"

    async def command(self, cmd: str) -> None:
        log.info("dryrun: %s", cmd)
        if cmd in ("play", "pause"):
            self._state = "playing" if cmd == "play" else "paused"
        elif cmd == "toggle":
            self._state = "paused" if self._state == "playing" else "playing"

    async def status(self) -> PlayerStatus:
        return PlayerStatus(self.name, self._state, title="Test tone", artist="HomeStream")


class AutoController:
    """Spotify desktop app when it's running, media keys otherwise."""

    name = "auto"

    def __init__(self) -> None:
        self.spotify = SpotifyController()
        self.keys = MediaKeyController()

    async def _pick(self):
        if shutil.which("osascript") and await spotify_running():
            return self.spotify
        return self.keys

    async def command(self, cmd: str) -> None:
        await (await self._pick()).command(cmd)

    async def status(self) -> PlayerStatus:
        return await (await self._pick()).status()


def make_controller(kind: str):
    controllers = {
        "auto": AutoController,
        "spotify": SpotifyController,
        "browser": MediaKeyController,
        "dryrun": DryRunController,
    }
    try:
        return controllers[kind]()
    except KeyError:
        raise SystemExit(f"HOMESTREAM_PLAYER must be one of {', '.join(controllers)} (got {kind!r})")
