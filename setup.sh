#!/usr/bin/env bash
# One-time HomeStream setup for macOS and Linux (on Windows, run setup.ps1). Safe to re-run.
#
#   ./setup.sh             what you need to stream to a phone
#   ./setup.sh --snapcast  also Snapcast, for the Snapdroid app / multi-room
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WITH_SNAPCAST=0
for arg in "$@"; do
  case "$arg" in
    --snapcast) WITH_SNAPCAST=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 1 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
step() { echo; bold "==> $*"; }
ok() { echo "    ✓ $*"; }
die() { echo "error: $*" >&2; exit 1; }
ask() { # ask "question" -> 0 for yes (the default); no when not interactive
  [[ -t 0 ]] || return 1
  local answer
  read -r -p "    $1 [Y/n] " answer
  [[ ! "$answer" =~ ^[Nn] ]]
}
as_root() { if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi; }

# --- macOS ------------------------------------------------------------------------
setup_macos() {
  command -v brew >/dev/null || die "Homebrew is required. Install it from https://brew.sh, then re-run ./setup.sh"

  step "Command-line tools"
  local formulae=(media-control)
  [[ $WITH_SNAPCAST == 1 ]] && formulae+=(snapcast)
  local f
  for f in "${formulae[@]}"; do
    if brew list --formula "$f" >/dev/null 2>&1; then ok "$f"; else brew install "$f"; fi
  done

  step "BlackHole (virtual audio device that carries the Mac's audio to HomeStream)"
  if [[ -d /Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver ]]; then
    ok "BlackHole 2ch installed"
  else
    echo "    macOS has no built-in way to record what's playing; BlackHole adds one."
    echo "    It's an audio driver, so macOS will ask for your password."
    if ask "Install BlackHole 2ch with Homebrew now?"; then
      brew install --cask blackhole-2ch
      echo "    If it isn't picked up straight away: sudo killall coreaudiod (or restart the Mac)."
    else
      echo "    Skipped. Install it later with: brew install --cask blackhole-2ch"
    fi
  fi
}

# --- Linux ------------------------------------------------------------------------
setup_linux() {
  step "System packages"
  echo "    playerctl (playback control), PulseAudio/PipeWire client library (capture), Python venv"
  local snap=()
  if command -v apt-get >/dev/null; then
    [[ $WITH_SNAPCAST == 1 ]] && snap=(snapserver)
    as_root apt-get update -qq
    as_root apt-get install -y -qq python3 python3-venv python3-pip playerctl libpulse0 "${snap[@]}"
  elif command -v dnf >/dev/null; then
    [[ $WITH_SNAPCAST == 1 ]] && snap=(snapcast)
    as_root dnf install -y -q python3 playerctl pulseaudio-libs "${snap[@]}"
  elif command -v pacman >/dev/null; then
    [[ $WITH_SNAPCAST == 1 ]] && snap=(snapcast)
    as_root pacman -S --needed --noconfirm python playerctl libpulse "${snap[@]}"
  elif command -v zypper >/dev/null; then
    as_root zypper --non-interactive install python3 playerctl libpulse0
  else
    echo "    ! Unknown package manager: install Python 3.10+, playerctl and libpulse yourself."
  fi
  ok "system packages"
  echo "    No virtual audio device needed: HomeStream records what your speakers play."
}

case "$(uname -s)" in
  Darwin) setup_macos ;;
  Linux) setup_linux ;;
  *) die "this script is for macOS and Linux; on Windows run setup.ps1" ;;
esac

# --- Python -----------------------------------------------------------------------
step "Python environment"
PY="$(command -v python3 || true)"
if [[ -z "$PY" ]] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  if [[ "$(uname -s)" == Darwin ]]; then brew install python && PY="$(brew --prefix)/bin/python3"
  else die "Python 3.10 or newer is required"; fi
fi
[[ -x .venv/bin/python ]] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e ".[tray]"
ok ".venv ($(.venv/bin/python --version)), the homestream command is .venv/bin/homestream"

if [[ "$(uname -s)" == Darwin ]]; then
  if .venv/bin/python -m homestream.capture --list 2>/dev/null | grep -qx "BlackHole 2ch"; then
    ok "BlackHole 2ch is visible"
  else
    echo "    ! BlackHole isn't visible yet. If you just installed it: sudo killall coreaudiod, or restart."
  fi
fi

# --- Settings -----------------------------------------------------------------------
step "Settings"
SETTINGS="$(.venv/bin/python -m homestream config)"
ok "$SETTINGS (includes a random token for devices that aren't on Tailscale)"

# --- Tailscale ----------------------------------------------------------------------
step "Tailscale (private network between this computer and your phone)"
TS="$(command -v tailscale || true)"
[[ -z "$TS" && -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]] && TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
if [[ -z "$TS" ]] && ask "Tailscale isn't installed. Install it now?"; then
  if [[ "$(uname -s)" == Darwin ]]; then
    brew install --cask tailscale-app
    TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
  else
    curl -fsSL https://tailscale.com/install.sh | sh
    TS="$(command -v tailscale || true)"
  fi
fi
if [[ -z "$TS" ]]; then
  echo "    Not installed. Get it from https://tailscale.com/download"
elif TS_IP="$("$TS" ip -4 2>/dev/null | head -1)" && [[ -n "$TS_IP" ]]; then
  ok "connected, this computer is $TS_IP"
elif [[ "$(uname -s)" == Darwin ]]; then
  open -a Tailscale 2>/dev/null || true
  echo "    Sign in from the Tailscale menu bar icon, then re-run ./setup.sh to check."
else
  echo "    Sign in with: sudo tailscale up"
fi

# --- Next steps -------------------------------------------------------------------------
step "Next steps"
cat <<EOF
  1. Install Tailscale on your phone and sign in with the same account.
  2. Start HomeStream:   ./scripts/start-relay.sh     (or, without a terminal: .venv/bin/homestream tray)
EOF
if [[ "$(uname -s)" == Darwin ]]; then
  cat <<EOF
     The first time, macOS asks to let your terminal use the microphone.
     Allow it: that's how HomeStream hears BlackHole.
EOF
fi
cat <<EOF
  3. On your phone, scan the QR code it shows (or type the address), then
     Share -> Add to Home Screen. Play something here and tap Listen.
EOF
