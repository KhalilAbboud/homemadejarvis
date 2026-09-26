"""
Quick manual test harness for when no microphone is available.

Reuses the real brain.py (incl. tool-calling loop), tts.py, and
tools/schemas.py exactly as main.py does — the only thing swapped out is
stt.py's listen_and_transcribe(), replaced with a plain input() prompt. This
is NOT a permanent part of the pipeline; delete once you have a mic again, or
keep it around as a standing no-mic dev tool.

Usage:
    python src/test_text_mode.py
    > open notepad
    > what are my system specs
    > search the web for faster-whisper github
    > exit
"""

from __future__ import annotations

import asyncio
import logging

from brain import LLMError, stream_llm_response
from tts import TTSError, speak, warm_up as warm_up_tts
from tools.schemas import TOOL_ANNOUNCEMENTS, TOOL_DECLARATIONS, TOOL_DISPATCH

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("osiris.test_text_mode")

EXIT_PHRASES = {"exit", "quit"}


async def _speak_reply(prompt: str) -> None:
    """Stream a reply (with tool-calling) straight to console + TTS, no sentence buffering."""
    reply_parts: list[str] = []
    async for token in stream_llm_response(
        prompt,
        tools=TOOL_DECLARATIONS,
        tool_dispatch=TOOL_DISPATCH,
        tool_announcements=TOOL_ANNOUNCEMENTS,
    ):
        reply_parts.append(token)
        print(token, end="", flush=True)
    print()
    full_reply = "".join(reply_parts).strip()
    if full_reply:
        try:
            await speak(full_reply)
        except TTSError as e:
            logger.warning("[TTS] %s (text-only, no audio played)", e)


async def run() -> None:
    print("OSIRIS text-mode test (no mic). Type a message, or 'exit' to quit.\n")
    try:
        await warm_up_tts()
    except TTSError as e:
        logger.warning("[TTS warm-up] %s", e)

    while True:
        try:
            user_text = input("You: ").strip()
        except EOFError:
            break
        if not user_text:
            continue
        if user_text.lower() in EXIT_PHRASES:
            break

        print("OSIRIS: ", end="", flush=True)
        try:
            await _speak_reply(user_text)
        except LLMError as e:
            logger.error("[Brain] %s", e)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nInterrupted.")