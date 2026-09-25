"""Orchestrates STT, streamed LLM output, and TTS playback."""

from __future__ import annotations

import asyncio
import logging
import re

from brain import LLMError, stream_llm_response
from stt import STTError, listen_and_transcribe, warm_up as warm_up_stt
from tts import TTSError, speak, warm_up as warm_up_tts

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("jarvis.main")

EXIT_PHRASES = {"exit", "quit", "goodbye", "stop", "stop listening"}
# Wait until a character follows punctuation. This prevents an arriving token
# such as "3." or an abbreviation from being spoken as a finished sentence.
_SENTENCE_END = re.compile(r"^(.+?[.!?])\s+", re.DOTALL)


def _is_exit_command(text: str) -> bool:
    return text.strip().lower().rstrip(".!?") in EXIT_PHRASES


def _pop_complete_sentence(buffer: str) -> tuple[str | None, str]:
    """Return one finished sentence and leave the unfinished text buffered."""
    match = _SENTENCE_END.match(buffer)
    if not match:
        return None, buffer
    return match.group(1).strip(), buffer[match.end():]


async def _warm_stt() -> None:
    try:
        await warm_up_stt()
    except STTError as e:
        logger.warning("[STT warm-up] %s", e)


async def _warm_tts() -> None:
    try:
        await warm_up_tts()
    except TTSError as e:
        logger.warning("[TTS warm-up] %s", e)


async def _stream_reply_to_speech(prompt: str) -> str:
    """Generate text and play each finished sentence while later text streams."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    reply_parts: list[str] = []

    async def produce() -> None:
        buffer = ""
        try:
            async for token in stream_llm_response(prompt):
                reply_parts.append(token)
                print(token, end="", flush=True)
                buffer += token
                while True:
                    sentence, buffer = _pop_complete_sentence(buffer)
                    if sentence is None:
                        break
                    await queue.put(sentence)
            if buffer.strip():
                await queue.put(buffer.strip())
        finally:
            await queue.put(None)

    async def consume() -> None:
        while True:
            sentence = await queue.get()
            if sentence is None:
                return
            await speak(sentence)

    await asyncio.gather(produce(), consume())
    print()
    return "".join(reply_parts).strip()


async def run() -> None:
    print("JARVIS is online. Speak after 'Listening...'. Say 'exit' to quit.\n")
    asyncio.create_task(_warm_stt())
    asyncio.create_task(_warm_tts())

    while True:
        print("Listening...")
        try:
            user_text = await listen_and_transcribe()
        except STTError as e:
            logger.warning("[STT] %s", e)
            continue
        if not user_text:
            continue

        print(f"You: {user_text}")
        if _is_exit_command(user_text):
            try:
                await speak("Goodbye.")
            except TTSError as e:
                logger.warning("[TTS] %s", e)
            break

        print("JARVIS: ", end="", flush=True)
        try:
            await _stream_reply_to_speech(user_text)
        except LLMError as e:
            logger.error("\n[Brain] %s", e)
            try:
                await speak("Sorry, I couldn't reach my language model just now.")
            except TTSError as speech_error:
                logger.error("[TTS] %s", speech_error)
        except TTSError as e:
            logger.error("\n[TTS] %s", e)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nInterrupted. Shutting down.")
