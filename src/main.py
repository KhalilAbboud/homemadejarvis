"""Orchestrates STT, streamed LLM output, and TTS playback."""

from __future__ import annotations

import asyncio
import logging
import os
import re

from brain import LLMError, stream_llm_response
from stt import STTError, listen_and_transcribe, warm_up as warm_up_stt
from tts import TTSError, speak, warm_up as warm_up_tts
from voice_commands import is_exit_command

# The orchestrator is the one module allowed to know about every other
# module (brain, stt, tts, tools) — it's the composition root. Individual
# modules never import each other directly; see CODING_RULES.md.
from tools.planner import PROJECT_PLANNING_PROMPT, build_project_planning_prompt
from tools.schemas import TOOL_ANNOUNCEMENTS, TOOL_DECLARATIONS, TOOL_DISPATCH

# Libraries log their own diagnostics at INFO level (the google-genai SDK, for
# example, prints "AFC is enabled with max remote calls: 10." on every streamed
# call). Because the root logger is at INFO with a bare "%(message)s" format,
# that output lands in the middle of the console transcript looking like part
# of OSIRIS's reply. Keep third-party chatter at WARNING or above; OSIRIS's own
# modules keep the configured level.
_NOISY_LIBRARY_LOGGERS = ("google_genai", "httpx", "httpcore", "urllib3")

PROJECT_PLANNING_THINKING_LEVEL = os.getenv("PROJECT_PLANNING_THINKING_LEVEL", "high")
# Project plans are long, structured, markdown-ish output (headings, bullet
# milestones, etc). Piping that through the sentence-splitter into TTS would
# have OSIRIS attempt to speak "1. Objective:" and bullet dashes aloud. Instead
# the full plan is printed/logged and OSIRIS speaks a short spoken notice only.
PROJECT_PLAN_SPOKEN_NOTICE = "I've put together the plan — check your screen for the full write-up."


def _configure_logging() -> None:
    level = getattr(logging, os.getenv("OSIRIS_LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")
    for name in _NOISY_LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))


_configure_logging()
logger = logging.getLogger("osiris.main")

# Wait until a character follows punctuation. This prevents an arriving token
# such as "3." or an abbreviation from being spoken as a finished sentence.
_SENTENCE_END = re.compile(r"^(.+?[.!?])\s+", re.DOTALL)

_PROJECT_PLANNING_TRIGGERS = ("project plan", "plan this project", "plan a project", "help me plan")

def _is_project_planning_request(text: str) -> bool:
    """Route explicit project-planning requests to the deeper planner."""
    normalized = text.lower()
    return any(trigger in normalized for trigger in _PROJECT_PLANNING_TRIGGERS)


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
    """
    Generate text and play each finished sentence while later text streams.

    Normal replies stream token-by-token with no change in behavior. When the
    model calls a tool, brain.py yields a short spoken announcement (e.g.
    "Opening notepad.") the moment it decides to call the tool, before the
    tool actually runs — that announcement flows through this same sentence
    buffer and gets spoken immediately, so the round-trip latency of the tool
    call (and the follow-up Gemini call once results are back) happens during
    or after that announcement instead of as silent dead air.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    reply_parts: list[str] = []

    async def produce() -> None:
        buffer = ""
        try:
            async for token in stream_llm_response(
                prompt,
                tools=TOOL_DECLARATIONS,
                tool_dispatch=TOOL_DISPATCH,
                tool_announcements=TOOL_ANNOUNCEMENTS,
            ):
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


async def _stream_plan_to_console(user_text: str) -> str:
    """
    Generate a project plan and print it in full, without routing it through
    TTS sentence-by-sentence (see PROJECT_PLAN_SPOKEN_NOTICE above). Speaks one
    short notice once the plan has finished printing.
    """
    prompt = build_project_planning_prompt(user_text)
    reply_parts: list[str] = []
    async for token in stream_llm_response(
        prompt,
        system_prompt=PROJECT_PLANNING_PROMPT,
        thinking_level=PROJECT_PLANNING_THINKING_LEVEL,
    ):
        reply_parts.append(token)
        print(token, end="", flush=True)
    print()
    await speak(PROJECT_PLAN_SPOKEN_NOTICE)
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
        if is_exit_command(user_text):
            try:
                await speak("Goodbye.")
            except TTSError as e:
                logger.warning("[TTS] %s", e)
            break

        print("OSIRIS: ", end="", flush=True)
        try:
            if _is_project_planning_request(user_text):
                await _stream_plan_to_console(user_text)
            else:
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