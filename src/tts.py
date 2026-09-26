"""Voice output with local Piper, Edge, and pyttsx3 engines."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import tempfile
import threading
import wave
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("osiris.tts")

TTS_ENGINE = os.getenv("TTS_ENGINE", "piper").strip().lower()
PIPER_MODEL = Path(os.getenv("PIPER_MODEL", "models/piper/en_US-lessac-low.onnx"))
PIPER_LENGTH_SCALE = float(os.getenv("PIPER_LENGTH_SCALE", "0.9"))
PIPER_LEADING_SILENCE = float(os.getenv("PIPER_LEADING_SILENCE", "0.18"))
EDGE_VOICE = os.getenv("EDGE_VOICE", "en-US-GuyNeural")
PYTTSX3_RATE = int(os.getenv("PYTTSX3_RATE", "180"))
TTS_CHUNK_CHARS = int(os.getenv("TTS_CHUNK_CHARS", "220"))


class TTSError(Exception):
    """Raised when speech synthesis or playback fails."""


_piper_voice = None
_piper_lock = threading.Lock()


def _get_piper_voice():
    """Load Piper once; process startup/model loading dominates latency."""
    global _piper_voice
    with _piper_lock:
        if _piper_voice is None:
            try:
                from piper import PiperVoice
                _piper_voice = PiperVoice.load(PIPER_MODEL)
            except Exception as e:
                raise TTSError(f"Could not load Piper model: {e}") from e
    return _piper_voice


async def warm_up() -> None:
    """Preload the local voice so the first spoken response is not delayed."""
    if TTS_ENGINE == "piper":
        if not PIPER_MODEL.is_file():
            raise TTSError(f"Piper model not found: {PIPER_MODEL}")
        await asyncio.to_thread(_get_piper_voice)


def _speech_chunks(text: str) -> list[str]:
    """Create short, sentence-aligned chunks for quicker first playback."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > TTS_CHUNK_CHARS:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text]


def _cache_path(engine: str, voice: str, text: str, suffix: str) -> Path:
    cache_dir = Path(tempfile.gettempdir()) / "osiris_tts_cache"
    cache_dir.mkdir(exist_ok=True)
    digest = hashlib.sha256(f"{engine}|{voice}|{text}".encode()).hexdigest()
    return cache_dir / f"{digest}{suffix}"


def _add_leading_silence(wav_path: Path) -> None:
    """Give Windows audio devices time to wake before the first phoneme."""
    if PIPER_LEADING_SILENCE <= 0:
        return
    padded_path = wav_path.with_suffix(".padded.wav")
    try:
        with wave.open(str(wav_path), "rb") as source:
            params = source.getparams()
            frames = source.readframes(source.getnframes())
        silence_frames = int(params.framerate * PIPER_LEADING_SILENCE)
        silence = b"\x00" * (silence_frames * params.nchannels * params.sampwidth)
        with wave.open(str(padded_path), "wb") as target:
            target.setparams(params)
            target.writeframes(silence)
            target.writeframes(frames)
        padded_path.replace(wav_path)
    finally:
        padded_path.unlink(missing_ok=True)


async def _play_file(path: Path) -> None:
    if path.suffix.lower() == ".wav" and os.name == "nt":
        # Native WAV playback is more reliable than playsound's MCI backend
        # for Piper files on Windows.
        import winsound
        try:
            await asyncio.to_thread(winsound.PlaySound, str(path), winsound.SND_FILENAME)
            return
        except RuntimeError as e:
            raise TTSError(f"Audio playback failed: {e}") from e

    try:
        from playsound import playsound
    except ImportError as e:
        raise TTSError("playsound not installed. Run: pip install playsound==1.2.2") from e
    try:
        await asyncio.to_thread(playsound, str(path))
    except Exception as e:
        raise TTSError(f"Audio playback failed: {e}") from e


async def _speak_piper(text: str) -> None:
    if not PIPER_MODEL.is_file():
        raise TTSError(f"Piper model not found: {PIPER_MODEL}")

    def synthesize(chunk: str, wav_path: Path) -> None:
        try:
            from piper.config import SynthesisConfig
            voice = _get_piper_voice()
            with wave.open(str(wav_path), "wb") as wav_file:
                voice.synthesize_wav(
                    chunk,
                    wav_file,
                    syn_config=SynthesisConfig(length_scale=PIPER_LENGTH_SCALE),
                )
        except TTSError:
            raise
        except Exception as e:
            raise TTSError(f"Piper synthesis failed: {e}") from e

    for chunk in _speech_chunks(text):
        # Include synthesis settings in the key so old unpadded cache files
        # are never reused after an audio-timing adjustment.
        voice_key = f"{PIPER_MODEL}|{PIPER_LENGTH_SCALE}|{PIPER_LEADING_SILENCE}"
        wav_path = _cache_path("piper", voice_key, chunk, ".wav")
        if not wav_path.exists():
            await asyncio.to_thread(synthesize, chunk, wav_path)
            await asyncio.to_thread(_add_leading_silence, wav_path)
        await _play_file(wav_path)


async def _speak_edge(text: str) -> None:
    try:
        import edge_tts
    except ImportError as e:
        raise TTSError("edge-tts not installed. Run: pip install edge-tts") from e
    for chunk in _speech_chunks(text):
        mp3_path = _cache_path("edge", EDGE_VOICE, chunk, ".mp3")
        if not mp3_path.exists():
            try:
                await edge_tts.Communicate(chunk, EDGE_VOICE).save(str(mp3_path))
            except Exception as e:
                raise TTSError(f"edge-tts synthesis failed (check internet connection): {e}") from e
        await _play_file(mp3_path)


async def _speak_pyttsx3(text: str) -> None:
    try:
        import pyttsx3
    except ImportError as e:
        raise TTSError("pyttsx3 not installed. Run: pip install pyttsx3") from e

    def _sync_speak() -> None:
        engine = pyttsx3.init()
        try:
            engine.setProperty("rate", PYTTSX3_RATE)
            engine.say(text)
            engine.runAndWait()
        finally:
            engine.stop()

    try:
        await asyncio.to_thread(_sync_speak)
    except Exception as e:
        raise TTSError(f"pyttsx3 synthesis/playback failed: {e}") from e


async def speak(text: str) -> None:
    """Synthesize and play non-empty text using the configured TTS engine."""
    if not text or not text.strip():
        raise ValueError("text must be a non-empty string")
    if TTS_ENGINE == "piper":
        await _speak_piper(text)
    elif TTS_ENGINE == "edge":
        await _speak_edge(text)
    elif TTS_ENGINE == "pyttsx3":
        await _speak_pyttsx3(text)
    else:
        raise TTSError(f"Unknown TTS_ENGINE='{TTS_ENGINE}'. Use 'piper', 'edge', or 'pyttsx3'.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(speak("Hello, I am OSIRIS. Local Piper voice is working."))
