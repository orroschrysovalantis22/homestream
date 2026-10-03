"""Playback control on macOS.

Backends:
  * NowPlayingController - talks to macOS's system-wide "Now Playing" through
                           the media-control CLI. Works with anything that shows
                           up in Control Center (Brave/Chrome/Safari tabs,
                           Spotify, Music, ...): real play/pause, next/previous,
                           title, artist, artwork and progress. No permissions.
  * SpotifyController    - drives the Spotify desktop app through AppleScript.
  * MediaKeyController   - posts the system media keys. Needs the Accessibility
                           permission, and play and pause both toggle.

HOMESTREAM_PLAYER picks one: "auto" (Now Playing when media-control is
installed, otherwise Spotify app / media keys), "nowplaying", "spotify" or
"browser" (media keys).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import time
from functools import lru_cache

from . import ControlError, PlayerStatus, iso_to_epoch as _epoch, log, to_float as _float


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
    return s & linefeed & (name of t) & linefeed & (artist of t) & linefeed & (album of t) & linefeed & (artwork url of t) & linefeed & (duration of t) & linefeed & (player position as string)
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
        if len(lines) < 7:
            return PlayerStatus(self.name, state=lines[0])
        state, title, artist, album, artwork, duration_ms, position = lines[:7]
        return PlayerStatus(
            self.name, state, title, artist, album, artwork or None, app="Spotify",
            duration=_float(duration_ms, scale=1000), elapsed=_float(position), elapsed_at=time.time(),
        )


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


@lru_cache(maxsize=32)
def app_display_name(bundle_id: str | None) -> str | None:
    """'com.brave.Browser' -> 'Brave Browser', using the installed app's name."""
    if not bundle_id:
        return None
    try:
        from AppKit import NSWorkspace

        url = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(bundle_id)
        if url is not None:
            return url.lastPathComponent().removesuffix(".app")
    except ImportError:
        pass
    return bundle_id.rsplit(".", 1)[-1]


class NowPlayingController:
    """Whatever macOS shows as Now Playing, via `media-control stream`.

    Keeps one long-running `media-control stream` process and caches the
    latest state, so status() is instant and wait_changed() lets the server
    push updates the moment the track changes.
    """

    name = "nowplaying"

    _verbs = {
        "play": "play",
        "pause": "pause",
        "toggle": "toggle-play-pause",
        "next": "next-track",
        "prev": "previous-track",
    }

    def __init__(self, binary: str) -> None:
        self.binary = binary
        self.info: dict = {}
        self.artwork: tuple[str, bytes] | None = None  # (mime type, image bytes)
        self.artwork_id: str | None = None
        self._changed = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None

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
                self.binary, "stream", "--no-diff",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=16 * 1024 * 1024,  # lines carry base64 artwork
            )
            started = time.monotonic()
            async for line in proc.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("type") == "data":
                    self._update(message.get("payload") or {})
            await proc.wait()
            if time.monotonic() - started > 30:
                backoff = 1.0
            log.warning("media-control stream exited (%s); restarting in %.0fs", proc.returncode, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _update(self, payload: dict) -> None:
        artwork = payload.pop("artworkData", None)
        if artwork:
            try:
                data = base64.b64decode(artwork)
            except ValueError:
                data = b""
            if data and (not self.artwork or self.artwork[1] != data):
                self.artwork = (payload.get("artworkMimeType") or "image/jpeg", data)
                self.artwork_id = hashlib.sha1(data).hexdigest()[:12]
        elif not payload.get("title"):
            self.artwork = self.artwork_id = None
        self.info = payload
        self._changed.set()
        self._changed = asyncio.Event()

    async def command(self, cmd: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.binary, self._verbs[cmd], stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            raise ControlError("media-control timed out")
        if proc.returncode != 0:
            raise ControlError(err.decode().strip() or f"media-control exited {proc.returncode}")

    async def status(self) -> PlayerStatus:
        info = self.info
        if not info.get("title"):
            return PlayerStatus(self.name, state="idle")
        return PlayerStatus(
            self.name,
            state="playing" if info.get("playing") else "paused",
            title=info.get("title"),
            artist=info.get("artist") or None,
            album=info.get("album") or None,
            artwork=f"/artwork?v={self.artwork_id}" if self.artwork_id else None,
            app=app_display_name(info.get("bundleIdentifier")),
            duration=_float(info.get("duration")),
            elapsed=_float(info.get("elapsedTime")),
            elapsed_at=_epoch(info.get("timestamp")),
        )


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


def media_control_path() -> str | None:
    # Also look in Homebrew's prefixes, in case PATH doesn't include them.
    for candidate in (shutil.which("media-control"), "/opt/homebrew/bin/media-control", "/usr/local/bin/media-control"):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def make_mac_controller(kind: str):
    if kind in ("auto", "nowplaying"):
        binary = media_control_path()
        if binary:
            return NowPlayingController(binary)
        if kind == "nowplaying":
            raise SystemExit("HOMESTREAM_PLAYER=nowplaying needs media-control: brew install media-control")
    controllers = {"auto": AutoController, "spotify": SpotifyController, "browser": MediaKeyController}
    try:
        return controllers[kind]()
    except KeyError:
        raise SystemExit(
            f"HOMESTREAM_PLAYER must be one of auto, nowplaying, spotify, browser, dryrun on macOS (got {kind!r})"
        )
