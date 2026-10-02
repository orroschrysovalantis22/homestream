"""HomeStream control server.

Serves the phone page, the live audio stream and the playback API from one
address. Every route except the page itself needs the shared token, sent as
an "Authorization: Bearer <token>" header or as the cookie set by POST /auth.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
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
HOST = os.environ.get("HOMESTREAM_HOST", "0.0.0.0")
PORT = int(os.environ.get("HOMESTREAM_PORT", "8765"))

audio = AudioBroadcaster(
    device=os.environ.get("HOMESTREAM_AUDIO_DEVICE", "BlackHole 2ch"),
    bitrate=os.environ.get("HOMESTREAM_BITRATE", "192k"),
)
player = make_controller(os.environ.get("HOMESTREAM_PLAYER", "auto"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await audio.close()


app = FastAPI(title="HomeStream", lifespan=lifespan, docs_url=None, redoc_url=None)
# Icons and the web app manifest; public so the home-screen icon loads before sign-in.
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")


def token_ok(candidate: str | None) -> bool:
    return bool(candidate) and secrets.compare_digest(candidate.encode(), TOKEN.encode())


def require_token(request: Request) -> None:
    header = request.headers.get("authorization", "")
    bearer = header[7:] if header.lower().startswith("bearer ") else None
    if not (token_ok(bearer) or token_ok(request.cookies.get(COOKIE))):
        raise HTTPException(401, "missing or wrong token")


authed = [Depends(require_token)]


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


@app.get("/status", dependencies=authed)
async def status():
    return {"player": (await player.status()).to_dict(), "stream": audio.status()}


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
async def stream():
    return StreamingResponse(
        audio.listen(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache, no-store", "X-Content-Type-Options": "nosniff"},
    )


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if len(TOKEN) < 16:
        raise SystemExit("Set HOMESTREAM_TOKEN (16+ chars) in .env — setup.sh generates one.")
    class Server(uvicorn.Server):
        # Audio streams never end on their own; close them when asked to quit
        # instead of letting uvicorn wait on them.
        def handle_exit(self, sig, frame):
            asyncio.get_event_loop().call_soon_threadsafe(audio.end_streams)
            super().handle_exit(sig, frame)

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="info", timeout_graceful_shutdown=3)
    Server(config).run()


if __name__ == "__main__":
    main()
