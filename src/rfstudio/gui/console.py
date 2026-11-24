"""Live console for script output with colouring, search and export."""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from . import icons, theme


class ConsoleView(QWidget):
    """Thread-safe: call `append()` from any thread; text is flushed every 80 ms."""

    def __init__(self, max_lines: int = 5000, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._queue: deque[tuple[float, str, str]] = deque()
        self._lock = Lock()
        self._formats: dict[str, QTextCharFormat] = {}
        self.timestamps = True
        self.autoscroll = True

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(4)
        title = QLabel("CONSOLE")
        title.setObjectName("cardTitle")
        bar.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setObjectName("cardBadge")
        bar.addWidget(self.count_label)
        bar.addStretch(1)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find…")
        self.find.setClearButtonEnabled(True)
        self.find.setFixedWidth(theme.px(170))
        self.find.returnPressed.connect(self._find_next)
        bar.addWidget(self.find)
        p = theme.current()

        def tool(name: str, tip: str, checkable: bool = False) -> QToolButton:
            b = QToolButton()
            b.setIcon(icons.icon(name, p.text))
            b.setIconSize(QSize(theme.px(16), theme.px(16)))
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(Qt.PointingHandCursor)
            bar.addWidget(b)
            return b

        self.btn_time = tool("clock", "Show timestamps", True)
        self.btn_time.setChecked(True)
        self.btn_time.toggled.connect(lambda c: setattr(self, "timestamps", c))
        self.btn_scroll = tool("scroll", "Auto-scroll to newest output", True)
        self.btn_scroll.setChecked(True)
        self.btn_scroll.toggled.connect(self._set_autoscroll)
        tool("copy", "Copy all").clicked.connect(lambda: QApplication.clipboard().setText(self.view.toPlainText()))
        tool("save", "Save console to file…").clicked.connect(self._save)
        tool("log", "Open log folder").clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.log_dir()))))
        tool("trash", "Clear").clicked.connect(self.clear)
        lay.addLayout(bar)

        self.view = QPlainTextEdit()
        self.view.setObjectName("console")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(max_lines)
        self.view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.view.setPlaceholderText("Script output will appear here.")
        lay.addWidget(self.view, 1)
        from .widgets import touch_scroll

        touch_scroll(self.view, mouse_drag=False)  # keep mouse text selection in the console

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._flush)
        self._timer.start()
        self.refresh_theme()

    def refresh_theme(self) -> None:
        p = theme.current()
        colors = {"stdout": p.text, "stderr": "#ff7b72" if p.name == "dark" else p.danger,
                  "info": p.accent, "warning": p.warning, "error": p.danger, "success": p.success,
                  "time": p.muted}
        self._formats = {}
        for key, color in colors.items():
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if key in ("info", "warning", "error", "success"):
                f.setFontWeight(600)
            self._formats[key] = f

    def set_max_lines(self, n: int) -> None:
        self.view.setMaximumBlockCount(max(200, n))

    def _set_autoscroll(self, on: bool) -> None:
        self.autoscroll = on
        if on:
            self.view.moveCursor(QTextCursor.End)

    def append(self, text: str, kind: str = "stdout") -> None:
        with self._lock:
            self._queue.append((time.time(), kind, text))
            if len(self._queue) > 20000:  # pathological output: drop the oldest
                self._queue.popleft()

    def _flush(self) -> None:
        with self._lock:
            if not self._queue:
                return
            batch = list(self._queue)
            self._queue.clear()
        sb = self.view.verticalScrollBar()
        cursor = QTextCursor(self.view.document())
        cursor.movePosition(QTextCursor.End)
        cursor.beginEditBlock()
        for ts, kind, text in batch:
            if not self.view.document().isEmpty():
                cursor.insertBlock()
            if self.timestamps:
                cursor.insertText(time.strftime("%H:%M:%S ", time.localtime(ts)), self._formats["time"])
            prefix = {"info": "› ", "warning": "⚠ ", "error": "✖ ", "success": "✔ "}.get(kind, "")
            cursor.insertText(prefix + text, self._formats.get(kind, self._formats["stdout"]))
        cursor.endEditBlock()
        if self.autoscroll:
            sb.setValue(sb.maximum())
        self.count_label.setText(f"{self.view.blockCount():,} lines")

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
        self.view.clear()
        self.count_label.setText("")

    def _find_next(self) -> None:
        needle = self.find.text()
        if not needle:
            return
        if not self.view.find(needle):
            self.view.moveCursor(QTextCursor.Start)
            self.view.find(needle)

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save console", "console.log", "Log files (*.log *.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.view.toPlainText())
