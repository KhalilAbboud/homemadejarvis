from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)


class ChatBubble(QFrame):
    """One chat message. role: 'user' | 'assistant' | 'error'."""

    def __init__(self, text: str, role: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("bubble")
        self.setProperty("role", role)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(4)

        badge = QLabel({"user": "YOU", "error": "ERROR"}.get(role, "OSIRIS"))
        badge.setObjectName("bubbleBadge")
        badge.setProperty("role", role)
        layout.addWidget(badge)

        self.body = QLabel()
        self.body.setObjectName("bubbleBody")
        self.body.setWordWrap(True)
        self.body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.body.setOpenExternalLinks(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        # Only model output is rendered as markdown; user/error text stays literal.
        self.body.setTextFormat(Qt.MarkdownText if role == "assistant" else Qt.PlainText)
        self.body.setText(text)
        layout.addWidget(self.body)
        self._text = text

    def fit_to(self, max_width: int) -> None:
        """Size to the text, capped at max_width. A wrapped QLabel's own sizeHint is far too narrow."""
        self.body.ensurePolished()  # pick up the stylesheet font before measuring
        metrics = self.body.fontMetrics()
        margins = self.layout().contentsMargins()
        chrome = margins.left() + margins.right() + 6
        natural = max((metrics.horizontalAdvance(line) for line in self._text.splitlines()), default=0)
        self.setFixedWidth(min(max_width, max(natural + chrome + 8, 120)))


class ChatView(QScrollArea):
    """Scrollable column of ChatBubble rows."""

    MAX_WIDTH_RATIO = 0.78

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatLog")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("chatContent")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(12)
        self._layout.addStretch(1)  # keeps messages packed to the top
        self.setWidget(content)
        content.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)

        self._bubbles: list[ChatBubble] = []

    def _max_bubble_width(self) -> int:
        return max(240, int(self.viewport().width() * self.MAX_WIDTH_RATIO))

    def add_message(self, text: str, role: str) -> ChatBubble:
        bubble = ChatBubble(text, role)
        bubble.fit_to(self._max_bubble_width())

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        if role == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch(1)

        self._layout.insertWidget(self._layout.count() - 1, row)
        self._bubbles.append(bubble)
        self._scroll_soon()
        return bubble

    def clear(self) -> None:
        while self._layout.count() > 1:  # keep the trailing stretch
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._bubbles.clear()

    def _scroll_soon(self) -> None:
        # Layout hasn't been recomputed yet, so scroll on the next event-loop turn.
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self._max_bubble_width()
        for bubble in self._bubbles:
            bubble.fit_to(width)