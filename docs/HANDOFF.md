# Session Handoff State

## Last Completed Task
- Successfully built and verified Module 2 (`src/brain.py`).
- Resolved Gemini API model endpoint error and confirmed working dual-mode execution (`api` mode via Gemini and `local` mode via Ollama `qwen2.5-coder:1.5b`).

## Current State
- `src/brain.py` is fully functional, async-compatible, and tested.
- `.env` is configured with `LLM_MODE` and `GEMINI_API_KEY`.

## Project Roadmap (Next 6 Steps)
1. **Step 1 (Immediate):** Build Module 1 (`src/stt.py`) — Speech-to-Text using `faster-whisper` to record and transcribe microphone input.
2. **Step 2:** Build Module 3 (`src/tts.py`) — Text-to-Speech using `edge-tts` or `pyttsx3` to give JARVIS a voice.
3. **Step 3:** Build Orchestrator (`src/main.py`) — Wire STT -> Brain -> TTS together into a basic continuous voice conversation loop.
4. **Step 4:** Build Module 4 (`src/tools.py`) — Add tool execution / system functions (opening apps, running terminal commands, checking system specs).
5. **Step 5:** Add Wake Word (`src/wake_word.py`) — Implement passive "Hey JARVIS" wake word detection using `openwakeword`.
6. **Step 6:** Conversation Memory — Add short-term chat history management so JARVIS remembers recent turns during a voice session.

## Next Task
Build Module 1 (`src/stt.py`) for microphone input and Speech-to-Text transcription.

## Next Prompt to Run
"Read `3_HANDOFF.md` alongside our project rules. Let's write `src/stt.py` for Module 1 (Speech-to-Text). We want an async-friendly module using `faster-whisper` and `sounddevice` (or `pyaudio`) that records audio from the microphone when triggered and returns transcribed text. Keep it lightweight so it runs fast on CPU."