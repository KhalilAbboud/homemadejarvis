"""
Standalone microphone diagnostic. No Whisper, no async, no callbacks —
just the simplest possible sounddevice capture, to isolate whether the
problem is PortAudio/the device itself vs. our stt.py logic.

Usage:
    python test_mic.py            # uses system default input device
    python test_mic.py 28         # uses device index 28
"""

import sys
import numpy as np
import sounddevice as sd

DEVICE = int(sys.argv[1]) if len(sys.argv) > 1 else None
DURATION = 4  # seconds
SAMPLE_RATE = 16000

print(f"Device: {DEVICE if DEVICE is not None else 'system default'}")

try:
    info = sd.query_devices(DEVICE, "input")
    print(f"Device info: {info['name']}")
    print(f"Device default samplerate: {info['default_samplerate']}")
except Exception as e:
    print(f"Could not query device info: {e}")

print(f"Recording {DURATION}s at {SAMPLE_RATE} Hz... speak now.")

try:
    audio = sd.rec(
        int(DURATION * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=DEVICE,
    )
    sd.wait()
    print("Recording finished (this line proving it did not hang).")
except Exception as e:
    print(f"RECORDING FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

peak = float(np.abs(audio).max())
rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
print(f"Peak amplitude: {peak:.5f}")
print(f"RMS: {rms:.5f}")

if peak < 1e-4:
    print("=> Captured pure silence. Stream opened but no signal reached it.")
elif rms < 0.005:
    print("=> Very quiet signal. Mic level may be low, or wrong device is capturing.")
else:
    print("=> Got a real signal. Capture works on this device.")