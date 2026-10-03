"""Where settings live, and loading them.

Settings are environment variables (HOMESTREAM_*), optionally kept in a .env
file. Which file: $HOMESTREAM_ENV_FILE if set; otherwise .env in a source
checkout (next to the homestream/ folder); otherwise a per-user file, e.g.
~/Library/Application Support/HomeStream/homestream.env on a Mac,
%APPDATA%\\HomeStream\\homestream.env on Windows, ~/.config/homestream/homestream.env
on Linux. Real environment variables always win over the file.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_DIR.parent


def user_config_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "HomeStream"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "HomeStream"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "homestream"


def env_file() -> Path:
    if os.environ.get("HOMESTREAM_ENV_FILE"):
        return Path(os.environ["HOMESTREAM_ENV_FILE"])
    source_env = SOURCE_ROOT / ".env"
    if source_env.exists() or (SOURCE_ROOT / "pyproject.toml").exists():
        return source_env
    return user_config_dir() / "homestream.env"


def load_env_file(path: Path | None = None) -> Path:
    """Load KEY=value lines into os.environ (without overriding); return the path used."""
    path = path or env_file()
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    return path


def ensure_env_file(path: Path | None = None) -> Path:
    """Create the settings file with a fresh random token if there isn't one yet."""
    path = path or env_file()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    template = (SOURCE_ROOT / "config" / "env.example")
    text = template.read_text(encoding="utf-8") if template.exists() else "HOMESTREAM_TOKEN=\n"
    lines = [
        f"HOMESTREAM_TOKEN={secrets.token_hex(24)}" if line.startswith("HOMESTREAM_TOKEN=") else line
        for line in text.splitlines()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def default_audio_device() -> str:
    """macOS needs a virtual device (BlackHole); Windows and Linux can record what's playing directly."""
    return "BlackHole 2ch" if sys.platform == "darwin" else "default"


def setting(name: str, default: str = "") -> str:
    return os.environ.get(f"HOMESTREAM_{name}", default)
