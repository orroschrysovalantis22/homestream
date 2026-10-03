"""Start HomeStream's tray app when you log in, on each OS.

  macOS    ~/Library/LaunchAgents/com.homestream.tray.plist (a login agent)
  Windows  HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run, value "HomeStream"
  Linux    ~/.config/autostart/homestream.desktop (the freedesktop autostart standard)
"""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

LABEL = "com.homestream.tray"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command() -> list[str]:
    """How to launch the tray app with this same Python, without a console window on Windows."""
    if getattr(sys, "frozen", False):  # a packaged app: the executable is the app
        return [sys.executable, "tray"]
    python = sys.executable
    if sys.platform == "win32":
        pythonw = Path(python).with_name("pythonw.exe")
        python = str(pythonw) if pythonw.exists() else python
    return [python, "-m", "homestream", "tray"]


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _linux_desktop() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "homestream.desktop"


def is_enabled() -> bool:
    if sys.platform == "darwin":
        return _mac_plist().exists()
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, "HomeStream")
            return True
        except OSError:
            return False
    return _linux_desktop().exists()


def enable() -> None:
    cmd = command()
    if sys.platform == "darwin":
        path = _mac_plist()
        path.parent.mkdir(parents=True, exist_ok=True)
        from .config import SOURCE_ROOT

        plist = {
            "Label": LABEL,
            "ProgramArguments": cmd,
            "RunAtLoad": True,
            "WorkingDirectory": str(SOURCE_ROOT),
            # Login agents get a bare PATH; Homebrew's tools (media-control, SwitchAudioSource) live here.
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        }
        path.write_bytes(plistlib.dumps(plist))
    elif sys.platform == "win32":
        import subprocess
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, "HomeStream", 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
    else:
        import shlex

        path = _linux_desktop()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[Desktop Entry]\nType=Application\nName=HomeStream\n"
            "Comment=Listen to this computer's audio on your phone\n"
            f"Exec={shlex.join(cmd)}\nX-GNOME-Autostart-enabled=true\n"
        )


def disable() -> None:
    if sys.platform == "darwin":
        _mac_plist().unlink(missing_ok=True)
    elif sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "HomeStream")
        except OSError:
            pass
    else:
        _linux_desktop().unlink(missing_ok=True)


def set_enabled(on: bool) -> None:
    enable() if on else disable()
