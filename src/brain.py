"""Provider-agnostic LLM router with low-latency streaming support."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator

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
DEFAULT_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low")
# Hard cap on tool-call round-trips per turn, so a confused model calling
# tools in a loop can't spin forever burning API calls / latency.
GEMINI_MAX_TOOL_TURNS = int(os.getenv("GEMINI_MAX_TOOL_TURNS", "4"))

# Spoken responses should be short. This reduces both model generation and
# speech-synthesis time, while still allowing a user to ask for more detail.
# This is a DEFAULT system prompt, not a hardcoded one: callers that need
# different behavior (e.g. a long-form planner) pass their own via
# `system_prompt` on stream_llm_response(). Brain has no opinion on what that
# prompt should say, and no opinion on what any given tool DOES — it never
# imports tools/, keeping this module decoupled from Module 4. Tool schemas,
# dispatch functions, and spoken announcements are all injected by the caller
# (main.py, the composition root) via the `tools`/`tool_dispatch`/
# `tool_announcements` parameters below.
VOICE_SYSTEM_PROMPT = os.getenv(
    "VOICE_SYSTEM_PROMPT",
    "You are OSIRIS, a helpful voice assistant. Reply in one or two concise "
    "spoken sentences unless the user explicitly asks for detail. Do not use "
    "markdown, lists, or filler.",
)

# Type aliases for the tool-injection parameters, so callers don't need to
# reach into the google-genai SDK just to type their own code.
ToolDispatch = dict[str, Callable[..., Awaitable[dict[str, Any]]]]
ToolAnnouncements = dict[str, Callable[[dict[str, Any]], str]]


class LLMError(Exception):
    """Raised when the selected provider cannot produce a response."""


async def _stream_local_response(prompt: str, *, system_prompt: str, tools: list[dict] | None) -> AsyncIterator[str]:
    if tools:
        # Raw Ollama /api/generate has no native function-calling API the way
        # Gemini does. Rather than hand-roll a prompt-based JSON tool-call
        # protocol (unreliable at 1.5B params, real work), local mode simply
        # answers without tools. Loud enough to notice, not an exception —
        # normal chat must keep working in local mode.
        logger.warning("Tool-calling requested but LLM_MODE=local has no tool support; answering without tools.")

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
    prompt: str,
    *,
    system_prompt: str,
    thinking_level: str = DEFAULT_THINKING_LEVEL,
    tools: list[dict] | None = None,
    tool_dispatch: ToolDispatch | None = None,
    tool_announcements: ToolAnnouncements | None = None,
) -> AsyncIterator[str]:
    if not GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set in the environment/.env file.")
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise LLMError("google-genai SDK not installed. Run: pip install google-genai") from e

    client = genai.Client(api_key=GEMINI_API_KEY)
    config: dict[str, Any] = {
        "system_instruction": system_prompt,
        "thinking_config": {"thinking_level": thinking_level},
    }
    if tools:
        config["tools"] = [types.Tool(function_declarations=tools)]

    contents: list[types.Content] = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]

    try:
        for _turn in range(GEMINI_MAX_TOOL_TURNS):
            stream = await client.aio.models.generate_content_stream(
                model=GEMINI_MODEL, contents=contents, config=config
            )

            model_parts: list = []
            pending_calls: list = []
            async for chunk in stream:
                candidates = chunk.candidates or []
                parts = candidates[0].content.parts if candidates and candidates[0].content else []
                for part in parts or []:
                    # Forward the SDK's own Part objects rather than
                    # reconstructing them from the .text/.function_calls
                    # convenience properties: a rebuilt function-call Part
                    # loses `thought_signature`, which newer Gemini models
                    # require echoed back verbatim on the next turn (missing
                    # it is a 400 INVALID_ARGUMENT, not just degraded quality).
                    model_parts.append(part)
                    if part.text:
                        yield part.text
                    if part.function_call:
                        pending_calls.append(part.function_call)

            if not pending_calls:
                return  # model produced a normal text answer; done.

            # Record the model's own turn (its text so far + the calls it
            # made) before appending results, so the conversation history
            # Gemini sees next stays accurate.
            contents.append(types.Content(role="model", parts=model_parts))

            response_parts = []
            for call in pending_calls:
                announce = (tool_announcements or {}).get(call.name)
                if announce:
                    try:
                        yield announce(dict(call.args or {}))
                    except Exception:  # an announcement must never break the turn
                        logger.warning("Tool announcement for '%s' failed to render.", call.name)

                handler = (tool_dispatch or {}).get(call.name)
                if handler is None:
                    result: dict[str, Any] = {"error": f"Unknown tool '{call.name}'."}
                else:
                    try:
                        result = await handler(**(call.args or {}))
                    except Exception as e:  # a tool must never crash the conversation
                        logger.warning("Tool '%s' raised: %s", call.name, e)
                        result = {"error": str(e)}
                response_parts.append(types.Part.from_function_response(name=call.name, response=result))

            contents.append(types.Content(role="user", parts=response_parts))
            # Loop: ask Gemini to continue now that it has the tool result(s).

        logger.warning("Hit GEMINI_MAX_TOOL_TURNS=%d without a final answer; stopping.", GEMINI_MAX_TOOL_TURNS)
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"Gemini API call failed: {e}") from e


async def stream_llm_response(
    prompt: str,
    *,
    system_prompt: str = VOICE_SYSTEM_PROMPT,
    thinking_level: str = DEFAULT_THINKING_LEVEL,
    tools: list[dict] | None = None,
    tool_dispatch: ToolDispatch | None = None,
    tool_announcements: ToolAnnouncements | None = None,
) -> AsyncIterator[str]:
    """
    Yield response text as it arrives from the selected LLM provider.

    `system_prompt`/`thinking_level` let a caller fully replace the default
    voice-assistant behavior (e.g. for project planning) instead of layering
    conflicting instructions into `prompt`. `tools`/`tool_dispatch`/
    `tool_announcements` enable real function-calling in API mode (ignored,
    with a warning, in local mode) — see tools/schemas.py for how main.py
    builds these.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if LLM_MODE == "local":
        async for text in _stream_local_response(prompt, system_prompt=system_prompt, tools=tools):
            yield text
    elif LLM_MODE == "api":
        async for text in _stream_api_response(
            prompt,
            system_prompt=system_prompt,
            thinking_level=thinking_level,
            tools=tools,
            tool_dispatch=tool_dispatch,
            tool_announcements=tool_announcements,
        ):
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