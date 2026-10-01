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
# Two-tier local model setup: FAST for snappy chitchat, SMART for general reasoning.
OLLAMA_MODEL_FAST = os.getenv("OLLAMA_MODEL_FAST", "smollm2:1.7b-instruct-q4_0")
OLLAMA_MODEL_SMART = os.getenv("OLLAMA_MODEL_SMART", "llama3.2:1b")
# Legacy fallback — still honoured if someone sets it explicitly.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
REQUEST_TIMEOUT = 30.0
DEFAULT_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low")
# Hard cap on tool-call round-trips per turn, so a confused model calling
# tools in a loop can't spin forever burning API calls / latency.
GEMINI_MAX_TOOL_TURNS = int(os.getenv("GEMINI_MAX_TOOL_TURNS", "6"))

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
    "You are OSIRIS, a helpful personal AI assistant. Reply in concise sentences. "
    "When asked to open, launch, close, or terminate apps, or check system specs, or search the web, "
    "always use the available tools directly.",
)

RESEARCH_SYSTEM_PROMPT = os.getenv(
    "RESEARCH_SYSTEM_PROMPT",
    "You are OSIRIS, an intelligent assistant. When researching, searching for articles, "
    "or looking for live information, use search_web to find relevant results. "
    "When asked to open, launch, close, or terminate applications or inspect system specs, use the respective tool. "
    "Once you obtain search results with titles, snippets, and URLs, synthesize and provide your "
    "complete response with article titles, clickable markdown URLs ([Title](url)), and key takeaways directly to the user.",
)

# Type aliases for the tool-injection parameters, so callers don't need to
# reach into the google-genai SDK just to type their own code.
ToolDispatch = dict[str, Callable[..., Awaitable[dict[str, Any]]]]
ToolAnnouncements = dict[str, Callable[[dict[str, Any]], str]]


# ---------------------------------------------------------------------------
#  Hybrid & Local Model Routing
# ---------------------------------------------------------------------------

# Keywords specifically indicating research, article queries, web lookup, or reports.
# When LLM_MODE=local, queries matching these are routed to the cloud API
# (Gemini) so the model can invoke web search/fetch tools and provide deep, grounded answers.
_RESEARCH_KEYWORDS = {
    "research", "scientific",
    "in-depth", "in depth",
    "whitepaper", "literature review", "full paper", "comprehensive analysis",
    "article", "articles", "paper", "papers", "publication", "publications",
    "journal", "journals", "news", "headline", "headlines",
}

_WEB_ACTION_KEYWORDS = {
    "search", "find", "fetch", "lookup", "look up", "browse", "get articles", "find articles",
    "show articles", "bring articles", "look for",
}


def _is_research_request(prompt: str) -> bool:
    """Return True if prompt relates to research, articles, or web data retrieval."""
    lowered = prompt.lower()
    if any(kw in lowered for kw in _RESEARCH_KEYWORDS):
        return True
    if any(kw in lowered for kw in _WEB_ACTION_KEYWORDS) and any(
        target in lowered for target in ("article", "articles", "news", "paper", "web", "online", "info", "information")
    ):
        return True
    return False


# Keywords for general complex prompts (logic, code, explanations, etc.)
# routed locally to OLLAMA_MODEL_SMART (llama3.2:1b).
_COMPLEX_KEYWORDS = {
    # reasoning / analytical
    "explain", "why", "how", "compare", "contrast", "analyze", "analyse",
    "summarize", "summarise", "evaluate", "reason", "think", "opinion",
    # creative / generative
    "plan", "design", "debug", "refactor", "implement", "write", "code",
    "create", "build", "generate", "draft", "compose",
    # instructional
    "teach", "tutorial", "guide", "step by step", "walk me through",
    # comparative / advisory
    "pros and cons", "trade-off", "tradeoff", "difference between",
    "recommend", "suggest", "describe", "what if", "list",
}
# Minimum word count that tips towards the smart model on its own.
_SMART_WORD_THRESHOLD = 18
# Score at or above this → smart model.
_SMART_SCORE_THRESHOLD = 1


def _pick_local_model(prompt: str) -> str:
    """Choose between the fast and smart Ollama models.

    Returns the model tag string.  If the legacy OLLAMA_MODEL env-var is set,
    it always wins (backward-compat).
    """
    if OLLAMA_MODEL:
        return OLLAMA_MODEL

    lowered = prompt.lower()
    words = lowered.split()
    score = 0

    # Very short utterances (greetings, one-liners) are never complex enough
    # to justify the smarter model, even if they contain a stray keyword like
    # "how" — "how are you?" vs "how does attention work in transformers?".
    if len(words) <= 5:
        logger.info("[local-router] score=0 (short-prompt fast-path) → %s", OLLAMA_MODEL_FAST)
        return OLLAMA_MODEL_FAST

    # Long prompts are usually complex.
    if len(words) >= _SMART_WORD_THRESHOLD:
        score += 2

    # Presence of complexity keywords.
    for kw in _COMPLEX_KEYWORDS:
        if kw in lowered:
            score += 1
            break  # one hit is enough; avoid double-counting near-synonyms

    # Multiple sentences hint at a richer request.
    sentence_enders = sum(1 for ch in prompt if ch in ".!?")
    if sentence_enders >= 2:
        score += 1


    chosen = OLLAMA_MODEL_SMART if score >= _SMART_SCORE_THRESHOLD else OLLAMA_MODEL_FAST
    logger.info("[local-router] score=%d → %s", score, chosen)
    return chosen


class LLMError(Exception):
    """Raised when the selected provider cannot produce a response."""


# Module-level flag so the local-mode tool-calling warning only fires once.
_local_tool_warning_shown = False


async def _stream_local_response(prompt: str, *, system_prompt: str, tools: list[dict] | None) -> AsyncIterator[str]:
    global _local_tool_warning_shown
    if tools and not _local_tool_warning_shown:
        # Raw Ollama /api/generate has no native function-calling API the way
        # Gemini does. Rather than hand-roll a prompt-based JSON tool-call
        # protocol (unreliable at 1.5B params, real work), local mode simply
        # answers without tools. Loud enough to notice, not an exception —
        # normal chat must keep working in local mode.
        logger.warning("Tool-calling requested but LLM_MODE=local has no tool support; answering without tools.")
        _local_tool_warning_shown = True

    model = _pick_local_model(prompt)
    url = f"{OLLAMA_HOST}/api/generate"
    payload = {
        "model": model,
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
    # Hybrid routing: if local mode is set but the user asks for in-depth research,
    # offload to cloud API (Gemini) if GEMINI_API_KEY is available.
    if LLM_MODE == "local" and _is_research_request(prompt) and GEMINI_API_KEY:
        logger.info("[router] Research query detected -> hybrid offload to Gemini API")
        effective_sys_prompt = RESEARCH_SYSTEM_PROMPT if system_prompt == VOICE_SYSTEM_PROMPT else system_prompt
        async for text in _stream_api_response(
            prompt,
            system_prompt=effective_sys_prompt,
            thinking_level=thinking_level,
            tools=tools,
            tool_dispatch=tool_dispatch,
            tool_announcements=tool_announcements,
        ):
            yield text
        return

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