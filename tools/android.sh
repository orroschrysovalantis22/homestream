#!/usr/bin/env bash
# Drive an Android emulator for testing the phone page (see README -> Development).
#
#   tools/android.sh create            make the "homestream" emulator (Android 16, Pixel 7, Chrome)
#   tools/android.sh boot              start it and wait until it's ready
#   tools/android.sh open URL          open URL in Chrome (the Mac is 10.0.2.2 from the emulator)
#   tools/android.sh tap X Y           tap the screen
#   tools/android.sh shot FILE.png     screenshot
#   tools/android.sh media             what Android's media controls see (title, state, actions)
#   tools/android.sh key play|pause|next|prev|power   press a hardware/media key
#   tools/android.sh stop              shut the emulator down
#
# Needs the Android command-line tools: brew install --cask android-commandlinetools,
# then sdkmanager "platform-tools" "emulator" "system-images;android-36;google_apis_playstore;arm64-v8a"
set -euo pipefail

SDK="${ANDROID_SDK_ROOT:-$(brew --prefix 2>/dev/null)/share/android-commandlinetools}"
export ANDROID_SDK_ROOT="$SDK" ANDROID_HOME="$SDK"
JAVA_HOME="${JAVA_HOME:-$(/usr/libexec/java_home 2>/dev/null || true)}"
export JAVA_HOME
ADB="$SDK/platform-tools/adb"
EMULATOR="$SDK/emulator/emulator"
IMAGE="system-images;android-36;google_apis_playstore;arm64-v8a"
AVD=homestream

case "${1:-}" in
  create)
    echo no | avdmanager create avd --force -n "$AVD" -k "$IMAGE" -d pixel_7 >/dev/null
    echo "created emulator '$AVD'"
    ;;
  boot)
    "$EMULATOR" -avd "$AVD" -no-snapshot-save -no-boot-anim -gpu auto >/tmp/homestream-emulator.log 2>&1 &
    "$ADB" wait-for-device
    until [[ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; do sleep 2; done
    "$ADB" shell input keyevent KEYCODE_WAKEUP
    "$ADB" shell wm dismiss-keyguard >/dev/null 2>&1 || true
    echo "emulator ready"
    ;;
  open)
    "$ADB" shell am start -a android.intent.action.VIEW -d "'$2'" com.android.chrome >/dev/null
    ;;
  tap)
    "$ADB" shell input tap "$2" "$3"
    ;;
  shot)
    "$ADB" exec-out screencap -p > "$2"
    ;;
  media)
    "$ADB" shell dumpsys media_session | grep -E "package=|state=PlaybackState|metadata:|description=" | head -20
    ;;
  key)
    case "$2" in
      play) code=KEYCODE_MEDIA_PLAY ;; pause) code=KEYCODE_MEDIA_PAUSE ;;
      next) code=KEYCODE_MEDIA_NEXT ;; prev) code=KEYCODE_MEDIA_PREVIOUS ;;
      power) code=KEYCODE_POWER ;; *) echo "unknown key: $2" >&2; exit 1 ;;
    esac
    "$ADB" shell input keyevent "$code"
    ;;
  stop)
    "$ADB" emu kill >/dev/null 2>&1 || true
    ;;
  *)
    sed -n '2,13p' "$0"
    exit 1
    ;;
esac
