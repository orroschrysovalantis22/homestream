"""HomeStream in the menu bar (macOS) or system tray (Windows, Linux): no terminal needed.

    homestream tray

The menu shows what's happening ("Ready", "Streaming to 1 phone", or a problem),
opens the "Connect a phone" page with its QR code, toggles start-at-login, and
quits. The first time it runs, it opens the "Connect a phone" page by itself.
Logs go to homestream.log next to the settings file.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
import webbrowser
from contextlib import ExitStack

log = logging.getLogger("homestream.tray")


def on_ui_thread(fn) -> None:
    """Run fn on the GUI thread. macOS kills an app that touches its menu from any
    other thread, and GTK (Linux) isn't thread-safe either."""
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper

        AppHelper.callAfter(fn)
        return
    if sys.platform.startswith("linux"):
        try:
            from gi.repository import GLib

            GLib.idle_add(lambda: fn() and False)
            return
        except Exception:
            pass
    fn()


def run() -> int:
    try:
        import pystray
        from PIL import Image
    except ImportError:
        print("The tray app needs two more packages: pip install 'homestream[tray]'", file=sys.stderr)
        return 1

    from .config import PACKAGE_DIR, env_file, ensure_env_file, load_env_file

    first_run = not env_file().exists()
    path = ensure_env_file()
    load_env_file(path)

    from . import autostart, preflight
    from . import server as srv
    from .cli import settings
    from .system import keep_awake, phone_address, route_mac_output

    device, player, port = settings()
    srv.check_settings()
    problem: dict = {"text": None}
    running = threading.Event()
    running.set()

    stack = ExitStack()
    stack.enter_context(route_mac_output(device))
    stack.enter_context(keep_awake())
    server = srv.make_server(log_level="warning")
    # After make_server: uvicorn's logging setup closes any handlers that already exist.
    logging.basicConfig(
        filename=path.with_name("homestream.log"), filemode="w", level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s", force=True,
    )
    server_thread = threading.Thread(target=server.run, name="homestream-server", daemon=True)
    server_thread.start()

    def status_text(item=None) -> str:
        if problem["text"]:
            return f"Problem: {problem['text'][:70]}"
        if not getattr(server, "started", False):
            return "Starting…"
        listeners = srv.audio.status()["listeners"]
        if listeners:
            return f"Streaming to {listeners} phone{'s' if listeners != 1 else ''}"
        return "Ready: open HomeStream on your phone"

    def open_pair(*_):
        webbrowser.open(f"http://localhost:{port}/pair")

    def open_player(*_):
        token = os.environ.get("HOMESTREAM_TOKEN")
        url = f"http://localhost:{port}/#token={token}" if token else phone_address(port)["url"]
        webbrowser.open(url)

    def toggle_login(icon, item):
        autostart.set_enabled(not autostart.is_enabled())
        on_ui_thread(icon.update_menu)

    def quit_app(icon, item):
        running.clear()
        server.request_stop()
        icon.stop()

    def check_audio():
        # A short test recording: on macOS this is what makes the Microphone prompt appear now.
        if device in ("test-tone", "test-signal"):
            return
        try:
            preflight.record(device, 0.5)
        except Exception as e:
            message = str(e)  # `e` itself is cleared when this block ends
            problem["text"] = message
            log.warning("audio check failed: %s", message)
            on_ui_thread(lambda: icon.notify(message, "HomeStream can't capture audio"))

    def refresh():
        shown = None
        while running.is_set():
            time.sleep(2)
            # A no-op on the GUI thread lets Python run pending signal handlers (logout,
            # `kill`): the macOS menu loop otherwise never hands control back to Python.
            on_ui_thread(lambda: None)
            text = status_text()
            if text == shown:
                continue  # rebuilding the menu while it's open would close it
            shown = text

            def apply(text=text):
                icon.title = f"HomeStream: {text}"
                icon.update_menu()

            on_ui_thread(apply)

    def setup(icon):
        icon.visible = True
        log.info("tray icon shown; server on port %s", port)
        threading.Thread(target=check_audio, daemon=True).start()
        threading.Thread(target=refresh, daemon=True).start()
        if first_run:
            for _ in range(50):  # wait for the server, then show the QR code once
                if getattr(server, "started", False):
                    break
                time.sleep(0.1)
            open_pair()

    menu = pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Connect a phone…", open_pair, default=True),
        pystray.MenuItem("Open the player here", open_player),
        pystray.MenuItem("Start at login", toggle_login, checked=lambda item: autostart.is_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit HomeStream", quit_app),
    )
    image = Image.open(PACKAGE_DIR / "web" / "static" / "icon-192.png")
    icon = pystray.Icon("HomeStream", image, "HomeStream", menu)

    if sys.platform == "darwin":
        try:  # a menu bar app: no Dock icon
            from AppKit import NSApplication

            NSApplication.sharedApplication().setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory
        except Exception:
            pass

    def stop_on_signal(signum, frame):
        log.info("signal %s: quitting", signum)
        running.clear()
        server.request_stop()
        icon.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop_on_signal)

    try:
        icon.run(setup)
    finally:
        running.clear()
        server.request_stop()
        server_thread.join(timeout=5)
        stack.close()
        log.info("stopped")
    return 0
