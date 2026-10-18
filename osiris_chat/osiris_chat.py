from __future__ import annotations

import os
from async_runner import CancellableRunner

import asyncio
import html
import json
import re
import sys
import urllib.request
from pathlib import Path
from datetime import datetime 

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_commands import is_exit_command
from chat_widgets import ChatView

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tools.schemas import TOOL_ANNOUNCEMENTS, TOOL_DECLARATIONS, TOOL_DISPATCH
from brain import LLMError, stream_llm_response
from stt import STTError, listen_and_transcribe
from tts import TTSError, speak
from tools.apps import (
    close_app,
    get_allowed_apps_info,
    launch_app,
    remove_allowed_app,
    save_allowed_app,
)

def format_markdown_to_html(raw_text: str) -> str:
    """Format markdown text safely into rich, clickable HTML for QTextBrowser."""
    # Convert markdown links [title](url) to styled anchor tags
    def _link_sub(m: re.Match) -> str:
        text, url = m.group(1), m.group(2)
        return f'<a href="{url}" style="color: #60a5fa; text-decoration: underline; font-weight: 600;">{text}</a>'

    # Convert standalone URLs (not already part of an href)
    def _raw_url_sub(m: re.Match) -> str:
        url = m.group(1)
        return f'<a href="{url}" style="color: #60a5fa; text-decoration: underline;">{url}</a>'

    lines = raw_text.split("\n")
    processed: list[str] = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            processed.append("<hr style='border: 0; border-top: 1px solid rgba(255,255,255,0.1); margin: 6px 0;'/>")
            continue

        if in_code_block:
            escaped = html.escape(line)
            processed.append(f"<div style='font-family: Consolas, monospace; background: rgba(0,0,0,0.3); padding: 2px 8px; border-radius: 4px; color: #a5f3fc;'>{escaped}</div>")
            continue

        l = html.escape(line)
        # Markdown links
        l = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", _link_sub, l)
        # Raw URLs
        l = re.sub(r"(?<!href=\")(?<!\">)(https?://[^\s<]+)", _raw_url_sub, l)
        # Bold
        l = re.sub(r"\*\*([^*]+)\*\*", r"<strong style='color: #ffffff; font-weight: 700;'>\1</strong>", l)
        # Italic
        l = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", l)
        # Inline code
        l = re.sub(r"`([^`]+)`", r"<code style='background: rgba(255,255,255,0.1); padding: 1px 6px; border-radius: 4px; font-family: Consolas, monospace; color: #7dd3fc;'>\1</code>", l)

        # Bullet list item
        if l.strip().startswith("&bull;") or l.strip().startswith("- ") or l.strip().startswith("* "):
            content = re.sub(r"^(?:&bull;|-|\*)\s*", "", l.strip())
            processed.append(f"<div style='margin-left: 14px; margin-top: 3px; margin-bottom: 3px;'>• {content}</div>")
        # Numbered list item
        elif re.match(r"^\d+\.\s+", l.strip()):
            processed.append(f"<div style='margin-left: 10px; margin-top: 4px; margin-bottom: 4px;'>{l.strip()}</div>")
        elif not l.strip():
            processed.append("<div style='height: 6px;'></div>")
        else:
            processed.append(f"<div>{l}</div>")

    return "".join(processed)

async def get_osiris_reply(user_text: str) -> str:
    """Return a completed response from the existing OSIRIS backend."""
    reply_parts: list[str] = []
    async for token in stream_llm_response(
        user_text,
        tools=TOOL_DECLARATIONS,
        tool_dispatch=TOOL_DISPATCH,
        tool_announcements=TOOL_ANNOUNCEMENTS,
    ):
        reply_parts.append(token)
    return "".join(reply_parts).strip() or "I don't have a response right now."


