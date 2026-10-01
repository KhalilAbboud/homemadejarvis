# Session Handoff State — OSIRIS (v0.1 checkpoint)

## Last Completed Task
- **Hybrid/multi-tier local routing in `brain.py`:** `smollm2:1.7b-instruct-q4_0` for fast chitchat, `llama3.2:1b` for general reasoning/code, with a keyword+heuristic scorer (`_pick_local_model`) choosing between them. A separate heuristic (`_is_research_request`) detects research/article/web-lookup intent and offloads those prompts from local mode straight to the Gemini API with tools attached, even when `LLM_MODE=local`.
- **`apps.py` gained `close_app()`** (force-terminate via `taskkill /F /IM`) and a **persistent allowlist**: `allowed_apps.json` at the repo root, merged with `_DEFAULT_APPS` and `OSIRIS_APP_PATHS`, with `save_allowed_app()`/`remove_allowed_app()` to edit it on disk.
- **`schemas.py`** gained a `close_app` tool declaration/dispatch/announcement alongside the existing four.
- **New desktop GUI: `osiris_chat.py`** (PySide6) — dark glassmorphic chat window, sidebar with quick research prompts and an apps panel (add/remove/launch/close), Markdown→HTML rendering with clickable links in `QTextBrowser`, voice mode reusing `listen_and_transcribe`/`speak`. Work (`stream_llm_response`, `listen_and_transcribe`, `speak`) runs on a dedicated `QThread` via `AssistantWorker` so the UI doesn't block during LLM calls.

## ⚠️ Urgent — fix before starting Module 5 (wake word)
These are regressions/risks introduced by the above, not style nits:

1. **`brain.py` reintroduces the `brain.py → tools/` coupling we deliberately removed, and leaks tool access into project planning.** `stream_llm_response()` now lazily imports `tools.schemas` and auto-attaches `TOOL_DECLARATIONS` whenever `tools=None` is passed. `main.py`'s `_stream_plan_to_console()` calls `stream_llm_response()` without `tools` — so planning requests silently get `launch_app`/`search_web` etc. attached, which was explicitly designed never to happen (planning should be pure text generation). **Fix:** remove the lazy-load block; every caller that wants tools passes them explicitly (as `main.py`'s chat path and `test_text_mode.py` already do). If `osiris_chat.py` wants defaults, it should import `tools.schemas` itself — it already imports `tools.apps` directly, so that's consistent.
2. **Hybrid local→cloud router over-matches on common words.** `_WEB_ACTION_KEYWORDS` includes `"find"`/`"look for"`, and the target-word list includes `"for"`/`"about"` — near-universal words. `"look for errors in my code"` or `"find a bug in this function"` will match and get silently offloaded to Gemini with web tools attached, even in `LLM_MODE=local`. This defeats local mode's privacy/latency guarantee for a lot of ordinary prompts. Needs a tighter rule (require an explicit web-ish noun like "article"/"web"/"online" to actually co-occur, not filler words like "for").
3. **`close_app()` kills by executable name system-wide, not by the process OSIRIS launched.** `taskkill /F /IM <exe_name>` terminates *every* running instance of that binary. `launch_app()` doesn't track the `Popen` PID it creates, so there's no way to scope a close to just what OSIRIS opened. For anything with multiple windows/instances (a browser, say) this is a real destructive blast radius with no confirmation step. **Fix:** store the PID (or `Popen` handle) per launch, kill by PID, and only fall back to by-name if that's an accepted tradeoff.
4. **Config-loading failures are now silently swallowed in `apps.py`.** Both the `allowed_apps.json` read and the `OSIRIS_APP_PATHS` parse do bare `except Exception: pass`. A typo'd JSON file reverts to defaults with zero log output — you'd have no way to tell why an app you added isn't showing up.
5. **`stt.py`'s logger is still named `"jarvis.stt"`** — every other module renamed to `osiris.*`; this one was missed.
6. **`osiris_chat.py`'s app launch/close buttons bypass the worker thread.** `_handle_app_launch`/`_handle_app_close` call `asyncio.run(...)` directly on the Qt main thread instead of routing through `AssistantWorker`'s `QThread` like chat/voice do — inconsistent, and risks a UI freeze if a launch ever blocks. They also re-implement exit-phrase handling separately rather than reusing `main.py`'s negation-aware `_is_exit_command`.

Priority: **#1 and #3** are the ones to actually block on (behavioral regression that defeats a deliberate design decision; destructive action with no scoping). #2 undermines local mode's whole point. #4–6 are quick cleanups.

## Current State
- Modules 1–4 + orchestrator (`main.py`) + a new desktop GUI (`osiris_chat.py`) all functional.
- Tool-calling (Gemini function-calling, real multi-turn loop with `thought_signature` preserved) works in API mode and is auto-offloaded to from local mode for research-flavored prompts (see urgent #2 above for the matching bug).
- `.env`: `LLM_MODE=local` by default, `OLLAMA_MODEL_FAST=smollm2:1.7b-instruct-q4_0`, `OLLAMA_MODEL_SMART=llama3.2:1b`, legacy `OLLAMA_MODEL` still honored if set.
- Mic input stable (sample-rate probing + resample, documented in earlier handoff); `STT_INPUT_DEVICE` left unset.

## Project Roadmap (Remaining Steps)
1. **Step 5 (next, after urgent fixes above):** Wake word (`wake_word.py`) — passive "Hey OSIRIS" detection via `openwakeword`, gating `listen_and_transcribe()`. Needs wiring into *both* `main.py`'s loop and `osiris_chat.py`'s voice mode.
2. **Step 6:** Conversation memory — short-term chat history across turns (currently every turn, in both CLI and GUI, is a stateless single prompt).

## Next Task
Fix the six items above, then build `wake_word.py`.

## Next Prompt to Run
"Following `CODING_RULES.md` and `ARCHITECTURE.md`: first fix the urgent items in HANDOFF.md (decouple brain.py from tools/, tighten the hybrid research-routing keywords, scope close_app to the launched PID, stop swallowing config errors in apps.py, rename stt.py's logger to osiris.stt, move osiris_chat.py's app launch/close onto the worker thread). Then build wake_word.py: passive 'Hey OSIRIS' detection via openwakeword, wired into both main.py's loop and osiris_chat.py's voice mode."