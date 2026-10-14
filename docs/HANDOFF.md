# Session Handoff State — OSIRIS (v0.1 + GUI remodel in progress)

## Status
- Modules 1–4 (STT, brain/router, TTS, tools), the CLI orchestrator (`main.py`) and the PySide6 desktop GUI (`osiris_chat/`) all work.
- All six urgent code-review items are fixed: brain/tools decoupling, research-router keyword overmatch, `close_app` PID scoping, silent config swallowing in `apps.py`, `stt.py` logger rename, GUI app launch/close on the worker thread with shared `is_exit_command`.
- Module 5 (wake word) has **not** started. A GUI remodel comes first (plan below).

## Done since the v0.1 checkpoint
- **GUI models panel:** Ollama fetch moved off the GUI thread (`fetch_models` slot + `models_fetched` signal), polled by a `QTimer` every 5s, so starting Ollama after the app updates the panel.
- **Phase A, chat rendering:** per-message bubble widgets replace the `QTextBrowser` HTML bubbles (Qt rich text ignored flex, border-radius and box-shadow). New `osiris_chat/chat_widgets.py` holds `ChatBubble` and `ChatView` (`add_message`, `update_bubble`, `clear`). Assistant text renders as markdown via `QLabel`; user and error text stay literal. Link colours come from `QPalette.Link`.
- **Streaming + thinking + errors:** the worker emits `token_ready`; the window grows a pending bubble ("*Thinking…*") as tokens arrive. Errors turn the pending bubble red instead of opening a `QMessageBox`.
- **Cancellable worker:** new `osiris_chat/async_runner.py` (`CancellableRunner`) replaces `asyncio.run` in `ask_text`/`ask_voice`. `worker.cancel_current()` is callable from the GUI thread. `closeEvent` cancels, waits 1.5s, then `os._exit(0)` (mic recording and TTS playback run in OS threads that can't be interrupted yet). Optional Send/Stop button wired to the same cancel.
- **Bug fix in `brain.py`:** tool announcements are yielded with a trailing space. Without it the CLI sentence splitter held "Opening notepad." until the end of the reply, so it was spoken after the tool finished.
- **Local models changed:** user now runs `phi4-mini` and `qwen3.5:4b` (llama3.2:1b and smollm2 removed). `OLLAMA_MODEL_FAST` / `OLLAMA_MODEL_SMART` in `.env` must match `ollama list`, or `OLLAMA_MODEL` forces one model.

## Repo layout (relevant parts)
```
src/        brain.py main.py stt.py tts.py voice_commands.py tools/{apps,planner,schemas,system_info,web}.py
osiris_chat/ osiris_chat.py chat_widgets.py async_runner.py
allowed_apps.json  .env  docs/{ARCHITECTURE,CODING_RULES,HANDOFF}.md
```

## Remodel plan (pre-Module 5)
- **A. Chat rendering** — done, user is verifying. Check: bubbles + right-aligned user messages, streaming, error bubble with Ollama stopped, instant close mid-reply, Stop button, announcement spacing.
- **B. Usability** — next: (B1) Close button next to each Open; (B2) starter-prompt chips in the empty chat, hidden after first message; (B3) shortcuts Ctrl+L clear, Ctrl+K focus input, Esc clear input; (B4) models-panel refresh button + "checked HH:MM", skip the 5s poll while a fetch or chat is running.
- **C. Aesthetics** — system card to fill the right panel (LLM mode, TTS engine, active models); window icon + header logo; SVG icons in `assets/` instead of emoji; 8px spacing grid and scrollbar restyle.
- **D. Repo polish** — README, `requirements.txt`, `.env.example` (replacing `fakeenv.txt`), delete `garbage info/`, gitignore `.kilo/` and `.sixth/`, pytest tests for `is_exit_command`, `_is_research_request`, `_pick_local_model`.
- **Deferred to Module 5:** tray icon + hide-to-tray, voice cancel button + mic level meter (need a stop event in `stt.py`), settings view (modules read `.env` at import), shared stream-to-speech helper for GUI and CLI.

## Known issues, not blocking
- Sentence regex in `main.py` splits on `Dr.`, `v1.0`, `$4.50`.
- TTS cache in the temp folder is never pruned.
- `main.py` does not await STT/TTS warm-up before the first listen.
- GUI voice path is not streamed (full reply, then `speak`); text path streams.
- Closing during mic recording or TTS playback still hits the `os._exit` fallback.
- DuckDuckGo HTML scraping in `web.py` can break if their markup changes.

## Next Task
Finish verifying Phase A, then build Phase B (B1–B4), then C and D, then start Module 5.

## Next Prompt to Run
"Following CODING_RULES.md, Phase A of the osiris_chat remodel is verified. Do Phase B from HANDOFF.md (B1 Close buttons, B2 starter chips, B3 shortcuts, B4 models refresh). Point out the edits instead of rewriting whole files."