"""Allowlisted application launcher; intentionally has no shell-command API."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Final

_APP_NAME = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,63}$")
_DEFAULT_APPS: Final[dict[str, str]] = {
    "calculator": "calc.exe",
    "notepad": "notepad.exe",
    "settings": "ms-settings:",
}


class AppError(RuntimeError):
    """Raised when an application cannot be launched safely."""


@dataclass(frozen=True)
class AppLaunchResult:
    app: str
    target: str


def _configured_apps() -> dict[str, str]:
    """
    Return the app allowlist: built-in defaults, overridden/extended by
    ``OSIRIS_APP_PATHS`` JSON. This merges rather than replaces — setting one
    custom app in OSIRIS_APP_PATHS must not silently remove calculator,
    notepad, and settings. To remove a default, override its name with a
    different target rather than omitting it (omission has no effect).
    """
    apps = dict(_DEFAULT_APPS)
    raw = os.getenv("OSIRIS_APP_PATHS", "").strip()
    if not raw:
        return apps
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AppError("OSIRIS_APP_PATHS must be a JSON object of app names to paths.") from e
    if not isinstance(configured, dict):
        raise AppError("OSIRIS_APP_PATHS must be a JSON object of app names to paths.")

    for name, target in configured.items():
        normalized = str(name).strip().lower()
        if not _APP_NAME.fullmatch(normalized) or not isinstance(target, str) or not target.strip():
            raise AppError("Each OSIRIS_APP_PATHS entry needs a safe name and non-empty target.")
        apps[normalized] = target.strip()
    return apps


async def list_available_apps() -> tuple[str, ...]:
    """Return the names the launcher is allowed to open."""
    return tuple(sorted(_configured_apps()))


def _launch(target: str) -> None:
    if target.lower().startswith(("ms-settings:", "http://", "https://")):
        os.startfile(target)  # type: ignore[attr-defined]  # Windows-only project.
        return
    subprocess.Popen([target], shell=False, close_fds=True)


async def launch_app(app: str) -> AppLaunchResult:
    """Launch one explicitly allowlisted application without using a shell."""
    normalized = app.strip().lower()
    if not _APP_NAME.fullmatch(normalized):
        raise AppError("App names may contain only letters, numbers, spaces, underscores, and hyphens.")

    apps = _configured_apps()
    target = apps.get(normalized)
    if target is None:
        available = ", ".join(sorted(apps)) or "none"
        raise AppError(f"'{app}' is not in the application allowlist. Available: {available}.")
    try:
        await asyncio.to_thread(_launch, target)
    except OSError as e:
        raise AppError(f"Could not launch '{normalized}': {e}") from e
    return AppLaunchResult(app=normalized, target=target)