class AssistantWorker(QObject):
    token_ready = Signal(str)
    text_requested = Signal(str)
    voice_requested = Signal()
    status_changed = Signal(str)
    text_reply_ready = Signal(str)
    voice_reply_ready = Signal(str, str)
    error_occurred = Signal(str)
    app_launch_requested = Signal(str)
    app_close_requested = Signal(str)
    app_action_done = Signal(str, str)  # app_name, result message
    models_requested = Signal()
    models_fetched = Signal(list, bool)  # models, ollama_online
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._runner = CancellableRunner()

    def cancel_current(self) -> None:
        """Abort whatever the worker is doing. Call from the GUI thread; not a slot."""
        self._runner.cancel()

    async def _stream_reply(self, text: str) -> str:
        parts: list[str] = []
        async for token in stream_llm_response(
            text,
            tools=TOOL_DECLARATIONS,
            tool_dispatch=TOOL_DISPATCH,
            tool_announcements=TOOL_ANNOUNCEMENTS,
        ):
            parts.append(token)
            self.token_ready.emit(token)
        return "".join(parts).strip()

    @Slot()
    def ask_voice(self) -> None:
        self._runner.begin()
        try:
            self.status_changed.emit("Listening...")
            transcript = self._runner.run(listen_and_transcribe())
            if not transcript:
                self.voice_reply_ready.emit("", "I didn't catch that.")
                return

            if is_exit_command(transcript):
                try:
                    self._runner.run(speak("Goodbye."))
                except TTSError:
                    pass
                self.voice_reply_ready.emit(transcript, "Goodbye.")
                return

            self.status_changed.emit("Processing reply...")
            reply = self._runner.run(get_osiris_reply(transcript))
            self.status_changed.emit("Speaking...")
            try:
                self._runner.run(speak(reply))
            except TTSError:
                pass
            self.voice_reply_ready.emit(transcript, reply)

        except asyncio.CancelledError:
            self.cancelled.emit()
        except (STTError, LLMError, TTSError, Exception) as exc:
            self.error_occurred.emit(f"Voice error: {exc}")
    
    @Slot()
    def fetch_models(self) -> None:
        """Fetch locally available Ollama models off the GUI thread."""
        models: list[dict[str, str]] = []
        ollama_online = False
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode())
                ollama_online = True
                for m in data.get("models", []):
                    details = m.get("details", {})
                    param_size = details.get("parameter_size", "")
                    quant = details.get("quantization_level", "")
                    size_mb = m.get("size", 0) / (1024 * 1024)
                    size_str = f"{size_mb / 1024:.1f} GB" if size_mb > 1000 else f"{size_mb:.0f} MB"
                    sub = f"{param_size} • {quant} • {size_str}".strip(" •")
                    models.append({"name": m.get("name", "unknown"), "info": sub})
        except Exception:
            ollama_online = False
        self.models_fetched.emit(models, ollama_online)
    
    @Slot(str)
    def do_launch_app(self, app_name: str) -> None:
        try:
            asyncio.run(launch_app(app_name))
            self.app_action_done.emit(app_name, f"Launched **{app_name}** successfully.")
        except Exception as exc:
            self.app_action_done.emit(app_name, f"Failed to launch {app_name}: {exc}")

    @Slot(str)
    def do_close_app(self, app_name: str) -> None:
        try:
            msg = asyncio.run(close_app(app_name))
            self.app_action_done.emit(app_name, msg)
        except Exception as exc:
            self.app_action_done.emit(app_name, f"Failed to close {app_name}: {exc}")

    @Slot(str)
    def ask_text(self, text: str) -> None:
        self._runner.begin()
        try:
            self.status_changed.emit("Thinking...")
            reply = self._runner.run(self._stream_reply(text))
            self.text_reply_ready.emit(reply or "I don't have a response right now.")
        except asyncio.CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.error_occurred.emit(f"OSIRIS error: {exc}")

STARTER_PROMPTS = ( #constants
    "What are my system specs?",
    "Search the web for AI news",
    "Open notepad",
)

class OsirisDesktopWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._pending_bubble = None
        self._stream_text = ""
        self.setWindowTitle("OSIRIS")
        self.resize(1260, 820)
        self.setMinimumSize(960, 680)

        app = QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
            app.setPalette(self._build_palette())
            app.setStyleSheet(self._build_stylesheet())

        self.thread = QThread(self)
        self.worker = AssistantWorker()
        self.worker.moveToThread(self.thread)
        self.thread.start()

        self._setup_ui()

        self.worker.status_changed.connect(self.set_status)
        self.worker.text_reply_ready.connect(self._on_text_reply)
        self.worker.voice_reply_ready.connect(self._on_voice_reply)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.text_requested.connect(self.worker.ask_text)
        self.worker.voice_requested.connect(self.worker.ask_voice)
        self.worker.app_action_done.connect(self._on_app_action_done)
        self.worker.app_launch_requested.connect(self.worker.do_launch_app)
        self.worker.app_close_requested.connect(self.worker.do_close_app)
        self.worker.models_fetched.connect(self._on_models_fetched)
        self.worker.models_requested.connect(self.worker.fetch_models)
        self.worker.token_ready.connect(self._on_token)
        self.worker.cancelled.connect(self._on_cancelled)

        self.send_button.clicked.connect(self._on_send_clicked)
        self.text_input.returnPressed.connect(self._send_text_message)
        self.clear_button.clicked.connect(self._clear_chat) 
        self.listen_button.clicked.connect(self._start_voice_turn)

        self.worker.models_requested.emit()
        self.models_poll_timer = QTimer(self)
        self.models_poll_timer.setInterval(5000)
        self.models_poll_timer.timeout.connect(self.worker.models_requested.emit)
        self.models_poll_timer.start()
        self.set_status("Ready")

        self._append_chat("OSIRIS", "Hello! I am OSIRIS. I can research topics, search live web articles, inspect system specs, and assist via voice or text.", "left")

    @staticmethod
    def _build_palette():
        from PySide6.QtGui import QPalette, QColor

        palette = QPalette()
        palette.setColor(QPalette.Window, QColor("#080c14"))
        palette.setColor(QPalette.WindowText, QColor("#e2e8f0"))
        palette.setColor(QPalette.Base, QColor("#0f172a"))
        palette.setColor(QPalette.AlternateBase, QColor("#1e293b"))
        palette.setColor(QPalette.ToolTipBase, QColor("#1e293b"))
        palette.setColor(QPalette.ToolTipText, QColor("#f8fafc"))
        palette.setColor(QPalette.Text, QColor("#f8fafc"))
        palette.setColor(QPalette.Button, QColor("#1e293b"))
        palette.setColor(QPalette.ButtonText, QColor("#f8fafc"))
        palette.setColor(QPalette.Highlight, QColor("#3b82f6"))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.Link, QColor("#60a5fa"))
        palette.setColor(QPalette.LinkVisited, QColor("#a78bfa"))
        return palette

    @staticmethod
    def _build_stylesheet() -> str:
        return """
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #070a12, stop:0.5 #0c1222, stop:1 #060911);
                color: #f1f5f9;
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
            }
            QWidget {
                color: #f1f5f9;
            }
            #appContainer {
                background: transparent;
                padding: 10px;
            }
            /* Header */
            #headerCard {
                background: rgba(15, 23, 42, 0.75);
                border: 1px solid rgba(59, 130, 246, 0.22);
                border-radius: 16px;
                padding: 10px 20px;
            }
            #logoText {
                font-size: 24px;
                font-weight: 800;
                letter-spacing: 2px;
                color: #ffffff;
            }
            #tagline {
                font-size: 11px;
                font-weight: 500;
                color: #64748b;
                letter-spacing: 0.5px;
            }
            #statusIndicator {
                background: rgba(16, 185, 129, 0.12);
                border: 1px solid rgba(16, 185, 129, 0.4);
                border-radius: 20px;
                padding: 6px 14px;
                color: #34d399;
                font-weight: 600;
                font-size: 12px;
            }
            /* Sidebar navigation */
            #sidebar {
                background: rgba(15, 23, 42, 0.7);
                border: 1px solid rgba(59, 130, 246, 0.18);
                border-radius: 16px;
                padding: 12px;
                min-width: 200px;
                max-width: 220px;
            }
            #sidebarTitle {
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1.2px;
                color: #475569;
                padding: 6px 8px;
                text-transform: uppercase;
            }
            .NavBtn {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 10px 14px;
                text-align: left;
                font-size: 13px;
                font-weight: 600;
                color: #94a3b8;
            }
            .NavBtn:hover {
                background: rgba(59, 130, 246, 0.1);
                color: #cbd5e1;
            }
            .NavBtn[active="true"] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(59, 130, 246, 0.28), stop:1 rgba(37, 99, 235, 0.12));
                border: 1px solid rgba(96, 165, 250, 0.4);
                color: #60a5fa;
                font-weight: 700;
            }
            #chipBox {
                background: rgba(30, 41, 59, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 12px;
                padding: 10px;
            }
            .PromptChip {
                background: rgba(30, 41, 59, 0.9);
                border: 1px solid rgba(148, 163, 184, 0.15);
                border-radius: 8px;
                padding: 7px 10px;
                color: #cbd5e1;
                font-size: 11px;
                text-align: left;
            }
            .PromptChip:hover {
                background: rgba(59, 130, 246, 0.2);
                border: 1px solid rgba(96, 165, 250, 0.4);
                color: #ffffff;
            }
            /* Right panel: System Models & Allowed Apps */
            #rightPanel {
                background: rgba(15, 23, 42, 0.7);
                border: 1px solid rgba(59, 130, 246, 0.18);
                border-radius: 16px;
                padding: 12px;
                min-width: 250px;
                max-width: 270px;
            }
            .CardBox {
                background: rgba(11, 18, 33, 0.7);
                border: 1px solid rgba(59, 130, 246, 0.15);
                border-radius: 12px;
                padding: 10px;
            }
            .ModelItem {
                background: rgba(30, 41, 59, 0.6);
                border: 1px solid rgba(148, 163, 184, 0.12);
                border-radius: 8px;
                padding: 7px 10px;
                margin-bottom: 6px;
            }
            .ModelName {
                font-size: 12px;
                font-weight: 700;
                color: #e2e8f0;
            }
            .ModelDetail {
                font-size: 10.5px;
                color: #64748b;
                margin-top: 2px;
            }
            .AppItem {
                background: rgba(30, 41, 59, 0.6);
                border: 1px solid rgba(148, 163, 184, 0.12);
                border-radius: 8px;
                padding: 6px 8px;
                margin-bottom: 6px;
            }
            .AppName {
                font-size: 12px;
                font-weight: 600;
                color: #cbd5e1;
            }
            .AppActionBtn {
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            .AppOpenBtn {
                background: rgba(16, 185, 129, 0.2);
                border: 1px solid rgba(16, 185, 129, 0.4);
                color: #34d399;
            }
            .AppOpenBtn:hover {
                background: rgba(16, 185, 129, 0.35);
                color: #ffffff;
            }
            .AppCloseBtn {
                background: rgba(239, 68, 68, 0.18);
                border: 1px solid rgba(239, 68, 68, 0.4);
                color: #f87171;
            }
            .AppCloseBtn:hover {
                background: rgba(239, 68, 68, 0.35);
                color: #ffffff;
            }
            .MiniIconBtn {
                background: rgba(30, 41, 59, 0.8);
                border: 1px solid rgba(148, 163, 184, 0.25);
                border-radius: 6px;
                color: #cbd5e1;
                font-size: 13px;
                font-weight: 700;
                padding: 2px 7px;
                min-width: 24px;
            }
            .MiniIconBtn:hover {
                background: rgba(59, 130, 246, 0.3);
                border-color: rgba(96, 165, 250, 0.5);
                color: #ffffff;
            }
            .MiniDeleteBtn {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                color: #94a3b8;
                font-size: 12px;
                padding: 2px 6px;
            }
            .MiniDeleteBtn:hover {
                background: rgba(239, 68, 68, 0.2);
                border: 1px solid rgba(239, 68, 68, 0.4);
                color: #fca5a5;
            }
            .OllamaDownBanner {
                background: rgba(239, 68, 68, 0.12);
                border: 1px solid rgba(239, 68, 68, 0.35);
                border-radius: 8px;
                padding: 10px;
                color: #fca5a5;
                font-size: 11.5px;
                font-weight: 600;
                text-align: center;
            }
            /* Main Content Cards */
            #mainPanel {
                background: rgba(15, 23, 42, 0.65);
                border: 1px solid rgba(59, 130, 246, 0.18);
                border-radius: 16px;
                padding: 16px;
            }
            #chatLog {
                background: rgba(8, 12, 22, 0.85);
                border: 1px solid rgba(59, 130, 246, 0.16);
                border-radius: 14px;
                color: #f1f5f9;
                padding: 14px;
                font-size: 13.5px;
            }
            #chatLog QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            #chatLog QScrollBar::handle:vertical {
                background: rgba(96, 165, 250, 0.35);
                border-radius: 4px;
            }
            #chatLog QScrollBar::handle:vertical:hover {
                background: rgba(96, 165, 250, 0.6);
            }
            #chatContent { background: transparent; }
            #bubble { border-radius: 14px; }
            #bubble[role="user"] { background: rgba(37, 99, 235, 0.25); border: 1px solid rgba(59, 130, 246, 0.45); }
            #bubble[role="assistant"] { background: rgba(15, 23, 42, 0.9); border: 1px solid rgba(71, 85, 105, 0.45); }
            #bubble[role="error"] { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.4); }
            #bubbleBadge { font-size: 11px; font-weight: 800; color: #38bdf8; background: transparent; border: none; }
            #bubbleBadge[role="user"] { color: #93c5fd; }
            #bubbleBadge[role="error"] { color: #f87171; }
            #bubbleBody { font-size: 13.5px; color: #f1f5f9; background: transparent; border: none; }
            /* Input controls */
            #inputContainer {
                background: rgba(15, 23, 42, 0.85);
                border: 1px solid rgba(96, 165, 250, 0.25);
                border-radius: 14px;
                padding: 4px 6px;
            }
            #textInput {
                background: transparent;
                border: none;
                padding: 10px 14px;
                color: #f8fafc;
                font-size: 14px;
            }
            #textInput:focus {
                outline: none;
            }
            #sendButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2563eb, stop:1 #3b82f6);
                border: none;
                border-radius: 10px;
                padding: 10px 20px;
                color: #ffffff;
                font-weight: 700;
                font-size: 13px;
            }
            #sendButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1d4ed8, stop:1 #2563eb);
            }
            #sendButton:disabled {
                background: #1e293b;
                color: #64748b;
            }
            #clearButton {
                background: transparent;
                border: 1px solid rgba(148, 163, 184, 0.2);
                border-radius: 10px;
                padding: 10px 14px;
                color: #94a3b8;
                font-size: 12px;
                font-weight: 600;
            }
            #clearButton:hover {
                background: rgba(239, 68, 68, 0.15);
                border: 1px solid rgba(239, 68, 68, 0.4);
                color: #fca5a5;
            }
            /* Voice View */
            #voiceCard {
                background: rgba(11, 17, 32, 0.7);
                border: 1px solid rgba(59, 130, 246, 0.18);
                border-radius: 16px;
                padding: 24px;
            }
            #voiceOutput {
                background: rgba(8, 12, 22, 0.85);
                border: 1px solid rgba(59, 130, 246, 0.16);
                border-radius: 14px;
                color: #f1f5f9;
                padding: 14px;
                font-size: 14px;
            }
            #voiceButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1d4ed8, stop:0.5 #2563eb, stop:1 #3b82f6);
                border: 1px solid rgba(147, 197, 253, 0.4);
                border-radius: 24px;
                padding: 16px 36px;
                font-size: 16px;
                font-weight: 700;
                color: #ffffff;
            }
            #voiceButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #2563eb, stop:0.5 #3b82f6, stop:1 #60a5fa);
            }
            #voiceButton:disabled {
                background: #1e293b;
                color: #64748b;
                border-color: transparent;
            }
            .SectionHeader {
                font-size: 14px;
                font-weight: 700;
                color: #93c5fd;
                margin-bottom: 6px;
            }
        """

    def _setup_ui(self) -> None:
        container = QWidget(objectName="appContainer")
        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(12)

        # Top Header Bar
        header = QFrame(objectName="headerCard")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 8, 16, 8)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        brand = QLabel("OSIRIS", objectName="logoText")
        tagline = QLabel("INTELLIGENT RESEARCH & VOICE ASSISTANT", objectName="tagline")
        title_col.addWidget(brand)
        title_col.addWidget(tagline)
        h_layout.addLayout(title_col)

        h_layout.addStretch()

        self.status_label = QLabel("● Ready", objectName="statusIndicator")
        h_layout.addWidget(self.status_label)
        root_layout.addWidget(header)

        # Main Body Splitter / Side-by-side
        body_layout = QHBoxLayout()
        body_layout.setSpacing(12)

        # Left Sidebar navigation
        sidebar = QFrame(objectName="sidebar")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(8, 10, 8, 10)
        sb_layout.setSpacing(8)

        nav_title = QLabel("WORKSPACE", objectName="sidebarTitle")
        sb_layout.addWidget(nav_title)

        self.btn_nav_chat = QPushButton("💬 Quick Chat")
        self.btn_nav_chat.setProperty("class", "NavBtn")
        self.btn_nav_chat.setProperty("active", "true")
        self.btn_nav_chat.clicked.connect(lambda: self._switch_view(0))
        sb_layout.addWidget(self.btn_nav_chat)

        self.btn_nav_voice = QPushButton("🎙️ Voice Assistant")
        self.btn_nav_voice.setProperty("class", "NavBtn")
        self.btn_nav_voice.setProperty("active", "false")
        self.btn_nav_voice.clicked.connect(lambda: self._switch_view(1))
        sb_layout.addWidget(self.btn_nav_voice)

        sb_layout.addStretch()
        body_layout.addWidget(sidebar)

        # Stacked Views (0: Chat view, 1: Voice view)
        self.stack = QStackedWidget()

        # VIEW 0: Quick Chat
        chat_view = QFrame(objectName="mainPanel")
        cv_layout = QVBoxLayout(chat_view)
        cv_layout.setContentsMargins(8, 8, 8, 8)
        cv_layout.setSpacing(10)

        self.chat_log = ChatView()
        cv_layout.addWidget(self.chat_log)

        self.chips_box = QFrame(objectName="chipBox")
        chips_layout = QHBoxLayout(self.chips_box)
        chips_layout.setContentsMargins(8, 8, 8, 8)
        chips_layout.setSpacing(8)
        for prompt in STARTER_PROMPTS:
            chip = QPushButton(prompt)
            chip.setProperty("class", "PromptChip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda checked=False, p=prompt: self._use_starter(p))
            chips_layout.addWidget(chip)
        chips_layout.addStretch()
        cv_layout.addWidget(self.chips_box)

        # Input Row
        input_card = QFrame(objectName="inputContainer")
        ic_layout = QHBoxLayout(input_card)
        ic_layout.setContentsMargins(6, 4, 6, 4)
        ic_layout.setSpacing(8)

        self.text_input = QLineEdit(objectName="textInput")
        self.text_input.setPlaceholderText("Ask a question, request web research, or type a command...")
        ic_layout.addWidget(self.text_input)

        self.clear_button = QPushButton("Clear", objectName="clearButton")
        ic_layout.addWidget(self.clear_button)

        self.send_button = QPushButton("Send", objectName="sendButton")
        ic_layout.addWidget(self.send_button)

        cv_layout.addWidget(input_card)
        self.stack.addWidget(chat_view)

        # VIEW 1: Voice Mode
        voice_view = QFrame(objectName="voiceCard")
        vv_layout = QVBoxLayout(voice_view)
        vv_layout.setContentsMargins(16, 16, 16, 16)
        vv_layout.setSpacing(14)

        t_label = QLabel("Spoken Transcript", objectName="SectionHeader")
        vv_layout.addWidget(t_label)
        self.voice_transcript = QTextEdit(objectName="voiceOutput")
        self.voice_transcript.setReadOnly(True)
        self.voice_transcript.setPlaceholderText("Your voice input will be transcribed here in real-time...")
        self.voice_transcript.setMaximumHeight(140)
        vv_layout.addWidget(self.voice_transcript)

        r_label = QLabel("OSIRIS Verbal Response", objectName="SectionHeader")
        vv_layout.addWidget(r_label)
        self.voice_reply = QTextBrowser(objectName="voiceOutput")
        self.voice_reply.setReadOnly(True)
        self.voice_reply.setOpenExternalLinks(True)
        self.voice_reply.anchorClicked.connect(self._handle_link_click)
        self.voice_reply.setPlaceholderText("OSIRIS's spoken response will appear here...")
        vv_layout.addWidget(self.voice_reply)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.listen_button = QPushButton("🎙️ Push to Talk / Listen", objectName="voiceButton")
        self.listen_button.setMinimumHeight(60)
        btn_row.addWidget(self.listen_button)
        btn_row.addStretch()
        vv_layout.addLayout(btn_row)

        self.stack.addWidget(voice_view)
        body_layout.addWidget(self.stack, stretch=1)

        # Right Panel: Models in Machine & Allowed Apps
        right_panel = QFrame(objectName="rightPanel")
        rp_layout = QVBoxLayout(right_panel)
        rp_layout.setContentsMargins(10, 10, 10, 10)
        rp_layout.setSpacing(12)

        # Top block: Models in machine
        models_header = QLabel("MACHINE MODELS", objectName="sidebarTitle")
        rp_layout.addWidget(models_header)

        models_box = QFrame()
        models_box.setProperty("class", "CardBox")
        self.models_box_layout = QVBoxLayout(models_box)
        self.models_box_layout.setContentsMargins(6, 6, 6, 6)
        self.models_box_layout.setSpacing(6)
        self._render_models_panel([], True)
        rp_layout.addWidget(models_box)

        # Bottom block: Allowed Apps
        apps_header_row = QHBoxLayout()
        apps_header_row.setContentsMargins(0, 0, 0, 0)
        apps_header = QLabel("ALLOWED APPS", objectName="sidebarTitle")
        apps_header_row.addWidget(apps_header)
        apps_header_row.addStretch()

        self.btn_add_app = QPushButton("+")
        self.btn_add_app.setProperty("class", "MiniIconBtn")
        self.btn_add_app.setToolTip("Add an executable (.exe) to allowlist")
        self.btn_add_app.clicked.connect(self._prompt_add_app)
        apps_header_row.addWidget(self.btn_add_app)

        self.btn_remove_app = QPushButton("-")
        self.btn_remove_app.setProperty("class", "MiniIconBtn")
        self.btn_remove_app.setToolTip("Remove an app from allowlist")
        self.btn_remove_app.clicked.connect(self._prompt_remove_app)
        apps_header_row.addWidget(self.btn_remove_app)

        rp_layout.addLayout(apps_header_row)

        apps_box = QFrame()
        apps_box.setProperty("class", "CardBox")
        self.apps_box_layout = QVBoxLayout(apps_box)
        self.apps_box_layout.setContentsMargins(6, 6, 6, 6)
        self.apps_box_layout.setSpacing(6)
        self._populate_apps_panel()
        rp_layout.addWidget(apps_box)

        rp_layout.addStretch()
        body_layout.addWidget(right_panel)

        root_layout.addLayout(body_layout, stretch=1)
        self.setCentralWidget(container)

    def _switch_view(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.btn_nav_chat.setProperty("active", "true" if index == 0 else "false")
        self.btn_nav_voice.setProperty("active", "true" if index == 1 else "false")
        self.btn_nav_chat.style().unpolish(self.btn_nav_chat)
        self.btn_nav_chat.style().polish(self.btn_nav_chat)
        self.btn_nav_voice.style().unpolish(self.btn_nav_voice)
        self.btn_nav_voice.style().polish(self.btn_nav_voice)

    def _render_models_panel(self, models: list[dict[str, str]], ollama_online: bool) -> None:
        """Render the models panel from already-fetched data — no network calls here."""
        # Clear existing items if any
        while self.models_box_layout.count():
            item = self.models_box_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not ollama_online:
            down_banner = QLabel("⚠️ Ollama is down\nLocal models unreachable")
            down_banner.setProperty("class", "OllamaDownBanner")
            down_banner.setAlignment(Qt.AlignCenter)
            self.models_box_layout.addWidget(down_banner)
            return

        for item in models:
            m_card = QFrame()
            m_card.setProperty("class", "ModelItem")
            m_layout = QVBoxLayout(m_card)
            m_layout.setContentsMargins(6, 4, 6, 4)
            m_layout.setSpacing(2)

            name_lbl = QLabel(f"🤖 {item['name']}")
            name_lbl.setProperty("class", "ModelName")
            m_layout.addWidget(name_lbl)

            info_lbl = QLabel(item["info"])
            info_lbl.setProperty("class", "ModelDetail")
            m_layout.addWidget(info_lbl)

            self.models_box_layout.addWidget(m_card)

    def _populate_apps_panel(self) -> None:
        """Display allowlisted apps with quick Open control and target binary path."""
        # Clear existing items if any
        while self.apps_box_layout.count():
            item = self.apps_box_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        apps = get_allowed_apps_info()
        for name, target in sorted(apps.items()):
            a_card = QFrame()
            a_card.setProperty("class", "AppItem")
            a_layout = QHBoxLayout(a_card)
            a_layout.setContentsMargins(6, 4, 6, 4)
            a_layout.setSpacing(6)

            title_col = QVBoxLayout()
            title_col.setSpacing(1)
            name_lbl = QLabel(f"⚡ {name.title()}")
            name_lbl.setProperty("class", "AppName")
            title_col.addWidget(name_lbl)

            target_lbl = QLabel(target)
            target_lbl.setToolTip(target)
            target_lbl.setText(target_lbl.fontMetrics().elidedText(target, Qt.ElideMiddle, 110))
            target_lbl.setProperty("class", "ModelDetail")
            title_col.addWidget(target_lbl)
            a_layout.addLayout(title_col, stretch=1)

            btn_open = QPushButton("Open")
            btn_open.setProperty("class", "AppActionBtn AppOpenBtn")
            btn_open.clicked.connect(lambda checked=False, app_name=name: self._handle_app_launch(app_name))
            a_layout.addWidget(btn_open)

            btn_close = QPushButton("Close")
            btn_close.setProperty("class", "AppActionBtn AppCloseBtn")
            btn_close.clicked.connect(lambda checked=False, app_name=name: self._handle_app_close(app_name))
            a_layout.addWidget(btn_close)

            self.apps_box_layout.addWidget(a_card)

    def _prompt_add_app(self) -> None:
        """Open file dialog to pick an executable and add it to allowlist."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Application Executable",
            "C:\\",
            "Executables (*.exe);;All Files (*.*)",
        )
        if not file_path:
            return

        exe_path = Path(file_path)
        default_name = exe_path.stem.lower()
        app_name, ok = QInputDialog.getText(
            self,
            "Application Alias",
            f"Enter name/alias for {exe_path.name}:",
            text=default_name,
        )
        if not ok or not app_name.strip():
            return

        name_clean = app_name.strip().lower()
        try:
            save_allowed_app(name_clean, str(exe_path))
            self._populate_apps_panel()
            self._append_chat("OSIRIS", f"Added **{name_clean}** (`{exe_path}`) to the allowed apps list.", "left")
        except Exception as exc:
            QMessageBox.warning(self, "Could Not Add App", str(exc))

    def _prompt_remove_app(self) -> None:
        """Prompt user with list of allowed apps to remove one."""
        apps = get_allowed_apps_info()
        if not apps:
            QMessageBox.information(self, "No Apps", "There are no apps in the allowlist to remove.")
            return

        items = sorted(apps.keys())
        choice, ok = QInputDialog.getItem(
            self,
            "Remove Allowed Application",
            "Select app to remove from allowlist:",
            items,
            0,
            False,
        )
        if not ok or not choice:
            return

        removed = remove_allowed_app(choice)
        if removed:
            self._populate_apps_panel()
            self._append_chat("OSIRIS", f"Removed **{choice}** from the allowed apps list.", "left")
        else:
            QMessageBox.warning(self, "Error", f"Could not remove {choice}.")

    def _handle_app_launch(self, app_name: str) -> None:
        self._switch_view(0)
        self._append_chat("You", f"Open {app_name}", "right")
        self.worker.app_launch_requested.emit(app_name)

    def _handle_app_close(self, app_name: str) -> None:
        self._switch_view(0)
        self._append_chat("You", f"Close {app_name}", "right")
        self.worker.app_close_requested.emit(app_name)

    def _clear_chat(self) -> None:
        self.chat_log.clear()
        self._pending_bubble = None
        self._append_chat("OSIRIS", "Chat history cleared. How may I assist you now?", "left")
        self.chips_box.show()

    def _handle_link_click(self, url: QUrl) -> None:
        QDesktopServices.openUrl(url)

    def _append_chat(self, speaker: str, message: str, alignment: str) -> None:
        role = "user" if alignment == "right" else "assistant"
        if role == "user":
            self.chips_box.hide()
        self.chat_log.add_message(message, role)

    def set_status(self, message: str) -> None:
        if "Listening" in message:
            self.status_label.setStyleSheet("background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.5); color: #f87171;")
            self.status_label.setText(f"● {message}")
        elif "Thinking" in message or "Researching" in message or "Speaking" in message:
            self.status_label.setStyleSheet("background: rgba(245, 158, 11, 0.15); border: 1px solid rgba(245, 158, 11, 0.5); color: #fbbf24;")
            self.status_label.setText(f"● {message}")
        else:
            self.status_label.setStyleSheet("background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.4); color: #34d399;")
            self.status_label.setText(f"● {message}")

    def _on_send_clicked(self) -> None:
        if self._pending_bubble is not None:
            self.worker.cancel_current()
        else:
            self._send_text_message()

    def _send_text_message(self) -> None:
        if self._pending_bubble is not None: return

        text = self.text_input.text().strip()
        if not text:
            return

        if is_exit_command(text):
            self._append_chat("You", text, "right")
            self._append_chat("OSIRIS", "Goodbye.", "left")
            self.text_input.clear()
            return

        self._append_chat("You", text, "right")
        self.text_input.clear()
        self.send_button.setText("Stop")
        self._stream_text = ""
        self._pending_bubble = self.chat_log.add_message("*Thinking…*", "assistant")
        self.worker.text_requested.emit(text)

    def _start_voice_turn(self) -> None:
        self.listen_button.setEnabled(False)
        self.worker.voice_requested.emit()

    def _use_starter(self, prompt: str) -> None:
        self.text_input.setText(prompt)
        self._send_text_message()
    
    def _on_token(self, token: str) -> None:
        if self._pending_bubble is None:
            return
        self._stream_text += token
        self.chat_log.update_bubble(self._pending_bubble, self._stream_text)

    def _on_text_reply(self, reply: str) -> None:
        if self._pending_bubble is not None:
            self.chat_log.update_bubble(self._pending_bubble, reply)
            self._pending_bubble = None
        else:
            self._append_chat("OSIRIS", reply, "left")
        self.send_button.setEnabled(True)
        self.send_button.setText("Send")
        self.set_status("Ready")

    def _on_voice_reply(self, transcript: str, reply: str) -> None:
        if transcript:
            self.voice_transcript.setPlainText(transcript)
        if reply:
            self.voice_reply.setMarkdown(reply)
        self.listen_button.setEnabled(True)
        self.set_status("Ready")

    def _on_error(self, message: str) -> None:
        self.set_status("Error")
        self.send_button.setEnabled(True)
        self.send_button.setText("Send")
        self.listen_button.setEnabled(True)
        if self.stack.currentIndex() == 1:  # voice view has no chat log
            self.voice_reply.setPlainText(message)
        elif self._pending_bubble is not None:
            self.chat_log.update_bubble(self._pending_bubble, message, role="error")
        else:
            self.chat_log.add_message(message, "error")
        self._pending_bubble = None
    
    def _on_app_action_done(self, app_name: str, message: str) -> None:
        self._append_chat("OSIRIS", message, "left")

    def _on_models_fetched(self, models: list, ollama_online: bool) -> None:
        self._render_models_panel(models, ollama_online)
    
    def _on_cancelled(self) -> None:
        if self._pending_bubble is not None:
            partial = self._stream_text.strip()
            self.chat_log.update_bubble(
                self._pending_bubble, f"{partial}\n\n*(stopped)*" if partial else "*Stopped.*"
            )
            self._pending_bubble = None
        self.send_button.setEnabled(True)
        self.send_button.setText("Send")
        self.listen_button.setEnabled(True)
        self.set_status("Ready")

    def closeEvent(self, event) -> None:
        self.models_poll_timer.stop()
        self.worker.cancel_current()
        self.hide()
        self.thread.quit()
        if not self.thread.wait(1500):
            os._exit(0)
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("OSIRIS")
    app.setOrganizationName("OSIRIS")
    window = OsirisDesktopWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
