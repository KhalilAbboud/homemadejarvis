"""
Gemini tool-calling wiring for the tools/ package.

This is the ONLY place tool-specific knowledge (schemas, JSON conversion,
spoken announcements) lives. brain.py stays generic — it just calls whatever
is in TOOL_DISPATCH by name and speaks whatever TOOL_ANNOUNCEMENTS returns; it
has no idea these map to apps.py/system_info.py/web.py. main.py (composition
root) is the only module that imports both brain.py and tools.schemas.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

from .apps import AppError, launch_app, list_available_apps
from .system_info import get_system_specs
from .web import WebError, fetch_web_page, search_web

# --- Gemini function_declarations ----------------------------------------

TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "launch_app",
        "description": "Launch an allowlisted desktop application by name.",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "App name, e.g. 'notepad', 'calculator'."},
            },
            "required": ["app"],
        },
    },
    {
        "name": "get_system_specs",
        "description": "Get read-only system specs: OS, CPU count, memory, disk space.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "search_web",
        "description": "Search the public web and return article titles, URLs, and snippets. Use this to quickly find articles, news, and links, then present the results directly to the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query, e.g. 'liver cancer articles'"},
                "max_results": {"type": "integer", "description": "1-10, default 5."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_web_page",
        "description": "Fetch a specific public web page and return its readable text. Use only when you need deep article contents beyond what search_web snippets provide.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Absolute http(s) URL."}},
            "required": ["url"],
        },
    },
]

# --- Dispatch: name -> async(**args) -> JSON-serializable dict ------------


async def _dispatch_launch_app(app: str) -> dict[str, Any]:
    try:
        result = await launch_app(app)
        return {"app": result.app, "target": result.target}
    except AppError as e:
        return {"error": str(e), "available_apps": list(await list_available_apps())}


async def _dispatch_get_system_specs() -> dict[str, Any]:
    return await get_system_specs()


async def _dispatch_search_web(query: str, max_results: int = 5) -> dict[str, Any]:
    try:
        results = await search_web(query, max_results=max_results)
        return {"results": [dataclasses.asdict(r) for r in results]}
    except (WebError, ValueError) as e:
        return {"error": str(e)}


async def _dispatch_fetch_web_page(url: str) -> dict[str, Any]:
    try:
        page = await fetch_web_page(url)
        return dataclasses.asdict(page)
    except (WebError, ValueError) as e:
        return {"error": str(e)}


TOOL_DISPATCH: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
    "launch_app": _dispatch_launch_app,
    "get_system_specs": _dispatch_get_system_specs,
    "search_web": _dispatch_search_web,
    "fetch_web_page": _dispatch_fetch_web_page,
}

# --- Spoken pre-announcements ----------------------------------------------
# Spoken immediately once the model decides to call a tool, BEFORE the tool
# actually runs — so there's no silent pause while e.g. a web request is in
# flight. Each takes the raw call args dict; keep these short (they go straight
# to TTS) and safe against missing/malformed args (a model could hallucinate
# an arg name — never let an announcement itself raise).

TOOL_ANNOUNCEMENTS: dict[str, Callable[[dict[str, Any]], str]] = {
    "launch_app": lambda args: f"Opening {args.get('app', 'that')}.",
    "get_system_specs": lambda _args: "Checking your system specs.",
    "search_web": lambda args: f"Searching the web for {args.get('query', 'that')}.",
    "fetch_web_page": lambda _args: "Pulling up that page.",
}