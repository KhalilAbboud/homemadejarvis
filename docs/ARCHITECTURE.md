# OSIRIS - System Architecture

## Project Goal
A modular, local/cloud hybrid AI assistant inspired by OSIRIS capable of voice interaction and computer controls.

## Execution Modes (`LLM_MODE`)
The system dynamically switches execution providers via a single environment variable (`LLM_MODE`):
- **LOCAL Mode (`LLM_MODE=local`):** Routes requests to an on-device server (Ollama / `llama.cpp`) running lightweight 1.7B–3B parameter models (e.g., `Qwen3:1.7B` or `Llama-3.2-3B`). Zero latency costs, fully offline.
- **API Mode (`LLM_MODE=api`):** Routes requests to cloud providers (Anthropic Claude 3.5 Sonnet, DeepSeek, or OpenAI) for complex reasoning and tool execution.

## Module Breakdown
1. **Module 1 (Voice In):** STT (Speech-to-Text) using `faster-whisper`.
2. **Module 2 (Brain / Router):**
   - **Provider Factory:** A single wrapper function (`get_llm_response(prompt: str)`) that reads `LLM_MODE`.
   - **Local Engine:** HTTP client connecting to `http://localhost:11434/api/generate` (Ollama).
   - **API Engine:** Official SDK client (e.g., `anthropic` or `openai` Python package).
3. **Module 3 (Voice Out):** TTS (Text-to-Speech) using Kokoro or Piper.
4. **Module 4 (Tools):** System commands, app launcher, web scraper.

## Active Focus
Building Module 2 (Brain) with dual-mode support (`local` vs `api`).
