# OSIRIS - System Architecture

## Project Goal
A modular, local/cloud hybrid AI assistant inspired by OSIRIS capable of voice interaction and computer controls.

## Execution Modes (`LLM_MODE`)
The system dynamically switches execution providers via a single environment variable (`LLM_MODE`):
- **LOCAL Mode (`LLM_MODE=local`):** Routes requests to an on-device Ollama server. Uses two-tier auto-routing: simple prompts go to `smollm2:1.7b-instruct-q4_0` (fast, low-latency), complex prompts go to `qwen3.5:4b` (capable reasoning). Model selection is handled by `_pick_local_model()` in `brain.py` — a scoring heuristic based on word count, complexity keywords, and sentence count. Fully offline, zero API cost.
- **API Mode (`LLM_MODE=api`):** Routes requests to Google Gemini (`gemini-3.6-flash`) via the `google-genai` SDK. Supports function-calling for tool use (Module 4). Configurable thinking level for reasoning depth.

## Module Breakdown
1. **Module 1 (Voice In):** STT (Speech-to-Text) using `faster-whisper`.
2. **Module 2 (Brain / Router):**
   - **Provider Factory:** `stream_llm_response()` reads `LLM_MODE` and dispatches to the appropriate provider.
   - **Local Engine:** HTTP client connecting to `http://localhost:11434/api/generate` (Ollama) with auto-routing between fast/smart models.
   - **API Engine:** `google-genai` SDK client with streaming, function-calling, and thought-signature preservation.
3. **Module 3 (Voice Out):** TTS (Text-to-Speech) using Piper (default), Edge TTS, or pyttsx3.
4. **Module 4 (Tools):** Safe, focused submodules for allowlisted app launching,
   read-only system specifications, public-web search/page retrieval, and detailed
   project planning. Wired into Gemini function-calling via `tools/schemas.py`.
   It intentionally has no arbitrary shell-command capability.

## Active Focus
Wake-word detection (Module 5) and conversation memory (Module 6).
