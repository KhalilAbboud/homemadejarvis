from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QTabWidget,
)

from brain import LLMError, stream_llm_response
from stt import STTError, listen_and_transcribe
from tts import TTSError, speak

EXIT_PHRASES = {"exit", "quit", "goodbye", "stop", "bye"}


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
            self.status_changed.emit("Thinking...")
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

            reply = asyncio.run(get_osiris_reply(transcript))
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
        self.setWindowTitle("OSIRIS")
        self.resize(1180, 760)
        self.setMinimumSize(900, 650)

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
        self.listen_button.clicked.connect(self._start_voice_turn)

        self.set_status("Ready")
        self._append_chat("OSIRIS", "Hello. I’m ready to help.", "left")

    @staticmethod
    def _build_palette():
        from PySide6.QtGui import QPalette, QColor

        palette = QPalette()
        palette.setColor(QPalette.Window, QColor("#090d18"))
        palette.setColor(QPalette.WindowText, QColor("#edf3ff"))
        palette.setColor(QPalette.Base, QColor("#101827"))
        palette.setColor(QPalette.AlternateBase, QColor("#0f172a"))
        palette.setColor(QPalette.ToolTipBase, QColor("#111827"))
        palette.setColor(QPalette.ToolTipText, QColor("#edf3ff"))
        palette.setColor(QPalette.Text, QColor("#edf3ff"))
        palette.setColor(QPalette.Button, QColor("#141d31"))
        palette.setColor(QPalette.ButtonText, QColor("#edf3ff"))
        palette.setColor(QPalette.Light, QColor("#1d2a42"))
        palette.setColor(QPalette.Midlight, QColor("#121c2f"))
        palette.setColor(QPalette.Dark, QColor("#070b12"))
        palette.setColor(QPalette.Shadow, QColor("#050912"))
        palette.setColor(QPalette.Highlight, QColor("#7aa2ff"))
        palette.setColor(QPalette.HighlightedText, QColor("#08111d"))
        return palette

    @staticmethod
    def _build_stylesheet() -> str:
        return """
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0b1020, stop:0.5 #0d1322, stop:1 #0a0d17);
                color: #edf3ff;
            }
            QWidget {
                color: #edf3ff;
            }
            #root {
                background: transparent;
                border: 1px solid rgba(164, 177, 255, 0.18);
                border-radius: 22px;
                padding: 12px;
            }
            #topBar {
                background: rgba(14, 20, 34, 0.76);
                border: 1px solid rgba(138, 160, 255, 0.18);
                border-radius: 18px;
                padding: 14px 18px;
                margin-bottom: 10px;
            }
            #brand {
                font-size: 28px;
                font-weight: 700;
                letter-spacing: 1.4px;
                color: #edf3ff;
            }
            #statusBadge {
                background: rgba(74, 144, 226, 0.15);
                border: 1px solid rgba(122, 162, 255, 0.4);
                border-radius: 999px;
                padding: 7px 12px;
                color: #a9c4ff;
                font-weight: 600;
                font-size: 12px;
            }
            QTabWidget::pane {
                border: none;
                background: transparent;
            }
            QTabBar::tab {
                background: #101827;
                color: #c1d4ff;
                border: 1px solid rgba(142, 162, 214, 0.18);
                border-radius: 12px 12px 0 0;
                padding: 10px 18px;
                margin-right: 6px;
            }
            QTabBar::tab:selected {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2b3a5d, stop:1 #1a2746);
                color: #f3f7ff;
                border: 1px solid rgba(122, 162, 255, 0.45);
            }
            #tabPanel {
                background: rgba(12, 17, 28, 0.82);
                border: 1px solid rgba(142, 162, 214, 0.18);
                border-radius: 18px;
                padding: 12px;
            }
            #chatLog {
                background: rgba(7, 12, 19, 0.72);
                border: 1px solid rgba(138, 160, 255, 0.14);
                border-radius: 16px;
                color: #edf3ff;
                padding: 12px;
            }
            #chatLog QScrollBar:vertical {
                background: transparent;
                width: 10px;
            }
            #chatLog QScrollBar::handle:vertical {
                background: rgba(122, 162, 255, 0.45);
                border-radius: 5px;
            }
            #textInput {
                background: rgba(15, 22, 34, 0.9);
                border: 1px solid rgba(142, 162, 214, 0.18);
                border-radius: 14px;
                padding: 12px 16px;
                color: #edf3ff;
                font-size: 14px;
            }
            #primaryButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #7aa2ff, stop:1 #4f87ff);
                border: none;
                border-radius: 14px;
                padding: 12px 16px;
                color: #08111d;
                font-weight: 700;
            }
            #primaryButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #8bb1ff, stop:1 #5f95ff);
            }
            #voiceButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a2949, stop:1 #0f1b31);
                border: 1px solid rgba(122, 162, 255, 0.45);
                border-radius: 36px;
                min-width: 140px;
                min-height: 72px;
                font-size: 18px;
                font-weight: 700;
                color: #edf3ff;
            }
            #voiceButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #213768, stop:1 #152444);
            }
            #voiceOutput {
                background: rgba(7, 12, 19, 0.72);
                border: 1px solid rgba(138, 160, 255, 0.14);
                border-radius: 16px;
                color: #edf3ff;
                padding: 12px;
            }
            QLabel {
                color: #edf3ff;
            }
        """

    def _setup_ui(self) -> None:
        central = QWidget(objectName="root")
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QWidget(objectName="topBar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 10, 18, 10)
        brand = QLabel("OSIRIS")
        brand.setObjectName("brand")
        header_layout.addWidget(brand)
        header_layout.addStretch()
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusBadge")
        header_layout.addWidget(self.status_label)
        root.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("tabPanel")

        text_tab = QWidget()
        text_layout = QVBoxLayout(text_tab)
        text_layout.setContentsMargins(8, 8, 8, 8)

        self.chat_log = QTextBrowser()
        self.chat_log.setObjectName("chatLog")
        self.chat_log.setOpenExternalLinks(False)
        text_layout.addWidget(self.chat_log)

        input_row = QHBoxLayout()
        self.text_input = QLineEdit()
        self.text_input.setObjectName("textInput")
        self.text_input.setPlaceholderText("Ask OSIRIS anything...")
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primaryButton")
        input_row.addWidget(self.text_input)
        input_row.addWidget(self.send_button)
        text_layout.addLayout(input_row)

        voice_tab = QWidget()
        voice_layout = QVBoxLayout(voice_tab)
        voice_layout.setContentsMargins(8, 8, 8, 8)

        self.voice_transcript = QTextEdit()
        self.voice_transcript.setObjectName("voiceOutput")
        self.voice_transcript.setReadOnly(True)
        self.voice_transcript.setPlaceholderText("Transcript will appear here...")
        voice_layout.addWidget(self.voice_transcript)

        self.voice_reply = QTextEdit()
        self.voice_reply.setObjectName("voiceOutput")
        self.voice_reply.setReadOnly(True)
        self.voice_reply.setPlaceholderText("OSIRIS response will appear here...")
        voice_layout.addWidget(self.voice_reply)

        self.listen_button = QPushButton("Listen")
        self.listen_button.setObjectName("voiceButton")
        self.listen_button.setMinimumHeight(74)
        voice_layout.addWidget(self.listen_button)

        self.tabs.addTab(text_tab, "Text mode")
        self.tabs.addTab(voice_tab, "Voice mode")
        root.addWidget(self.tabs)

        self.setCentralWidget(central)

    def _append_chat(self, speaker: str, message: str, alignment: str) -> None:
        direction = "flex-end" if alignment == "right" else "flex-start"
        bubble = (
            f"<div style='margin: 10px 0; display: flex; justify-content: {direction};'>"
            f"<div style='max-width: 78%; padding: 12px 16px; border-radius: 18px; "
            f"background: rgba(123, 146, 255, 0.10); border: 1px solid rgba(123, 146, 255, 0.18);'>"
            f"<div style='font-size: 11px; color: #9fc0ff; margin-bottom: 4px; font-weight: 700;'>{speaker}</div>"
            f"<div style='color: #edf3ff; line-height: 1.5;'>{message}</div>"
            "</div></div>"
        )
        self.chat_log.insertHtml(bubble)
        self.chat_log.verticalScrollBar().setValue(self.chat_log.verticalScrollBar().maximum())

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

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
            self.voice_reply.setPlainText(reply)
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
