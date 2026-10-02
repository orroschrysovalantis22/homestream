"""Write live audio from a Core Audio input (e.g. BlackHole) to stdout as raw PCM.

ffmpeg's own macOS capture (-f avfoundation) silently loses around 12% of the
audio from BlackHole, which sounds like crackle. PortAudio captures it
cleanly, so HomeStream captures here and only uses ffmpeg to encode.

    python capture.py "BlackHole 2ch"              # 16-bit stereo at the device's rate
    python capture.py --seconds 1 "BlackHole 2ch"  # stop after one second
    python capture.py --info "BlackHole 2ch"       # print the sample rate and exit
"""

from __future__ import annotations

import argparse
import queue
import signal
import sys

CHANNELS = 2


def device_rate(device: str) -> int:
    import sounddevice as sd

    return int(sd.query_devices(device, "input")["default_samplerate"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("device")
    parser.add_argument("--info", action="store_true", help="print the device's sample rate and exit")
    parser.add_argument("--seconds", type=float, help="stop after this much audio")
    args = parser.parse_args()

    import sounddevice as sd

    try:
        rate = device_rate(args.device)
    except (ValueError, sd.PortAudioError) as e:
        print(f"Audio device not found: {args.device} ({e})", file=sys.stderr)
        return 2
    if args.info:
        print(rate)
        return 0

    blocks: queue.Queue[bytes] = queue.Queue(maxsize=500)

    def on_audio(data, frames, time, status):
        if status:
            print(f"capture: {status}", file=sys.stderr, flush=True)
        try:
            blocks.put_nowait(bytes(data))
        except queue.Full:
            pass  # the reader stopped; nothing useful to do

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    out = sys.stdout.buffer
    remaining = int(args.seconds * rate * CHANNELS * 2) if args.seconds else None
    try:
        with sd.RawInputStream(device=args.device, samplerate=rate, channels=CHANNELS, dtype="int16", callback=on_audio):
            while remaining is None or remaining > 0:
                block = blocks.get()
                if remaining is not None:
                    block = block[:remaining]
                    remaining -= len(block)
                out.write(block)
                out.flush()
        return 0
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    except sd.PortAudioError as e:
        print(f"Can't capture {args.device!r}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
