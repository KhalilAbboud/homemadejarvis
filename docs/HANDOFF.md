# Session Handoff State

## Last Completed Task
- Module 2 (`brain.py`): dual-mode LLM router (`local` via Ollama, `api` via Gemini `google-genai` SDK), async streaming, error handling. **Local mode now auto-routes between two Ollama models** based on prompt complexity — `smollm2:1.7b-instruct-q4_0` (fast, for simple prompts) and `qwen3.5:4b` (smart, for reasoning/complex requests). A scoring heuristic in `_pick_local_model()` evaluates word count, complexity keywords, and sentence count. The legacy `OLLAMA_MODEL` env-var overrides auto-routing if set.
- Module 1 (`stt.py`): `faster-whisper` + `sounddevice`. Single continuously-open `InputStream`, RMS-based silence trimming, configurable device/thresholds via env. Lazy-loaded model singleton, `warm_up()` for early load.
- Module 3 (`tts.py`): multi-engine (`piper` default, `edge`, `pyttsx3`), sentence-chunked synthesis, SHA256-keyed audio cache, Windows-specific playback path (`winsound`) with `playsound` fallback, leading-silence padding for Windows device wake-up.
- Module 4 (`tools/`): Safe, focused submodules — `apps.py` (allowlisted app launching), `system_info.py` (read-only specs), `web.py` (search + page fetch with SSRF protection), `planner.py` (elevated-reasoning project plans). Wired into Gemini function-calling via `schemas.py` (declarations, dispatch, spoken pre-announcements). No arbitrary shell-command capability.
- Orchestrator (`main.py`): wires STT → streamed brain output → TTS, speaking completed sentences while the rest of the reply still streams (producer/consumer queue). Routes project-planning requests to `_stream_plan_to_console()` with higher thinking level. Exit-phrase detection, per-stage error isolation (STT/LLM/TTS failures don't crash the loop).
- Diagnostics: `test_mic.py` (bare `sd.rec` capture check) and `test_chunks.py` (per-chunk RMS to inspect mic startup latency) — used to debug mic/device issues, not part of the runtime pipeline.

## Current State
- Modules 1–4 and the orchestrator are built, decoupled (no cross-imports between stt/tts/brain/tools), and async throughout per project coding rules.
- End-to-end voice loop (listen → think → speak) is functional with tool-calling support in API mode.
- Local mode uses two-tier model routing: simple prompts → `smollm2` (fast), complex prompts → `qwen3.5` (smart). No tool-calling in local mode (logged warning, graceful fallback).
- `.env` defaults to `LLM_MODE=local` with `OLLAMA_MODEL_FAST` and `OLLAMA_MODEL_SMART` configured.

## Environment Variables (brain-related)
| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_MODE` | `local` | `local` (Ollama) or `api` (Gemini) |
| `OLLAMA_MODEL_FAST` | `smollm2:1.7b-instruct-q4_0` | Quick-response model for simple prompts |
| `OLLAMA_MODEL_SMART` | `qwen3.5:4b` | Capable model for complex/reasoning prompts |
| `OLLAMA_MODEL` | _(empty)_ | Legacy override — if set, bypasses auto-routing |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Cloud model for API mode |

## Project Roadmap (Remaining Steps)
1. **Step 5:** Add wake word (`wake_word.py`) — passive "Hey OSIRIS" detection via `openwakeword`, gating `listen_and_transcribe()`.
2. **Step 6:** Conversation memory — short-term chat history across turns in a voice session (currently each turn is a stateless single prompt).

## Next Task
Add wake-word detection so OSIRIS listens passively until activated.

## Next Prompt to Run
"Build Module 5: wake-word detection using openwakeword. Add a wake_word.py module that passively listens for 'Hey OSIRIS' and only then gates into listen_and_transcribe(). Wire it into main.py's run loop."
