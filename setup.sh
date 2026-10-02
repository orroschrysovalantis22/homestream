#!/usr/bin/env bash
# One-time HomeStream setup for macOS. Safe to re-run.
#
#   ./setup.sh             HTTP stream (works in any phone browser)
#   ./setup.sh --snapcast  also install Snapcast, for the Snapdroid app / multi-room
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

[[ "$(uname -s)" == Darwin ]] || die "HomeStream's capture and control only work on macOS"
command -v brew >/dev/null || die "Homebrew is required. Install it from https://brew.sh, then re-run ./setup.sh"

# --- Command-line tools -------------------------------------------------------
step "Command-line tools"
FORMULAE=(ffmpeg media-control switchaudio-osx qrencode)
[[ $WITH_SNAPCAST == 1 ]] && FORMULAE+=(snapcast)
for f in "${FORMULAE[@]}"; do
  if brew list --formula "$f" >/dev/null 2>&1; then
    ok "$f"
  else
    brew install "$f"
  fi
done

# --- BlackHole ------------------------------------------------------------------
step "BlackHole (virtual audio device that carries Mac audio to the relay)"
if [[ -d /Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver ]]; then
  ok "BlackHole 2ch installed"
else
  echo "    It's an audio driver, so macOS will ask for your password."
  if ask "Install BlackHole 2ch with Homebrew now?"; then
    brew install --cask blackhole-2ch
  else
    echo "    Skipped. Install it later with: brew install --cask blackhole-2ch"
  fi
fi
# ffmpeg exits non-zero after listing devices; don't let pipefail turn that into "missing".
DEVICES="$(ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 || true)"
if grep -qF "] BlackHole 2ch" <<<"$DEVICES"; then
  ok "BlackHole 2ch visible to ffmpeg"
else
  echo "    ! BlackHole isn't visible yet. If you just installed it, restart the Mac."
fi

# --- Python ---------------------------------------------------------------------
step "Python environment"
PY=python3
if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  brew install python
  PY="$(brew --prefix)/bin/python3"
fi
[[ -x control-server/.venv/bin/python ]] || "$PY" -m venv control-server/.venv
control-server/.venv/bin/pip install -q --upgrade pip
control-server/.venv/bin/pip install -q -r control-server/requirements.txt
ok "control-server/.venv ($(control-server/.venv/bin/python --version))"

# --- Config -----------------------------------------------------------------------
step "Configuration"
if [[ -f .env ]]; then
  ok ".env exists (kept as is)"
else
  cp config/env.example .env
  sed -i '' "s/^HOMESTREAM_TOKEN=.*/HOMESTREAM_TOKEN=$(openssl rand -hex 24)/" .env
  [[ $WITH_SNAPCAST == 1 ]] && sed -i '' 's/^HOMESTREAM_SNAPCAST=.*/HOMESTREAM_SNAPCAST=1/' .env
  chmod 600 .env
  ok "created .env with a fresh random token"
fi

# --- Tailscale ----------------------------------------------------------------------
step "Tailscale (private network between the Mac and your phone)"
find_tailscale() {
  if command -v tailscale >/dev/null; then command -v tailscale
  elif [[ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then echo /Applications/Tailscale.app/Contents/MacOS/Tailscale
  fi
  return 0
}
TS="$(find_tailscale)"
if [[ -z "$TS" ]] && ask "Tailscale isn't installed. Install it with Homebrew now?"; then
  brew install --cask tailscale-app
  TS="$(find_tailscale)"
fi
TS_IP=""
if [[ -z "$TS" ]]; then
  echo "    Not installed. Get it from https://tailscale.com/download (or: brew install --cask tailscale-app)"
else
  TS_IP="$("$TS" ip -4 2>/dev/null | head -1 || true)"
  if [[ -n "$TS_IP" ]]; then
    ok "connected, this Mac is $TS_IP"
  else
    open -a Tailscale 2>/dev/null || true
    echo "    Sign in from the Tailscale menu bar icon, then re-run ./setup.sh to check."
  fi
fi

# --- Next steps -------------------------------------------------------------------------
step "Next steps"
cat <<EOF
  1. Install Tailscale on your phone and sign in with the same account.
  2. Start the relay:   ./scripts/start-relay.sh
     The first time, macOS asks to let your terminal use the microphone.
     Allow it: that's how ffmpeg reads the BlackHole device.
  3. Scan the QR code it prints with your phone, then tap Listen.
  4. To control a browser player (Spotify Web, YouTube), add your terminal app under
     System Settings → Privacy & Security → Accessibility (it sends the media keys).
     The Spotify desktop app needs no extra setup. macOS asks once to allow control.
EOF
