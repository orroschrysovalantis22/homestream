"""HomeStream control server.

Serves the phone page, the live audio stream and the playback API from one
address.

Who gets in: devices on your Tailscale network (Tailscale has already proven
they're yours), plus anyone presenting the shared token, as an
"Authorization: Bearer <token>" header or the cookie set by POST /auth. So
your own phone needs no password, while someone on the Mac's Wi-Fi does.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from audio_stream import AudioBroadcaster
from mac_control import COMMANDS, ControlError, make_controller

ROOT = Path(__file__).resolve().parent.parent
COOKIE = "homestream_token"

log = logging.getLogger("homestream")


def load_env_file(path: Path) -> None:
    """Minimal .env reader; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env_file(Path(os.environ.get("HOMESTREAM_ENV_FILE", ROOT / ".env")))

TOKEN = os.environ.get("HOMESTREAM_TOKEN", "")
TRUST_TAILSCALE = os.environ.get("HOMESTREAM_TRUST_TAILSCALE", "1") != "0"
TAILSCALE_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
HOST = os.environ.get("HOMESTREAM_HOST", "0.0.0.0")
PORT = int(os.environ.get("HOMESTREAM_PORT", "8765"))

audio = AudioBroadcaster(
    device=os.environ.get("HOMESTREAM_AUDIO_DEVICE", "BlackHole 2ch"),
    bitrate=os.environ.get("HOMESTREAM_BITRATE", "192k"),
    prebuffer=float(os.environ.get("HOMESTREAM_PREBUFFER", "1.0")),
)
player = make_controller(os.environ.get("HOMESTREAM_PLAYER", "auto"))
# Set when the server is asked to quit, so long-lived responses can finish.
shutting_down = asyncio.Event()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if hasattr(player, "start"):
        await player.start()
    yield
    await audio.close()
    if hasattr(player, "close"):
        await player.close()


app = FastAPI(title="HomeStream", lifespan=lifespan, docs_url=None, redoc_url=None)
# Icons and the web app manifest; public so the home-screen icon loads before sign-in.
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")


def token_ok(candidate: str | None) -> bool:
    return bool(candidate) and secrets.compare_digest(candidate.encode(), TOKEN.encode())


def is_tailscale_address(host: str | None) -> bool:
    try:
        ip = ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in network for network in TAILSCALE_NETWORKS)


def via_tailscale(request: Request) -> bool:
    """A direct connection from another device on your tailnet.

    Both ends must be Tailscale addresses: the phone's, and the Mac address it
    connected to. Checking the Mac's side too means a device on the local
    network can't get in just because its own address happens to look similar.
    """
    if not TRUST_TAILSCALE:
        return False
    server = (request.scope.get("server") or (None,))[0]
    client = request.client.host if request.client else None
    return is_tailscale_address(server) and is_tailscale_address(client)


def has_token(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    bearer = header[7:] if header.lower().startswith("bearer ") else None
    return token_ok(bearer) or token_ok(request.cookies.get(COOKIE))


def require_access(request: Request) -> None:
    if not (via_tailscale(request) or has_token(request)):
        raise HTTPException(401, "not on your Tailscale network, and no valid token")


authed = [Depends(require_access)]


class AuthBody(BaseModel):
    token: str


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-cache"})


@app.post("/auth", status_code=204)
async def auth(body: AuthBody, request: Request, response: Response):
    if not token_ok(body.token):
        raise HTTPException(401, "wrong token")
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        COOKIE, body.token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax", secure=https
    )


@app.post("/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(COOKIE)


async def current_status() -> dict:
    return {"player": (await player.status()).to_dict(), "stream": audio.status()}


@app.get("/status", dependencies=authed)
async def status(request: Request):
    # "access" lets the page hide the token controls when Tailscale let us in.
    return {**await current_status(), "access": "tailscale" if via_tailscale(request) else "token"}


async def wait_for_change(timeout: float) -> None:
    """Return when the player reports a change, on shutdown, or after `timeout`."""
    waiters = [asyncio.create_task(shutting_down.wait())]
    if hasattr(player, "wait_changed"):
        waiters.append(asyncio.create_task(player.wait_changed()))
    _, pending = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()


@app.get("/events", dependencies=authed)
async def events():
    """Server-sent events: the same JSON as /status, pushed whenever it changes."""

    async def stream():
        last, last_sent = None, 0.0
        loop = asyncio.get_running_loop()
        while not shutting_down.is_set():
            payload = json.dumps(await current_status(), separators=(",", ":"))
            if payload != last:
                yield f"data: {payload}\n\n"
                last, last_sent = payload, loop.time()
            elif loop.time() - last_sent > 15:
                yield ": keep-alive\n\n"
                last_sent = loop.time()
            await wait_for_change(timeout=1.0)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.get("/artwork", dependencies=authed)
async def artwork():
    art = getattr(player, "artwork", None)
    if not art:
        raise HTTPException(404, "no artwork")
    mime, data = art
    # The URL carries a version (?v=...), so each image can be cached for good.
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=31536000, immutable"})


def command_route(cmd: str):
    async def run():
        try:
            await player.command(cmd)
        except ControlError as e:
            raise HTTPException(409, str(e))
        return {"ok": True, "command": cmd}

    return run


for _cmd in COMMANDS:
    app.add_api_route(f"/{_cmd}", command_route(_cmd), methods=["POST"], dependencies=authed, name=_cmd)


@app.get("/stream.mp3", dependencies=authed)
async def stream(id: str | None = None):
    return StreamingResponse(
        audio.listen(id),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache, no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/stream-info", dependencies=authed)
async def stream_info(id: str, delay: float | None = None):  # delay: the page's own measurement, for logs
    """When this listener's audio was captured, so the page can measure its delay."""
    info = audio.listener_info(id)
    if info is None:
        raise HTTPException(404, "no such listener")
    return {**info, "now": time.time()}


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if TOKEN and len(TOKEN) < 16:
        raise SystemExit("HOMESTREAM_TOKEN is too short (16+ characters); setup.sh generates a good one.")
    if not TOKEN and not TRUST_TAILSCALE:
        raise SystemExit("Nobody could connect: set HOMESTREAM_TOKEN or HOMESTREAM_TRUST_TAILSCALE=1.")
    if not TOKEN:
        log.warning("no HOMESTREAM_TOKEN: only devices on your Tailscale network can connect")
    class Server(uvicorn.Server):
        # Audio streams and event streams never end on their own; close them
        # when asked to quit instead of letting uvicorn wait on them.
        def handle_exit(self, sig, frame):
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(audio.end_streams)
            loop.call_soon_threadsafe(shutting_down.set)
            super().handle_exit(sig, frame)

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="info", timeout_graceful_shutdown=3)
    Server(config).run()


if __name__ == "__main__":
    main()
