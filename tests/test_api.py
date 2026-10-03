"""End-to-end tests against a real server process (test tone + dry-run player)."""

import http.client
import json
import os
import signal
import subprocess
import sys

import pytest

from conftest import ROOT, SERVER_CMD, TOKEN, start_server, stop_server, wait_for


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
    before = server.request("GET", "/status", token=TOKEN).json()["stream"]["capture_starts"]
    a, b = server.open_stream(), server.open_stream()
    a.read(8000)
    b.read(8000)
    stream = server.request("GET", "/status", token=TOKEN).json()["stream"]
    assert stream["listeners"] == 2
    assert stream["capture_starts"] - before <= 1  # one capture feeds both (or an already running one)
    a.close()
    b.close()


def test_refuses_to_start_without_a_real_token():
    result = subprocess.run(
        SERVER_CMD, cwd=ROOT,
        env={**os.environ, "HOMESTREAM_TOKEN": "short"},  # all of it: Windows can't start networking without SYSTEMROOT
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode != 0
    assert "HOMESTREAM_TOKEN" in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="Ctrl+C is a POSIX signal here")
def test_ctrl_c_with_a_listener_exits_promptly():
    srv = start_server()
    resp = srv.open_stream()
    resp.read(8000)
    took = stop_server(srv)
    # uvicorn shuts down cleanly, then re-raises SIGINT like any Ctrl+C'd program.
    assert srv.proc.returncode in (0, -signal.SIGINT)
    assert took < 2, f"shutdown took {took:.1f}s"


# --- who gets in -------------------------------------------------------------------

def make_request(client: str, server: str):
    from starlette.requests import Request

    return Request({"type": "http", "headers": [], "client": (client, 50000), "server": (server, 8765)})


@pytest.mark.parametrize(
    "client,server,trusted",
    [
        ("100.101.102.103", "100.64.0.10", True),        # phone -> Mac, both on the tailnet
        ("fd7a:115c:a1e0::2", "fd7a:115c:a1e0::1", True),
        ("100.101.102.103", "192.168.1.20", False),       # tailnet-looking client on the local network
        ("192.168.1.30", "100.64.0.10", False),
        ("192.168.1.30", "192.168.1.20", False),          # someone on the same Wi-Fi
        ("127.0.0.1", "127.0.0.1", False),                # local processes / proxies need the token
        ("100.128.0.1", "100.64.0.10", False),           # just outside 100.64.0.0/10
    ],
)
def test_only_direct_tailscale_connections_skip_the_token(client, server, trusted):
    from homestream import server as main

    assert main.via_tailscale(make_request(client, server)) is trusted


def test_status_says_how_we_got_in(server):
    assert server.request("GET", "/status", token=TOKEN).json()["access"] == "token"


def test_runs_without_a_token_but_then_only_tailscale_gets_in():
    srv = start_server(HOMESTREAM_TOKEN="")
    try:
        assert srv.request("GET", "/").status == 200
        assert srv.request("GET", "/status").status == 401  # localhost isn't on the tailnet
        assert srv.request("POST", "/auth", body={"token": ""}).status in (401, 422)
    finally:
        stop_server(srv)


def test_refuses_to_start_when_nobody_could_connect():
    result = subprocess.run(
        SERVER_CMD, cwd=ROOT,
        env={**os.environ, "HOMESTREAM_TOKEN": "", "HOMESTREAM_TRUST_TAILSCALE": "0"},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode != 0
    assert "Nobody could connect" in result.stderr


# --- the "Connect a phone" page -------------------------------------------------------

def test_pair_page_is_only_for_this_computer(server):
    r = server.request("GET", "/pair")
    assert r.status == 200 and b"<svg" in r.body and b"Connect your phone" in r.body
    assert b"{{" not in r.body  # every placeholder filled in
    assert "Share →".encode() in r.body  # read as UTF-8, not Windows' default code page


@pytest.mark.parametrize("client,server_addr,headers,allowed", [
    ("127.0.0.1", "127.0.0.1", [], True),
    ("::1", "::1", [], True),
    ("127.0.0.1", "127.0.0.1", [(b"x-forwarded-for", b"203.0.113.9")], False),  # via a local proxy
    ("100.101.102.103", "100.64.0.10", [], False),  # a phone on the tailnet
    ("192.168.1.30", "192.168.1.20", [], False),
])
def test_pair_page_access_rule(client, server_addr, headers, allowed):
    from starlette.requests import Request

    from homestream import server as main

    request = Request({"type": "http", "headers": headers, "client": (client, 5000), "server": (server_addr, 8765)})
    assert main.from_this_computer(request) is allowed
