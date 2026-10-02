"""Allowlisted application launcher; intentionally has no shell-command API."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Final

from pathlib import Path

_APP_NAME = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,63}$")
_DEFAULT_APPS: Final[dict[str, str]] = {
    "calculator": "calc.exe",
    "notepad": "notepad.exe",
    "settings": "ms-settings:",
}
_PERSISTENT_FILE = Path(__file__).resolve().parent.parent.parent / "allowed_apps.json"

_launched_pids: dict[str, int] = {}


class AppError(RuntimeError):
    """Raised when an application cannot be launched safely."""


@dataclass(frozen=True)
class AppLaunchResult:
    app: str
    target: str


def _configured_apps() -> dict[str, str]:
    """
    Return the app allowlist: built-in defaults, overridden/extended by
    local JSON file and ``OSIRIS_APP_PATHS`` environment variable.
    """
    apps = dict(_DEFAULT_APPS)
    if _PERSISTENT_FILE.exists():
        try:
            with open(_PERSISTENT_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    for k, v in saved.items():
                        apps[str(k).strip().lower()] = str(v).strip()
        except Exception:
            pass

    raw = os.getenv("OSIRIS_APP_PATHS", "").strip()
    if raw:
        try:
            configured = json.loads(raw)
            if isinstance(configured, dict):
                for name, target in configured.items():
                    normalized = str(name).strip().lower()
                    if _APP_NAME.fullmatch(normalized) and isinstance(target, str) and target.strip():
                        apps[normalized] = target.strip()
        except Exception:
            pass

    return apps


def save_allowed_app(name: str, target: str) -> None:
    """Add or update an allowed application and persist to disk."""
    normalized = str(name).strip().lower()
    if not _APP_NAME.fullmatch(normalized):
        raise AppError("App names may contain only letters, numbers, spaces, underscores, and hyphens.")
    target_clean = str(target).strip()
    if not target_clean:
        raise AppError("Target path or command cannot be empty.")

    current = _configured_apps()
    current[normalized] = target_clean
    with open(_PERSISTENT_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)


def remove_allowed_app(name: str) -> bool:
    """Remove an application from the allowlist."""
    normalized = str(name).strip().lower()
    current = _configured_apps()
    if normalized in current:
        del current[normalized]
        with open(_PERSISTENT_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        return True
    return False


async def list_available_apps() -> tuple[str, ...]:
    """Return the names the launcher is allowed to open."""
    return tuple(sorted(_configured_apps()))


def get_allowed_apps_info() -> dict[str, str]:
    """Return dictionary of allowed app names to targets."""
    return _configured_apps()


def _launch(target: str) -> subprocess.Popen | None:
    if target.lower().startswith(("ms-settings:", "http://", "https://")):
        os.startfile(target)  # type: ignore[attr-defined]
        return None  # no PID to track for these
    return subprocess.Popen([target], shell=False, close_fds=True)


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
        proc = await asyncio.to_thread(_launch, target)
    except OSError as e:
        raise AppError(f"Could not launch '{normalized}': {e}") from e
    if proc is not None:
        _launched_pids[normalized] = proc.pid
    return AppLaunchResult(app=normalized, target=target)   


def _close_process(target: str, pid: int | None) -> int:
    if pid is not None:
        try:
            res = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True, check=False)
            return 1 if res.returncode == 0 else 0
        except Exception:
            return 0
    if not target.lower().endswith(".exe"):
        return 0
    exe_name = Path(target).name
    try:
        res = subprocess.run(["taskkill", "/F", "/IM", exe_name], capture_output=True, text=True, check=False)
        return 1 if res.returncode == 0 else 0
    except Exception:
        return 0


async def close_app(app: str) -> str:
    normalized = app.strip().lower()
    if not _APP_NAME.fullmatch(normalized):
        raise AppError("App names may contain only letters, numbers, spaces, underscores, and hyphens.")
    apps = _configured_apps()
    target = apps.get(normalized)
    if target is None:
        available = ", ".join(sorted(apps)) or "none"
        raise AppError(f"'{app}' is not in the application allowlist. Available: {available}.")

    pid = _launched_pids.pop(normalized, None)
    killed = await asyncio.to_thread(_close_process, target, pid)
    if killed:
        scope = "the window I opened" if pid is not None else f"all running {normalized}"
        return f"Successfully closed {scope}."
    return f"{normalized} was closed or not currently running."