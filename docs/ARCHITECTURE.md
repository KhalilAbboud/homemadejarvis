# OSIRIS - System Architecture

## Project Goal
A modular, local/cloud hybrid AI assistant inspired by OSIRIS capable of voice interaction and computer controls.

## Execution Modes (`LLM_MODE`)
The system dynamically switches execution providers via environment variables and intent heuristics:
- **LOCAL Mode (`LLM_MODE=local`):** Routes requests to on-device Ollama models:
  - **Fast Chitchat / Short Utterances:** Handled locally by `smollm2:1.7b-instruct-q4_0` (<= 5 words or low complexity).
  - **General Complex / Code / Reasoning:** Handled locally by `llama3.2:1b` (fast on CPU, low memory footprint).
  - **Hybrid Research Offload:** Queries containing in-depth research, reports, or scientific study keywords are automatically routed to the **Gemini Cloud API** (`gemini-3.6-flash`), preventing CPU/RAM lockups while granting access to search tools and detailed reports.
- **API Mode (`LLM_MODE=api`):** Routes all requests directly to Google Gemini (`gemini-3.6-flash`) via the `google-genai` SDK. Supports function-calling for tool use (Module 4) and configurable thinking levels.

## Module Breakdown
1. **Module 1 (Voice In):** STT (Speech-to-Text) using `faster-whisper`.
2. **Module 2 (Brain / Router):**
   - **Provider Factory:** `stream_llm_response()` reads `LLM_MODE`, handles hybrid research offload, and dispatches to local or API streams.
   - **Local Engine:** HTTP client connecting to `http://localhost:11434/api/generate` (Ollama) routing between `smollm2:1.7b` and `llama3.2:1b`.
   - **API Engine:** `google-genai` SDK client with streaming, function-calling, and thought-signature preservation.
3. **Module 3 (Voice Out):** TTS (Text-to-Speech) using Piper (default), Edge TTS, or pyttsx3.
4. **Module 4 (Tools):** Safe, focused submodules for allowlisted app launching,
   read-only system specifications, public-web search/page retrieval, and detailed
   project planning. Wired into Gemini function-calling via `tools/schemas.py`.
   It intentionally has no arbitrary shell-command capability.

## Active Focus
Wake-word detection (Module 5) and conversation memory (Module 6).
