"""Safe, decoupled capabilities available to OSIRIS."""

from .apps import AppError, AppLaunchResult, launch_app, list_available_apps
from .planner import PROJECT_PLANNING_PROMPT, build_project_planning_prompt
from .system_info import get_system_specs
from .web import SearchResult, WebError, WebPage, fetch_web_page, search_web

__all__ = [
    "AppError",
    "AppLaunchResult",
    "PROJECT_PLANNING_PROMPT",
    "SearchResult",
    "WebError",
    "WebPage",
    "build_project_planning_prompt",
    "fetch_web_page",
    "get_system_specs",
    "launch_app",
    "list_available_apps",
    "search_web",
]