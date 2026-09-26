"""
Diagnostic: records several consecutive short chunks via sd.rec() (the
same pattern stt.py uses) and prints RMS per chunk, so we can see the
real per-call MME startup latency instead of guessing at it.

Usage:
    python test_chunks.py           # 0.3s chunks x 8 (~2.4s), speak throughout
    python test_chunks.py 1.0       # 1.0s chunks x 8
"""

import sys
import numpy as np
import sounddevice as sd

CHUNK_SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 0.3
N_CHUNKS = 8
SAMPLE_RATE = 16000

print(f"Recording {N_CHUNKS} chunks of {CHUNK_SECONDS}s each — talk continuously now.")

for i in range(N_CHUNKS):
    block = sd.rec(
        int(CHUNK_SECONDS * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    block = block.flatten()
    rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
    peak = float(np.abs(block).max())
    print(f"chunk {i}: rms={rms:.5f} peak={peak:.5f}")