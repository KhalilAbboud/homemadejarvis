"""Orchestrates STT, streamed LLM output, and TTS playback."""

from __future__ import annotations

import asyncio
import logging
import os
import re

from brain import LLMError, stream_llm_response, stream_project_plan
from stt import STTError, listen_and_transcribe, warm_up as warm_up_stt
from tts import TTSError, speak, warm_up as warm_up_tts

# Libraries log their own diagnostics at INFO level (the google-genai SDK, for
# example, prints "AFC is enabled with max remote calls: 10." on every streamed
# call). Because the root logger is at INFO with a bare "%(message)s" format,
# that output lands in the middle of the console transcript looking like part
# of OSIRIS's reply. Keep third-party chatter at WARNING or above; OSIRIS's own
# modules keep the configured level.
_NOISY_LIBRARY_LOGGERS = ("google_genai", "httpx", "httpcore", "urllib3")


def _configure_logging() -> None:
    level = getattr(logging, os.getenv("OSIRIS_LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")
    for name in _NOISY_LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))


_configure_logging()
logger = logging.getLogger("osiris.main")

EXIT_PHRASES = {"exit", "quit", "goodbye", "stop", "stop listening"}
# An exit phrase spoken at the end of a short utterance still means "stop"
# ("and stop", "okay, stop listening"), but a negated or question form must not
# ("don't stop", "should I stop?").
_NEGATION_RE = re.compile(r"\b(?:don't|do not|dont|never|can't|cannot|won't|will not)\b")
_MAX_EXIT_UTTERANCE_WORDS = 5
# Wait until a character follows punctuation. This prevents an arriving token
# such as "3." or an abbreviation from being spoken as a finished sentence.
_SENTENCE_END = re.compile(r"^(.+?[.!?])\s+", re.DOTALL)


def _is_exit_command(text: str) -> bool:
    normalized = text.strip().lower().rstrip(".!?").strip()
    if not normalized:
        return False
    if normalized in EXIT_PHRASES:
        return True
    if "?" in text or _NEGATION_RE.search(normalized):
        return False
    words = normalized.replace(",", " ").split()
    if len(words) > _MAX_EXIT_UTTERANCE_WORDS:
        return False
    trailing = " ".join(words[-2:])
    return words[-1] in EXIT_PHRASES or trailing in EXIT_PHRASES


def _is_project_planning_request(text: str) -> bool:
    """Route explicit project-planning requests to the deeper planner."""
    normalized = text.lower()
    triggers = ("project plan", "plan this project", "plan a project", "help me plan")
    return any(trigger in normalized for trigger in triggers)


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


async def _stream_reply_to_speech(prompt: str, *, project_planning: bool = False) -> str:
    """Generate text and play each finished sentence while later text streams."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    reply_parts: list[str] = []

    async def produce() -> None:
        buffer = ""
        try:
            response_stream = stream_project_plan(prompt) if project_planning else stream_llm_response(prompt)
            async for token in response_stream:
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
    print("OSIRIS is online. Speak after 'Listening...'. Say 'exit' to quit.\n")
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

        print("OSIRIS: ", end="", flush=True)
        try:
            await _stream_reply_to_speech(user_text, project_planning=_is_project_planning_request(user_text))
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
