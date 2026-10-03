"""Playback control on Windows: the Global System Media Transport Controls.

That's the API behind the media panel in Windows' volume flyout. Spotify,
Chrome, Edge, Brave, Firefox, the Media Player app and most others report to
it, so this works with whatever is playing: title, artist, artwork, progress,
and real play/pause/next/previous. Needs the winrt-* packages (installed on
Windows by pip).
"""

from __future__ import annotations

import asyncio
import hashlib
import time

from . import ControlError, PlayerStatus, log

POLL_SECONDS = 0.5
PLAYBACK_STATES = {3: "stopped", 4: "playing", 5: "paused"}  # GlobalSystemMediaTransportControlsSessionPlaybackStatus
APP_NAMES = {"spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge", "brave": "Brave", "firefox": "Firefox",
             "308046b0af4a39cb": "Firefox", "microsoft.zunemusic": "Media Player", "vlc": "VLC"}


def app_name(app_id: str | None) -> str | None:
    """'Spotify.exe' -> 'Spotify', 'Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic' -> 'Media Player'."""
    if not app_id:
        return None
    key = app_id.split("!")[0].split("_")[0].lower().removesuffix(".exe")
    return APP_NAMES.get(key, key.split(".")[-1].capitalize())


def read_session(session, props) -> dict:
    """The parts of a session we show, as plain values (kept separate so it can be tested anywhere)."""
    playback = session.get_playback_info()
    timeline = session.get_timeline_properties()
    duration = (timeline.end_time - timeline.start_time).total_seconds()
    updated = timeline.last_updated_time
    return {
        "title": props.title or None,
        "artist": props.artist or None,
        "album": props.album_title or None,
        "state": PLAYBACK_STATES.get(int(playback.playback_status), "unknown"),
        "app": app_name(session.source_app_user_model_id),
        "duration": duration if duration > 0 else None,
        "elapsed": timeline.position.total_seconds() if duration > 0 else None,
        "elapsed_at": updated.timestamp() if updated and updated.year > 1601 else time.time(),
        "has_thumbnail": props.thumbnail is not None,
    }


class WindowsMediaController:
    name = "windows"

    _methods = {
        "play": "try_play_async",
        "pause": "try_pause_async",
        "toggle": "try_toggle_play_pause_async",
        "next": "try_skip_next_async",
        "prev": "try_skip_previous_async",
    }

    def __init__(self) -> None:
        self.info: dict = {}
        self.artwork: tuple[str, bytes] | None = None
        self.artwork_id: str | None = None
        self._manager = None
        self._changed = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._watch())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def wait_changed(self) -> None:
        await self._changed.wait()

    async def _session(self):
        if self._manager is None:
            from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

            self._manager = await Manager.request_async()
        return self._manager.get_current_session()

    async def _watch(self) -> None:
        while True:
            try:
                session = await self._session()
                if session is None:
                    info = {}
                else:
                    props = await session.try_get_media_properties_async()
                    info = read_session(session, props)
                    if info["has_thumbnail"] and (info["title"], info["artist"]) != (
                        self.info.get("title"), self.info.get("artist")
                    ):
                        await self._load_artwork(props.thumbnail)
                    elif not info["has_thumbnail"]:
                        self.artwork = self.artwork_id = None
                if info != self.info:
                    self.info = info
                    self._changed.set()
                    self._changed = asyncio.Event()
            except Exception as e:  # keep watching whatever a misbehaving player does
                log.warning("media session read failed: %s", e)
                self._manager = None
            await asyncio.sleep(POLL_SECONDS)

    async def _load_artwork(self, thumbnail) -> None:
        try:
            from winrt.windows.storage.streams import Buffer, InputStreamOptions

            stream = await thumbnail.open_read_async()
            buffer = Buffer(stream.size)
            await stream.read_async(buffer, buffer.capacity, InputStreamOptions.READ_AHEAD)
            data = bytes(buffer)
        except Exception as e:
            log.info("couldn't read artwork: %s", e)
            self.artwork = self.artwork_id = None
            return
        self.artwork = (getattr(stream, "content_type", "") or "image/png", data)
        self.artwork_id = hashlib.sha1(data, usedforsecurity=False).hexdigest()[:12]

    async def command(self, cmd: str) -> None:
        session = await self._session()
        if session is None:
            raise ControlError("nothing is playing")
        if not await getattr(session, self._methods[cmd])():
            raise ControlError(f"the player didn't accept {cmd}")

    async def status(self) -> PlayerStatus:
        info = self.info
        if not info.get("title"):
            return PlayerStatus(self.name, state="idle")
        return PlayerStatus(
            self.name,
            state=info["state"],
            title=info["title"],
            artist=info["artist"],
            album=info["album"],
            artwork=f"/artwork?v={self.artwork_id}" if self.artwork_id else None,
            app=info["app"],
            duration=info["duration"],
            elapsed=info["elapsed"],
            elapsed_at=info["elapsed_at"],
        )


def make_windows_controller(kind: str):
    if kind not in ("auto", "windows"):
        raise SystemExit(f"HOMESTREAM_PLAYER must be auto, windows or dryrun on Windows (got {kind!r})")
    return WindowsMediaController()
