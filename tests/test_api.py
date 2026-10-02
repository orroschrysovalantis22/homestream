"""End-to-end tests against a real server process (test tone + dry-run player)."""

import http.client
import json
import signal
import subprocess
import sys

import pytest

from conftest import ROOT, SERVER_DIR, TOKEN, start_server, stop_server, wait_for


def test_page_is_public_and_uncached(server):
    r = server.request("GET", "/")
    assert r.status == 200
    assert b"<title>HomeStream</title>" in r.body
    assert r.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/static/manifest.webmanifest", "/static/apple-touch-icon.png", "/static/icon.svg"])
def test_static_assets_are_public(server, path):
    assert server.request("GET", path).status == 200


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/status"), ("GET", "/stream.mp3"), ("POST", "/play"), ("POST", "/pause"),
     ("POST", "/toggle"), ("POST", "/next"), ("POST", "/prev")],
)
def test_routes_need_the_token(server, method, path):
    assert server.request(method, path).status == 401
    assert server.request(method, path, token="wrong-token").status == 401
    assert server.request(method, path, cookie="homestream_token=wrong-token").status == 401


def test_status_with_bearer_token(server):
    r = server.request("GET", "/status", token=TOKEN)
    assert r.status == 200
    body = r.json()
    assert body["player"]["backend"] == "dryrun"
    assert body["stream"]["device"] == "test-tone"


def test_cookie_login_and_logout(server):
    assert server.request("POST", "/auth", body={"token": "wrong"}).status == 401

    r = server.request("POST", "/auth", body={"token": TOKEN})
    assert r.status == 204
    set_cookie = r.headers["set-cookie"]
    assert set_cookie.startswith(f"homestream_token={TOKEN};")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Secure" not in set_cookie  # plain http

    assert server.request("GET", "/status", cookie=f"homestream_token={TOKEN}").status == 200

    r = server.request("POST", "/logout")
    assert r.status == 204
    assert 'homestream_token=""' in r.headers["set-cookie"]
    assert "Max-Age=0" in r.headers["set-cookie"]


def test_secure_cookie_behind_https_proxy(server):
    # e.g. `tailscale serve` terminating HTTPS in front of the server
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("POST", "/auth", body=json.dumps({"token": TOKEN}),
                 headers={"Content-Type": "application/json", "X-Forwarded-Proto": "https"})
    resp = conn.getresponse()
    assert resp.status == 204
    assert "Secure" in resp.headers["set-cookie"]


def test_play_pause_toggle(server):
    def state():
        return server.request("GET", "/status", token=TOKEN).json()["player"]["state"]

    assert server.request("POST", "/pause", token=TOKEN).json() == {"ok": True, "command": "pause"}
    assert state() == "paused"
    server.request("POST", "/play", token=TOKEN)
    assert state() == "playing"
    server.request("POST", "/toggle", token=TOKEN)
    assert state() == "paused"


def test_query_params_cant_change_the_command(server):
    r = server.request("POST", "/play?cmd=next", token=TOKEN)
    assert r.json() == {"ok": True, "command": "play"}


@pytest.mark.parametrize("cmd", ["next", "prev"])
def test_track_commands(server, cmd):
    assert server.request("POST", f"/{cmd}", token=TOKEN).status == 200


def test_commands_are_post_only(server):
    assert server.request("GET", "/next", token=TOKEN).status == 405


def test_stream_is_live_mp3(server):
    resp = server.open_stream()
    assert resp.status == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    data = resp.read(48_000)  # ~2 s at 192 kbps
    # MP3 frame sync: 11 set bits, near the start.
    assert any(data[i] == 0xFF and data[i + 1] & 0xE0 == 0xE0 for i in range(2000))
    assert server.listeners() == 1
    resp.close()
    assert wait_for(lambda: server.listeners() == 0)


def test_two_listeners_share_one_capture(server):
    a, b = server.open_stream(), server.open_stream()
    a.read(8000)
    b.read(8000)
    assert server.listeners() == 2
    capture = subprocess.run(["pgrep", "-f", "lavfi -i sine"], capture_output=True, text=True).stdout.split()
    assert len(capture) == 1
    a.close()
    b.close()


def test_refuses_to_start_without_a_real_token():
    result = subprocess.run(
        [sys.executable, str(SERVER_DIR / "main.py")],
        env={"PATH": "/usr/bin:/bin", "HOMESTREAM_ENV_FILE": str(ROOT / "tests" / "nope.env"), "HOMESTREAM_TOKEN": "short"},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode != 0
    assert "HOMESTREAM_TOKEN" in result.stderr


def test_ctrl_c_with_a_listener_exits_promptly():
    srv = start_server()
    resp = srv.open_stream()
    resp.read(8000)
    took = stop_server(srv)
    # uvicorn shuts down cleanly, then re-raises SIGINT like any Ctrl+C'd program.
    assert srv.proc.returncode in (0, -signal.SIGINT)
    assert took < 2, f"shutdown took {took:.1f}s"
