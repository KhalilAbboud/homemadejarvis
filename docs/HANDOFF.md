# Session Handoff State

## Last Completed Task
- Module 2 (`brain.py`): dual-mode LLM router (`local` via Ollama `qwen2.5-coder:1.5b`, `api` via Gemini), async streaming, error handling. **Note:** architecture doc lists Anthropic/DeepSeek/OpenAI for API mode, but the actual API engine implemented is Gemini (`google-genai` SDK) — flagging in case that's intentional drift or needs reconciling.
- Module 1 (`stt.py`): `faster-whisper` + `sounddevice`. Single continuously-open `InputStream`, RMS-based silence trimming, configurable device/thresholds via env. Lazy-loaded model singleton, `warm_up()` for early load.
- Module 3 (`tts.py`): multi-engine (`piper` default, `edge`, `pyttsx3`), sentence-chunked synthesis, SHA256-keyed audio cache, Windows-specific playback path (`winsound`) with `playsound` fallback, leading-silence padding for Windows device wake-up.
- Orchestrator (`main.py`): wires STT → streamed brain output → TTS, speaking completed sentences while the rest of the reply still streams (producer/consumer queue). Exit-phrase detection, per-stage error isolation (STT/LLM/TTS failures don't crash the loop).
- Diagnostics: `test_mic.py` (bare `sd.rec` capture check) and `test_chunks.py` (per-chunk RMS to inspect mic startup latency) — used to debug mic/device issues, not part of the runtime pipeline.
- `.env` (`fakeenv.txt` as template): `LLM_MODE`, `GEMINI_API_KEY`, Whisper/TTS/mic config, includes `STT_INPUT_DEVICE=2` for a Bluetooth earbud mic that required pinning.

## Current State
- Modules 1–3 and the orchestrator are built, decoupled (no cross-imports between stt/tts/brain), and async throughout per project coding rules.
- End-to-end voice loop (listen → think → speak) is functional.

## Project Roadmap (Remaining Steps)
1. **Step 4:** Module 4 is organized as `tools/apps.py`, `tools/system_info.py`, `tools/web.py`, and `tools/planner.py`. It omits arbitrary shell commands. `brain.stream_project_plan()` uses elevated API reasoning for detailed plans.
2. **Step 5:** Add wake word (`wake_word.py`) — passive "Hey OSIRIS" detection via `openwakeword`, gating `listen_and_transcribe()`.
3. **Step 6:** Conversation memory — short-term chat history across turns in a voice session (currently each turn is a stateless single prompt).

## Next Task
Wire Module 4's safe submodules into the provider's function-calling path.

## Next Prompt to Run
"Wire Module 4's safe submodules into Gemini function calling. Preserve the app allowlist, SSRF protections for web tools, and the absence of any shell-command function."
