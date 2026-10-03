"""Shared fixtures: a real control server running the test tone and dry-run player."""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SERVER_CMD = [sys.executable, "-m", "homestream", "serve"]
# Tests that import the server module must not pick up the developer's real .env.
os.environ["HOMESTREAM_ENV_FILE"] = str(ROOT / "tests" / "does-not-exist.env")

TOKEN = "test-token-0123456789abcdef"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Response:
    status: int
    headers: http.client.HTTPMessage
    body: bytes

    def json(self):
        return json.loads(self.body)


@dataclass
class Server:
    port: int
    proc: subprocess.Popen

    def request(self, method: str, path: str, token: str | None = None, cookie: str | None = None, body=None) -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if cookie:
            headers["Cookie"] = cookie
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        result = Response(resp.status, resp.headers, resp.read())
        conn.close()
        return result

    def open_stream(self, token: str = TOKEN) -> http.client.HTTPResponse:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/stream.mp3", headers={"Authorization": f"Bearer {token}"})
        return conn.getresponse()

    def listeners(self) -> int:
        return self.request("GET", "/status", token=TOKEN).json()["stream"]["listeners"]


def start_server(**env_overrides: str) -> Server:
    port = free_port()
    env = {
        **os.environ,
        "HOMESTREAM_ENV_FILE": str(ROOT / "tests" / "does-not-exist.env"),
        "HOMESTREAM_TOKEN": TOKEN,
        "HOMESTREAM_AUDIO_DEVICE": "test-tone",
        "HOMESTREAM_PLAYER": "dryrun",
        "HOMESTREAM_HOST": "127.0.0.1",
        "HOMESTREAM_PORT": str(port),
        **env_overrides,
    }
    # A file, not a pipe: an unread pipe would eventually block the server's logging.
    log = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        SERVER_CMD, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT
    )
    server = Server(port, proc)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.seek(0)
            raise RuntimeError("server exited:\n" + log.read().decode())
        try:
            if server.request("GET", "/").status == 200:
                return server
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("server didn't start within 15s")


def stop_server(server: Server, timeout: float = 10) -> float:
    """SIGINT (like Ctrl+C) and return how long shutdown took."""
    started = time.monotonic()
    if os.name == "nt":
        server.proc.terminate()  # Windows has no SIGINT for child processes
    else:
        server.proc.send_signal(signal.SIGINT)
    try:
        server.proc.wait(timeout)
    except subprocess.TimeoutExpired:
        server.proc.kill()
        server.proc.wait()
    return time.monotonic() - started


@pytest.fixture(scope="session")
def server():
    srv = start_server()
    yield srv
    stop_server(srv)


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False
