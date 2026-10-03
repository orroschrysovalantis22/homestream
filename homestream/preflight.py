"""Startup checks, run by `homestream start` before the server starts.

On macOS the point is to trigger the one-time permission prompts while you're
at the computer. Otherwise the Microphone prompt would first appear when the
phone tapped Listen, with nobody there to click Allow. On every OS it catches
a missing audio device or control tool before your phone does.

Returns False if audio capture can't work; everything else is a warning.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading

from .capture import TEST_DEVICES, CaptureError, open_source

CAPTURE_TIMEOUT = 90  # long enough to read and answer a permission dialog
SILENCE_PEAK = 30  # of 32767

MAC_SETTINGS = "x-apple.systempreferences:com.apple.preference.security?Privacy_"


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def warn(msg: str) -> None:
    print(f"  ! {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


def record(device: str, seconds: float = 1.0) -> bytes:
    """Capture a little audio; raises CaptureError or TimeoutError."""
    result: dict = {}

    def run():
        try:
            source = open_source(device)
            need = int(seconds * source.rate) * 4
            data = bytearray()
            for block in source.blocks():
                data += block
                if len(data) >= need:
                    break
            source.stop()
            result["data"] = bytes(data)
        except Exception as e:  # reported to the caller below
            result["error"] = e

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(CAPTURE_TIMEOUT)
    if thread.is_alive():
        raise TimeoutError("audio capture timed out. Is a permission dialog still open?")
    if "error" in result:
        raise result["error"]
    return result["data"]


def peak(pcm: bytes) -> int:
    from array import array

    samples = array("h", pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return max((abs(s) for s in samples), default=0)


def check_capture(device: str) -> bool:
    if device in TEST_DEVICES:
        ok(f"audio source: {device}")
        return True
    if sys.platform == "darwin":
        print(f"  … test-recording 1 s from {device!r}. If macOS asks to let your terminal")
        print("    use the microphone, click Allow: that's how HomeStream hears BlackHole.")
    try:
        pcm = record(device)
    except (CaptureError, TimeoutError, OSError) as e:
        fail(str(e))
        if "not found" in str(e):
            if sys.platform == "darwin":
                print("    Check HOMESTREAM_AUDIO_DEVICE, or restart the Mac after installing BlackHole.")
            elif sys.platform.startswith("linux"):
                print("    Is PulseAudio or PipeWire running? `homestream doctor` lists what can be captured.")
            return False
        if sys.platform == "darwin":
            print("    Allow your terminal app under System Settings → Privacy & Security → Microphone,")
            print("    then quit and reopen the terminal and try again.")
            subprocess.run(["open", MAC_SETTINGS + "Microphone"], check=False)
        return False
    where = device if device != "default" else "what this computer plays"
    if peak(pcm) < SILENCE_PEAK:
        ok(f"audio capture works ({where}), silent right now. Fine if nothing is playing.")
    else:
        ok(f"audio capture works ({where}) and hears sound")
    return True


def check_controls(player: str) -> None:
    if player == "dryrun":
        return
    if sys.platform == "darwin":
        _check_mac_controls(player)
    elif sys.platform.startswith("linux"):
        if shutil.which("playerctl"):
            ok("controls via MPRIS (playerctl): works with Spotify, browsers, VLC and more")
        else:
            warn("playerctl isn't installed, so the phone can't control playback (e.g. sudo apt install playerctl)")
    elif sys.platform == "win32":
        try:
            import winrt.windows.media.control  # noqa: F401
            ok("controls via Windows media controls: works with Spotify, browsers and more")
        except ImportError:
            warn("the winrt packages are missing, so the phone can't control playback (reinstall HomeStream)")


def _check_mac_controls(player: str) -> None:
    from .players.macos import accessibility_trusted, media_control_path

    if player in ("auto", "nowplaying") and media_control_path():
        ok("controls via macOS Now Playing (media-control): works with any player")
        return
    if player in ("auto", "browser"):
        if accessibility_trusted():
            ok("Accessibility allowed (media keys for browser players)")
        else:
            accessibility_trusted(prompt=True)  # opens macOS's own "allow Accessibility" dialog
            warn("Accessibility not allowed yet, so browser players can't be controlled.")
            print("    Turn your terminal app on under System Settings → Privacy & Security → Accessibility,")
            print("    then restart. (Or: brew install media-control, which needs no permission.)")
    if player == "spotify" or player == "auto":
        if subprocess.run(["pgrep", "-xq", "Spotify"]).returncode != 0:
            if player == "spotify":
                warn("Spotify desktop app isn't running. Open it before using the controls.")
            return
        # The first AppleScript call triggers the one-time "control Spotify?" prompt.
        try:
            result = subprocess.run(["osascript", "-e", 'tell application "Spotify" to player state'],
                                    capture_output=True, timeout=CAPTURE_TIMEOUT)
        except subprocess.TimeoutExpired:
            warn("Spotify check timed out. Is a permission dialog still open?")
            return
        if result.returncode == 0:
            ok("Spotify desktop app can be controlled")
        else:
            warn("not allowed to control Spotify: " + result.stderr.decode(errors="replace").strip())


def run(device: str, player: str) -> bool:
    print("Checking:")
    if not check_capture(device):
        return False
    check_controls(player)
    return True
