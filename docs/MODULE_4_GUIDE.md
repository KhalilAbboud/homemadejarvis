# OSIRIS Module 4 Guide

Module 4 lives in `src/tools/`. Its functions are independent from speech input
and output, so they can be reused by the brain, a CLI, or a future UI.

## Submodules

| File | Purpose | Main functions |
| --- | --- | --- |
| `tools/apps.py` | Starts only allowlisted apps. It has no shell-command function. | `list_available_apps()`, `launch_app(app)` |
| `tools/system_info.py` | Reads basic OS, CPU, RAM, and disk information. | `get_system_specs()` |
| `tools/web.py` | Searches public web pages and extracts readable page text. Local/private network targets are blocked. | `search_web(query)`, `fetch_web_page(url)` |
| `tools/planner.py` | Builds a structured prompt for thorough project plans. | `build_project_planning_prompt(project, context, constraints)` |
| `tools/__init__.py` | Re-exports the public Module 4 API from one import location. | `from tools import ...` |

## App allowlist

By default, OSIRIS may open `calculator`, `notepad`, and `settings`. To choose
your own list, set `OSIRIS_APP_PATHS` in `.env` as a JSON object:

```env
OSIRIS_APP_PATHS={"browser":"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe","notepad":"notepad.exe"}
```

Only names in that list can be launched. Arguments and arbitrary shell commands
are intentionally unsupported.

## Project planning

`brain.stream_project_plan()` uses `PROJECT_PLANNING_THINKING_LEVEL` (default:
`high`) in API mode. The voice loop uses it for explicit requests containing
phrases such as `project plan`, `plan this project`, `plan a project`, or
`help me plan`.

The planner asks for useful conclusions: scope, assumptions, architecture,
milestones, dependencies, risks, validation criteria, and next actions. It does
not request or expose private chain-of-thought.

## Changes outside Module 4

- `src/brain.py` adds `stream_project_plan()` and configurable planning depth.
- `src/main.py` recognizes explicit planning requests and streams the deeper
  planning response to the console and TTS.
- `docs/ARCHITECTURE.md` and `docs/HANDOFF.md` describe the new module layout
  and record Gemini function-calling wiring as future work.
- `fakeenv.txt` is a secret-free `.env` template. It documents the app allowlist
  and planning-depth setting without containing your real API key.

## Current boundary

The submodules are implemented and smoke-tested. Project planning is connected
to the voice flow. App launching, system information, and web retrieval are not
yet automatically selected by Gemini; the next implementation step is a
function-calling dispatcher that exposes only these safe functions.
