"""Check a recording of HOMESTREAM_AUDIO_DEVICE=test-signal for glitches.

The test signal repeats every second: a 1 kHz beep (50 ms) on top of a 440 Hz
tone (900 ms), then 100 ms of silence. Played back cleanly, the beeps arrive
exactly 1 s apart, the tone never drops out, and there is nothing but the two
tones. This reports where that isn't so.

    python tools/analyze_test_signal.py recording.raw   # mono s16le, 44.1 kHz
"""

from __future__ import annotations

import sys

import numpy as np

RATE = 44100
HOP = 441  # 10 ms
WINDOW = 2048


def band_power(spectrum: np.ndarray, freqs: np.ndarray, centre: float, width: float = 25) -> np.ndarray:
    band = (freqs > centre - width) & (freqs < centre + width)
    return spectrum[:, band].sum(axis=1)


def analyze(samples: np.ndarray) -> dict:
    x = samples.astype(np.float64) / 32768
    frames = np.lib.stride_tricks.sliding_window_view(x, WINDOW)[::HOP]
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(WINDOW), axis=1)) ** 2
    freqs = np.fft.rfftfreq(WINDOW, 1 / RATE)
    total = spectrum.sum(axis=1) + 1e-12
    tone = band_power(spectrum, freqs, 440)
    beep = band_power(spectrum, freqs, 1000)
    # Harmonics and anything else: what clean playback should not contain.
    residual = np.clip(total - tone - beep, 0, None)
    t = np.arange(len(frames)) * HOP / RATE

    loud = total > total.max() * 1e-3
    if not loud.any():
        return {"seconds": len(x) / RATE, "error": "recording is silent"}

    beep_on = beep > beep.max() * 0.1
    onsets = t[1:][beep_on[1:] & ~beep_on[:-1]]
    intervals = np.diff(onsets)
    jumps = [(round(float(a), 2), round(float(d), 3)) for a, d in zip(onsets[1:], intervals) if abs(d - 1) > 0.03]

    # Tone dropouts: inside each second's tone section, the 440 Hz level should be steady.
    expected = np.median(tone[loud])
    dropouts = []
    for onset in onsets:
        section = (t > onset + 0.08) & (t < onset + 0.85)
        weak = section & (tone < expected * 0.1)
        if weak.any():
            dropouts.append(round(float(onset), 2))

    noise_db = 10 * np.log10(residual[loud] / total[loud] + 1e-12)
    noisy = t[loud][noise_db > -20]
    return {
        "seconds": round(len(x) / RATE, 2),
        "beeps": len(onsets),
        "beep_interval_ms": [round(float(i) * 1000) for i in intervals[:3]] + (["..."] if len(intervals) > 3 else []),
        "timing_jumps": jumps,
        "tone_dropouts_at": dropouts,
        "noise_db_median": round(float(np.median(noise_db)), 1),
        "noisy_windows": int(len(noisy)),
        "noisy_at": sorted({round(float(s), 1) for s in noisy})[:10],
    }


def main() -> int:
    samples = np.fromfile(sys.argv[1], dtype="<i2")
    report = analyze(samples)
    for key, value in report.items():
        print(f"{key:>18}: {value}")
    clean = not report.get("error") and not report["timing_jumps"] and not report["tone_dropouts_at"] and report["noisy_windows"] == 0
    print("\nverdict:", "CLEAN" if clean else "GLITCHES FOUND")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
