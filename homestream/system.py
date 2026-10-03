"""Per-OS chores around the server: staying awake, routing audio on macOS,
and finding the addresses a phone can use."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from contextlib import contextmanager


@contextmanager
def keep_awake():
    """Stop the computer from idling to sleep while HomeStream runs."""
    proc = None
    if sys.platform == "darwin":
        proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    elif sys.platform.startswith("linux") and shutil.which("systemd-inhibit"):
        proc = subprocess.Popen(
            ["systemd-inhibit", "--what=idle:sleep", "--who=HomeStream", "--why=Streaming audio to a phone",
             "--mode=block", "sleep", "infinity"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    elif sys.platform == "win32":
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


@contextmanager
def route_mac_output(device: str):
    """macOS: point the system output at BlackHole while relaying, and put it back afterwards.

    Windows and Linux record what the speakers play, so they need no rerouting.
    """
    switch = shutil.which("SwitchAudioSource")
    previous = None
    if sys.platform == "darwin" and switch and os.environ.get("HOMESTREAM_AUTO_ROUTE", "1") == "1":
        current = subprocess.run([switch, "-t", "output", "-c"], capture_output=True, text=True).stdout.strip()
        if current and current != device:
            subprocess.run([switch, "-t", "output", "-s", device], capture_output=True)
            previous = current
            print(f"Sound output: {current} -> {device} (switched back when HomeStream stops)")
    try:
        yield
    finally:
        if previous:
            subprocess.run([switch, "-t", "output", "-s", previous], capture_output=True)
            print(f"Sound output restored to: {previous}")


def tailscale_cli() -> str | None:
    candidates = [shutil.which("tailscale")]
    if sys.platform == "darwin":
        candidates.append("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    elif sys.platform == "win32":
        candidates.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe"))
    return next((c for c in candidates if c and os.path.exists(c)), None)


def tailscale_status() -> dict:
    """This computer's Tailscale IPv4 address and name, or {} if Tailscale isn't up."""
    cli = tailscale_cli()
    if not cli:
        return {}
    try:
        out = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, timeout=5).stdout
        data = json.loads(out)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    me = data.get("Self") or {}
    ipv4 = next((ip for ip in me.get("TailscaleIPs") or [] if "." in ip), None)
    if not ipv4 or data.get("BackendState") != "Running":
        return {}
    return {"ip": ipv4, "name": (me.get("DNSName") or "").split(".")[0] or None}


def lan_ip() -> str | None:
    """The address other devices on the local network would use (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def print_qr(text: str) -> None:
    try:
        import qrcode
    except ImportError:
        return
    qr = qrcode.QRCode(border=2)
    qr.add_data(text)
    try:
        qr.print_tty() if sys.stdout.isatty() else qr.print_ascii(invert=True)
    except (OSError, UnicodeEncodeError):
        pass


def phone_address(port: int) -> dict:
    """How a phone should reach this computer: over Tailscale if it's up, else the local network."""
    trust_tailscale = os.environ.get("HOMESTREAM_TRUST_TAILSCALE", "1") != "0"
    ts = tailscale_status()
    if ts and trust_tailscale:
        return {"via": "tailscale", "url": f"http://{ts['ip']}:{port}", "typed": f"{ts['ip']}:{port}", "name": ts.get("name")}
    token = os.environ.get("HOMESTREAM_TOKEN", "")
    ip = lan_ip() or "localhost"
    return {"via": "lan", "url": f"http://{ip}:{port}/#token={token}", "typed": f"{ip}:{port}", "name": None}
