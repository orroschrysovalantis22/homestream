"""Startup checks, run by start-relay.sh before the server starts.

The point is to trigger macOS's one-time permission prompts while you're at
the Mac. Otherwise the Microphone prompt would first appear when the phone
tapped Listen, with nobody home to click Allow.

Exit status 1 means audio capture can't work; everything else is a warning.
"""

from __future__ import annotations

import array
import os
import re
import subprocess
import sys

from audio_stream import CAPTURE_SCRIPT, TEST_SIGNAL, TEST_TONE
from mac_control import accessibility_trusted, media_control_path

CAPTURE_TIMEOUT = 90  # long enough to read and answer the permission dialog
SILENCE_PEAK = 30  # of 32767

SETTINGS = "x-apple.systempreferences:com.apple.preference.security?Privacy_"


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def warn(msg: str) -> None:
    print(f"  ! {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


def check_capture(device: str) -> bool:
    if device in (TEST_TONE, TEST_SIGNAL):
        ok(f"audio source: {device}")
        return True
    print(f"  … test-recording 1 s from {device!r}. If macOS asks to let your terminal")
    print("    use the microphone, click Allow: that's how the relay hears BlackHole.")
    cmd = [sys.executable, str(CAPTURE_SCRIPT), "--seconds", "1", device]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=CAPTURE_TIMEOUT)
    except subprocess.TimeoutExpired:
        fail("audio capture timed out. Is a permission dialog still open?")
        return False
    if result.returncode != 0 or not result.stdout:
        lines = result.stderr.decode(errors="replace").strip().splitlines()
        error = re.sub(r"^\[[^\]]*\]\s*", "", lines[0]) if lines else "no audio captured"
        fail(f"can't capture {device!r}: {error}")
        if "not found" in error:
            print("    Check HOMESTREAM_AUDIO_DEVICE in .env, or restart the Mac after installing BlackHole.")
            return False
        print("    Allow your terminal app under System Settings → Privacy & Security → Microphone,")
        print("    then quit and reopen the terminal and run start-relay.sh again.")
        subprocess.run(["open", SETTINGS + "Microphone"], check=False)
        return False
    samples = array.array("h", result.stdout[: len(result.stdout) // 2 * 2])
    peak = max((abs(s) for s in samples), default=0)
    if peak < SILENCE_PEAK:
        ok(f"audio capture works ({device}), silent right now. Fine if nothing is playing.")
    else:
        ok(f"audio capture works ({device}) and hears sound")
    return True


def check_accessibility(player: str) -> None:
    if player in ("auto", "nowplaying") and media_control_path():
        ok("controls via macOS Now Playing (media-control): works with any player")
        return
    if player not in ("auto", "browser"):
        return
    if accessibility_trusted():
        ok("Accessibility allowed (media keys for browser players)")
        return
    # Opens macOS's own "allow Accessibility" dialog for this terminal.
    accessibility_trusted(prompt=True)
    warn("Accessibility not allowed yet, so browser players can't be controlled.")
    print("    Turn your terminal app on under System Settings → Privacy & Security → Accessibility,")
    print("    then restart the relay. (Not needed if you only use the Spotify desktop app.)")


def check_spotify(player: str) -> None:
    if player != "spotify" and not (player == "auto" and not media_control_path()):
        return
    running = subprocess.run(["pgrep", "-xq", "Spotify"]).returncode == 0
    if not running:
        if player == "spotify":
            warn("Spotify desktop app isn't running. Open it before using the controls.")
        return
    # The first AppleScript call triggers the one-time "control Spotify?" prompt.
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Spotify" to player state'],
            capture_output=True, timeout=CAPTURE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        warn("Spotify check timed out. Is a permission dialog still open?")
        return
    if result.returncode == 0:
        ok("Spotify desktop app can be controlled")
    else:
        warn("not allowed to control Spotify: " + result.stderr.decode(errors="replace").strip())
        print("    Allow it under System Settings → Privacy & Security → Automation.")


def main() -> int:
    device = os.environ.get("HOMESTREAM_AUDIO_DEVICE", "BlackHole 2ch")
    player = os.environ.get("HOMESTREAM_PLAYER", "auto")
    print("Checking permissions:")
    if not check_capture(device):
        return 1
    check_accessibility(player)
    check_spotify(player)
    return 0


if __name__ == "__main__":
    sys.exit(main())
