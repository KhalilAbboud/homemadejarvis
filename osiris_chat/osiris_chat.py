from __future__ import annotations

import asyncio
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from brain import LLMError, stream_llm_response
from stt import STTError, listen_and_transcribe
from tts import TTSError, speak

EXIT_PHRASES = {"exit", "quit", "goodbye", "stop", "bye"}


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
    async for token in stream_llm_response(user_text):
        reply_parts.append(token)
    return "".join(reply_parts).strip() or "I don't have a response right now."


class AssistantWorker(QObject):
    text_requested = Signal(str)
    voice_requested = Signal()
    status_changed = Signal(str)
    text_reply_ready = Signal(str)
    voice_reply_ready = Signal(str, str)
    error_occurred = Signal(str)

    @Slot(str)
    def ask_text(self, text: str) -> None:
        try:
            self.status_changed.emit("Researching & Thinking...")
            reply = asyncio.run(get_osiris_reply(text))
            self.text_reply_ready.emit(reply)
        except Exception as exc:
            self.error_occurred.emit(f"OSIRIS error: {exc}")

    @Slot()
    def ask_voice(self) -> None:
        try:
            self.status_changed.emit("Listening...")
            transcript = asyncio.run(listen_and_transcribe())
            if not transcript:
                self.voice_reply_ready.emit("", "I didn't catch that.")
                return

            normalized = transcript.strip().lower()
            if normalized in EXIT_PHRASES:
                try:
                    asyncio.run(speak("Goodbye."))
                except TTSError:
                    pass
                self.voice_reply_ready.emit(transcript, "Goodbye.")
                return

            self.status_changed.emit("Processing reply...")
            reply = asyncio.run(get_osiris_reply(transcript))
            self.status_changed.emit("Speaking...")
            try:
                asyncio.run(speak(reply))
            except TTSError:
                pass
            self.voice_reply_ready.emit(transcript, reply)
        except (STTError, LLMError, TTSError, Exception) as exc:
            self.error_occurred.emit(f"Voice error: {exc}")


class OsirisDesktopWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("OSIRIS AI Workstation")
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

        self.send_button.clicked.connect(self._send_text_message)
        self.text_input.returnPressed.connect(self._send_text_message)
        self.clear_button.clicked.connect(self._clear_chat)
        self.listen_button.clicked.connect(self._start_voice_turn)

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

        # Sidebar navigation & quick research prompts
        sidebar = QFrame(objectName="sidebar")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(8, 10, 8, 10)
        sb_layout.setSpacing(8)

        nav_title = QLabel("WORKSPACE", objectName="sidebarTitle")
        sb_layout.addWidget(nav_title)

        self.btn_nav_chat = QPushButton("💬 Chat & Research")
        self.btn_nav_chat.setProperty("class", "NavBtn")
        self.btn_nav_chat.setProperty("active", "true")
        self.btn_nav_chat.clicked.connect(lambda: self._switch_view(0))
        sb_layout.addWidget(self.btn_nav_chat)

        self.btn_nav_voice = QPushButton("🎙️ Voice Assistant")
        self.btn_nav_voice.setProperty("class", "NavBtn")
        self.btn_nav_voice.setProperty("active", "false")
        self.btn_nav_voice.clicked.connect(lambda: self._switch_view(1))
        sb_layout.addWidget(self.btn_nav_voice)

        sb_layout.addSpacing(12)
        quick_title = QLabel("QUICK RESEARCH", objectName="sidebarTitle")
        sb_layout.addWidget(quick_title)

        chip_box = QFrame(objectName="chipBox")
        chip_layout = QVBoxLayout(chip_box)
        chip_layout.setContentsMargins(4, 4, 4, 4)
        chip_layout.setSpacing(6)

        prompts = [
            "Fetch 3 articles about liver cancer",
            "Search latest discoveries in quantum computing",
            "What are my system specs?",
            "Open notepad",
        ]
        for p in prompts:
            chip = QPushButton(p)
            chip.setProperty("class", "PromptChip")
            chip.clicked.connect(lambda checked=False, query=p: self._trigger_prompt(query))
            chip_layout.addWidget(chip)

        sb_layout.addWidget(chip_box)
        sb_layout.addStretch()

        body_layout.addWidget(sidebar)

        # Stacked Views (0: Chat/Research view, 1: Voice view)
        self.stack = QStackedWidget()

        # VIEW 0: Chat & Research
        chat_view = QFrame(objectName="mainPanel")
        cv_layout = QVBoxLayout(chat_view)
        cv_layout.setContentsMargins(8, 8, 8, 8)
        cv_layout.setSpacing(10)

        self.chat_log = QTextBrowser(objectName="chatLog")
        self.chat_log.setOpenExternalLinks(True)
        self.chat_log.anchorClicked.connect(self._handle_link_click)
        cv_layout.addWidget(self.chat_log)

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

    def _trigger_prompt(self, query: str) -> None:
        self._switch_view(0)
        self.text_input.setText(query)
        self._send_text_message()

    def _clear_chat(self) -> None:
        self.chat_log.clear()
        self._append_chat("OSIRIS", "Chat history cleared. How may I assist you now?", "left")

    def _handle_link_click(self, url: QUrl) -> None:
        QDesktopServices.openUrl(url)

    def _append_chat(self, speaker: str, message: str, alignment: str) -> None:
        is_user = alignment == "right"
        direction = "flex-end" if is_user else "flex-start"
        bg = "rgba(37, 99, 235, 0.22)" if is_user else "rgba(15, 23, 42, 0.85)"
        border = "rgba(59, 130, 246, 0.45)" if is_user else "rgba(71, 85, 105, 0.4)"
        header_color = "#93c5fd" if is_user else "#38bdf8"
        icon_badge = "👤 You" if is_user else "⚡ OSIRIS"

        formatted_msg = format_markdown_to_html(message)

        bubble = (
            f"<div style='margin: 12px 0; display: flex; justify-content: {direction};'>"
            f"<div style='max-width: 82%; padding: 14px 18px; border-radius: 14px; "
            f"background: {bg}; border: 1px solid {border}; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3);'>"
            f"<div style='font-size: 11px; color: {header_color}; margin-bottom: 6px; font-weight: 800; letter-spacing: 0.5px;'>{icon_badge}</div>"
            f"<div style='color: #f1f5f9; line-height: 1.6; font-size: 13.5px;'>{formatted_msg}</div>"
            "</div></div>"
        )
        self.chat_log.insertHtml(bubble)
        self.chat_log.verticalScrollBar().setValue(self.chat_log.verticalScrollBar().maximum())

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

    def _send_text_message(self) -> None:
        text = self.text_input.text().strip()
        if not text:
            return

        if text.lower() in EXIT_PHRASES:
            self._append_chat("You", text, "right")
            self._append_chat("OSIRIS", "Goodbye.", "left")
            self.text_input.clear()
            return

        self._append_chat("You", text, "right")
        self.text_input.clear()
        self.send_button.setEnabled(False)
        self.worker.text_requested.emit(text)

    def _start_voice_turn(self) -> None:
        self.listen_button.setEnabled(False)
        self.worker.voice_requested.emit()

    def _on_text_reply(self, reply: str) -> None:
        self._append_chat("OSIRIS", reply, "left")
        self.send_button.setEnabled(True)
        self.set_status("Ready")

    def _on_voice_reply(self, transcript: str, reply: str) -> None:
        if transcript:
            self.voice_transcript.setPlainText(transcript)
        if reply:
            formatted = format_markdown_to_html(reply)
            self.voice_reply.setHtml(f"<div style='color: #f1f5f9; line-height: 1.6;'>{formatted}</div>")
        self.listen_button.setEnabled(True)
        self.set_status("Ready")

    def _on_error(self, message: str) -> None:
        self.set_status("Error")
        self.send_button.setEnabled(True)
        self.listen_button.setEnabled(True)
        QMessageBox.critical(self, "OSIRIS Error", message)

    def closeEvent(self, event) -> None:
        self.thread.quit()
        self.thread.wait()
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
