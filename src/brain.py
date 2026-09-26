"""Provider-agnostic LLM router with low-latency streaming support."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("osiris.brain")

# The google-genai SDK logs INFO-level diagnostics on every call ("AFC is
# enabled with max remote calls: 10.", "AFC remote call 1 is done.") that would
# otherwise appear in OSIRIS's console transcript. Quiet them here too so
# running this module standalone stays clean.
logging.getLogger("google_genai").setLevel(logging.WARNING)

LLM_MODE = os.getenv("LLM_MODE", "local").strip().lower()
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:1.5b")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
REQUEST_TIMEOUT = 30.0

# Spoken responses should be short. This reduces both model generation and
# speech-synthesis time, while still allowing a user to ask for more detail.
# This is a DEFAULT system prompt, not a hardcoded one: callers that need
# different behavior (e.g. a long-form planner) pass their own via
# `system_prompt` on stream_llm_response(). Brain has no opinion on what that
# prompt should say and never imports from tools/ to build one — keeping this
# module decoupled from Module 4.
VOICE_SYSTEM_PROMPT = os.getenv(
    "VOICE_SYSTEM_PROMPT",
    "You are OSIRIS, a helpful voice assistant. Reply in one or two concise "
    "spoken sentences unless the user explicitly asks for detail. Do not use "
    "markdown, lists, or filler.",
)

DEFAULT_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low")


class LLMError(Exception):
    """Raised when the selected provider cannot produce a response."""


async def _stream_local_response(prompt: str, *, system_prompt: str) -> AsyncIterator[str]:
    url = f"{OLLAMA_HOST}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "system": system_prompt,
        "stream": True,
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            async with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    text = data.get("response", "")
                    if text:
                        yield text
    except httpx.ConnectError as e:
        raise LLMError(f"Could not reach Ollama at {url}. Is it running?") from e
    except httpx.HTTPStatusError as e:
        raise LLMError(f"Ollama returned {e.response.status_code}: {e.response.text}") from e
    except httpx.TimeoutException as e:
        raise LLMError(f"Ollama request timed out after {REQUEST_TIMEOUT}s") from e
    except json.JSONDecodeError as e:
        raise LLMError(f"Ollama returned invalid stream data: {e}") from e


async def _stream_api_response(
    prompt: str, *, system_prompt: str, thinking_level: str = DEFAULT_THINKING_LEVEL
) -> AsyncIterator[str]:
    if not GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set in the environment/.env file.")
    try:
        from google import genai
    except ImportError as e:
        raise LLMError("google-genai SDK not installed. Run: pip install google-genai") from e

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        stream = await client.aio.models.generate_content_stream(
            model=GEMINI_MODEL,
            contents=prompt,
            config={
                "system_instruction": system_prompt,
                "thinking_config": {"thinking_level": thinking_level},
            },
        )
        async for response in stream:
            text = response.text or ""
            if text:
                yield text
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"Gemini API call failed: {e}") from e


async def stream_llm_response(
    prompt: str,
    *,
    system_prompt: str = VOICE_SYSTEM_PROMPT,
    thinking_level: str = DEFAULT_THINKING_LEVEL,
) -> AsyncIterator[str]:
    """
    Yield response text as it arrives from the selected LLM provider.

    `system_prompt` lets a caller (e.g. the orchestrator building a
    project-planning request) fully replace the default voice-assistant
    instructions rather than layering a second, conflicting instruction set
    into `prompt` itself. `thinking_level` only affects API mode.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if LLM_MODE == "local":
        async for text in _stream_local_response(prompt, system_prompt=system_prompt):
            yield text
    elif LLM_MODE == "api":
        async for text in _stream_api_response(prompt, system_prompt=system_prompt, thinking_level=thinking_level):
            yield text
    else:
        raise LLMError(f"Unknown LLM_MODE='{LLM_MODE}'. Use 'local' or 'api'.")


async def get_llm_response(prompt: str, *, system_prompt: str = VOICE_SYSTEM_PROMPT) -> str:
    """Compatibility helper that collects a full streamed response."""
    return "".join([text async for text in stream_llm_response(prompt, system_prompt=system_prompt)]).strip()


if __name__ == "__main__":
    async def _main() -> None:
        try:
            print(f"[{LLM_MODE}]", end=" ")
            async for text in stream_llm_response("Say hello in one short sentence."):
                print(text, end="", flush=True)
            print()
        except LLMError as e:
            logger.error("Brain error: %s", e)

    asyncio.run(_main())