"""
Module 1 (Voice In)
--------------------
Microphone capture + transcription using faster-whisper.

Decoupling: this module knows nothing about the brain (Module 2), TTS
(Module 3), or tools (Module 4). It exposes one public coroutine:
    listen_and_transcribe() -> str
"""

from __future__ import annotations

import os
import asyncio
import logging
import threading
from typing import Optional

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("jarvis.stt")

# --- Configuration -----------------------------------------------------

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")  # fast on CPU

MAX_RECORD_SECONDS = float(os.getenv("STT_MAX_SECONDS", "8"))
SILENCE_THRESHOLD = float(os.getenv("STT_SILENCE_THRESHOLD", "0.004"))  # RMS
TRAILING_PAD_SECONDS = float(os.getenv("STT_TRAILING_PAD", "0.4"))
SPEECH_START_TIMEOUT = float(os.getenv("STT_START_TIMEOUT", "5"))
BLOCK_SECONDS = float(os.getenv("STT_BLOCK_SECONDS", "0.1"))

# Optional: pin a specific input device if the system default is wrong.
# NOTE: the device must support SAMPLE_RATE natively (sd.rec() does not
# resample) — run `python -m sounddevice` to check "default_samplerate".
# On Windows, MME devices are usually flexible; WASAPI/WDM-KS often are not.
_raw_device = os.getenv("STT_INPUT_DEVICE")
STT_INPUT_DEVICE = int(_raw_device) if _raw_device and _raw_device.strip().isdigit() else (_raw_device or None)


class STTError(Exception):
    """Raised when recording or transcription fails."""


# --- Lazy singleton model (expensive to load; load once, reuse) --------

_model: Optional[WhisperModel] = None
_model_lock = threading.Lock()


def _get_model() -> WhisperModel:
    global _model
    with _model_lock:
        if _model is None:
            try:
                logger.info("Loading faster-whisper model '%s'...", WHISPER_MODEL_SIZE)
                _model = WhisperModel(
                    WHISPER_MODEL_SIZE,
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                )
            except Exception as e:
                raise STTError(f"Failed to load Whisper model: {e}") from e
    return _model


async def warm_up() -> None:
    """Load Whisper early so startup happens before the first utterance."""
    await asyncio.to_thread(_get_model)


# --- Blocking recording (runs in a thread) ------------------------------

def _trim_silence(audio: np.ndarray) -> np.ndarray:
    """Cuts leading silence and trims trailing silence to a short pad."""
    block = int(SAMPLE_RATE * 0.1)  # 100ms windows
    n_blocks = len(audio) // block
    if n_blocks == 0:
        return audio

    rms = np.array(
        [
            np.sqrt(np.mean(audio[i * block:(i + 1) * block].astype(np.float64) ** 2))
            for i in range(n_blocks)
        ]
    )
    speaking = rms > SILENCE_THRESHOLD
    if not speaking.any():
        return audio[:0]  # signals "no speech" to the caller

    first = int(np.argmax(speaking))
    last = int(np.max(np.nonzero(speaking)[0]))
    pad_blocks = int(TRAILING_PAD_SECONDS / 0.1)
    end_block = min(n_blocks, last + 1 + pad_blocks)

    return audio[first * block:end_block * block]


def _record_blocking() -> np.ndarray:
    """
    Records with one continuously-open input stream. It does not reopen a
    Bluetooth microphone for each chunk, but can stop after speech ends.
    Blocking — must be called via asyncio.to_thread.
    """
    try:
        block_size = max(1, int(BLOCK_SECONDS * SAMPLE_RATE))
        start_limit = max(1, int(SPEECH_START_TIMEOUT / BLOCK_SECONDS))
        trailing_limit = max(1, int(TRAILING_PAD_SECONDS / BLOCK_SECONDS))
        max_blocks = max(1, int(MAX_RECORD_SECONDS / BLOCK_SECONDS))
        blocks: list[np.ndarray] = []
        heard_speech = False
        quiet_blocks = 0
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            device=STT_INPUT_DEVICE,
            blocksize=block_size,
        ) as stream:
            for index in range(max_blocks):
                block, overflowed = stream.read(block_size)
                if overflowed:
                    logger.warning("Microphone input overflowed; continuing with available audio.")
                block = block[:, 0].copy()
                blocks.append(block)
                rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
                if rms > SILENCE_THRESHOLD:
                    heard_speech = True
                    quiet_blocks = 0
                elif heard_speech:
                    quiet_blocks += 1
                    if quiet_blocks >= trailing_limit:
                        break
                elif index + 1 >= start_limit:
                    break
    except sd.PortAudioError as e:
        raise STTError(
            f"Microphone error: {e}. If you pinned STT_INPUT_DEVICE, "
            f"confirm it supports {SAMPLE_RATE} Hz natively "
            f"(check with `python -m sounddevice`)."
        ) from e

    return _trim_silence(np.concatenate(blocks)) if blocks else np.array([], dtype=DTYPE)


# --- Public API ----------------------------------------------------------

async def listen_and_transcribe() -> str:
    """
    Records from the microphone and returns the transcribed text.
    Raises STTError on microphone or transcription failure.
    """
    audio = await asyncio.to_thread(_record_blocking)

    if audio.size == 0:
        raise STTError(
            f"No speech detected in {SPEECH_START_TIMEOUT}s "
            f"(RMS never exceeded {SILENCE_THRESHOLD})."
        )

    logger.info("Recording captured, transcribing (model load is slow on first run)...")

    def _transcribe() -> str:
        model = _get_model()
        try:
            segments, _info = model.transcribe(
                audio,
                language="en",
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                word_timestamps=False,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as e:
            raise STTError(f"Transcription failed: {e}") from e

    return await asyncio.to_thread(_transcribe)


# --- Manual test entry point ---------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    async def _main() -> None:
        try:
            dev_info = sd.query_devices(STT_INPUT_DEVICE, "input")
            print(f"Using input device: {STT_INPUT_DEVICE} -> {dev_info['name']}")
        except Exception as e:
            print(f"Could not resolve STT_INPUT_DEVICE={STT_INPUT_DEVICE!r}: {e}")
        print("Listening... speak now.")
        try:
            text = await listen_and_transcribe()
            print(f"Transcribed: {text!r}")
        except STTError as e:
            logger.error("STT error: %s", e)

    asyncio.run(_main())
