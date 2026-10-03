"""The `homestream` command.

    homestream            same as `homestream start`
    homestream start      check everything, then stream (Ctrl+C to stop)
    homestream serve      just the server, no checks or extras (for tests and services)
    homestream doctor     report on audio devices, playback control and Tailscale
    homestream tray       run from the menu bar / system tray, without a terminal
    homestream config     show (and create, if missing) the settings file
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

from . import __version__
from .config import SOURCE_ROOT, default_audio_device, ensure_env_file, env_file, load_env_file


def settings() -> tuple[str, str, int]:
    device = os.environ.get("HOMESTREAM_AUDIO_DEVICE") or default_audio_device()
    player = os.environ.get("HOMESTREAM_PLAYER", "auto")
    port = int(os.environ.get("HOMESTREAM_PORT", "8765"))
    return device, player, port


def print_connect_info(port: int) -> None:
    from .system import phone_address, print_qr

    address = phone_address(port)
    computer = "this Mac" if sys.platform == "darwin" else "this computer"
    print()
    print("HomeStream is starting.")
    print()
    if address["via"] == "tailscale":
        print("  On your phone, with Tailscale switched on, scan the code below or type:")
        print(f"      {address['typed']}")
        if address["name"]:
            print(f"      (by name: http://{address['name']}:{port}, including the http://)")
        print("  then Share -> Add to Home Screen, and it's an app from then on.")
    else:
        print("  Tailscale isn't connected, so this only works on your local network:")
        print(f"      {address['url']}")
    print()
    print_qr(address["url"])
    token = os.environ.get("HOMESTREAM_TOKEN")
    if token:
        print(f"  On {computer}: http://localhost:{port}/#token={token}")
    print()
    print(f"Play something on {computer}, then tap Listen on the phone. Ctrl+C to stop.")
    print()


def start_snapcast(device: str) -> subprocess.Popen | None:
    """Optional Snapcast server (HOMESTREAM_SNAPCAST=1), for Snapdroid / multi-room setups."""
    if os.environ.get("HOMESTREAM_SNAPCAST", "0") != "1":
        return None
    snapserver = shutil.which("snapserver")
    if not snapserver or sys.platform == "win32":
        print("  ! Snapcast is on, but snapserver isn't installed (macOS: ./setup.sh --snapcast)")
        return None
    state = Path(os.environ.get("HOMESTREAM_STATE_DIR", SOURCE_ROOT / ".homestream"))
    (state / "snapserver").mkdir(parents=True, exist_ok=True)
    # snapserver runs a program for its audio; a small wrapper avoids its trouble with
    # arguments and spaces in paths.
    wrapper = state / "capture-pcm.sh"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m homestream.capture "{device}"\n')
    wrapper.chmod(0o755)
    snapweb = next((p for p in ("/opt/homebrew/share/snapserver/snapweb", "/usr/local/share/snapserver/snapweb",
                                "/usr/share/snapserver/snapweb") if os.path.isdir(p)), "")
    template = (SOURCE_ROOT / "config" / "snapserver.conf.template").read_text()
    conf = state / "snapserver.conf"
    conf.write_text(template.replace("__STATE_DIR__", str(state)).replace("__SNAPWEB_DIR__", snapweb)
                    .replace("__CAPTURE_SCRIPT__", str(wrapper)))
    env = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT)}
    log = open(state / "snapserver.log", "w")
    proc = subprocess.Popen([snapserver, "-c", str(conf)], stdout=log, stderr=subprocess.STDOUT, env=env)
    print("  Snapcast server running (stream port 1704, web 1780).")
    return proc


def cmd_start(args) -> int:
    from . import preflight
    from .system import keep_awake, route_mac_output

    path = ensure_env_file()
    load_env_file(path)
    device, player, port = settings()
    from .server import check_settings

    check_settings()
    with ExitStack() as stack:
        stack.enter_context(route_mac_output(device))
        if not args.skip_checks and os.environ.get("HOMESTREAM_SKIP_PREFLIGHT", "0") != "1":
            if not preflight.run(device, player):
                print("\nFix the audio capture problem above and try again.")
                return 1
        stack.enter_context(keep_awake())
        snapcast = start_snapcast(device)
        if snapcast:
            stack.callback(snapcast.terminate)
        print_connect_info(port)
        from .server import serve

        try:
            serve()
        except KeyboardInterrupt:
            pass
    return 0


def cmd_serve(args) -> int:
    load_env_file()
    from .server import serve

    serve()
    return 0


def cmd_doctor(args) -> int:
    from .capture import list_devices
    from .system import tailscale_cli, tailscale_status

    path = load_env_file()
    device, player, port = settings()
    print(f"HomeStream {__version__} on {sys.platform}, Python {sys.version.split()[0]}")
    print(f"Settings file: {path}{'' if path.exists() else ' (not created yet)'}")
    print(f"Audio device: {device}   Player: {player}   Port: {port}")
    try:
        devices = list_devices()
        print("Capturable audio:", ", ".join(devices) or "none")
    except Exception as e:
        print("Capturable audio: couldn't list devices:", e)
    ts = tailscale_status()
    if ts:
        print(f"Tailscale: up, this computer is {ts['ip']}" + (f" ({ts['name']})" if ts.get("name") else ""))
    else:
        print("Tailscale:", "installed but not connected" if tailscale_cli() else "not installed")
    from . import preflight

    return 0 if preflight.run(device, player) else 1


def cmd_config(args) -> int:
    path = ensure_env_file()
    print(path)
    return 0


def cmd_tray(args) -> int:
    from .tray import run

    return run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="homestream", description="Listen to this computer's audio on your phone.")
    parser.add_argument("--version", action="version", version=f"homestream {__version__}")
    sub = parser.add_subparsers(dest="command")
    start = sub.add_parser("start", help="check everything, then stream (the default)")
    start.add_argument("--skip-checks", action="store_true", help="skip the startup checks")
    sub.add_parser("serve", help="just the server, no checks or extras")
    sub.add_parser("doctor", help="report on audio devices, playback control and Tailscale")
    sub.add_parser("tray", help="run from the menu bar / system tray")
    sub.add_parser("config", help="show (and create) the settings file")
    args = parser.parse_args(argv)
    handlers = {"start": cmd_start, "serve": cmd_serve, "doctor": cmd_doctor, "tray": cmd_tray, "config": cmd_config}
    if args.command is None:
        args = parser.parse_args(["start", *(argv or sys.argv[1:])])
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
