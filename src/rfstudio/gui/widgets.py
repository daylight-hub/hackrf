"""Reusable widgets: toggle switch, cards, status pill, number field, stats."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import script_model
from . import icons, theme


class ToggleSwitch(QAbstractButton):
    """Animated on/off switch."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._offset = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(theme.px(42), theme.px(24))

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        super().setChecked(checked)
        if not hasattr(self, "_anim") or self._anim.state() != QPropertyAnimation.Running:
            self._offset = 1.0 if checked else 0.0
            self.update()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(1, 2, self.width() - 2, self.height() - 4)
        off, on = QColor(p.border), QColor(p.accent if self.isEnabled() else p.muted)
        t = self._offset
        track = QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t),
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        d = rect.height() - 6
        x = rect.left() + 3 + (rect.width() - d - 6) * t
        painter.setBrush(QColor("#ffffff") if self.isEnabled() else QColor(p.surface2))
        painter.drawEllipse(QRectF(x, rect.top() + 3, d, d))
        painter.end()


class Card(QFrame):
    """Rounded surface with an optional uppercase title and badge."""

    def __init__(self, title: str = "", badge: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(18, 14, 18, 16)
        self.outer.setSpacing(10)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("cardTitle")
        self.badge_label = QLabel(badge)
        self.badge_label.setObjectName("cardBadge")
        self.badge_label.setVisible(bool(badge))
        self.header.addWidget(self.title_label)
        self.header.addWidget(self.badge_label)
        self.header.addStretch(1)
        if title:
            self.outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self.outer.addLayout(self.body)

    def set_badge(self, text: str) -> None:
        self.badge_label.setText(text)
        self.badge_label.setVisible(bool(text))


class StatusPill(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 12, 4)
        lay.setSpacing(7)
        self.dot = QLabel()
        self.text = QLabel()
        self.text.setStyleSheet("font-weight: 700;")
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        self.set_state("stopped")

    def set_state(self, state: str, label: str | None = None) -> None:
        color = theme.state_color(state)
        p = theme.current()
        self.dot.setPixmap(icons.status_dot(color, 9))
        self.text.setText((label or state).capitalize())
        self.text.setStyleSheet(f"font-weight: 700; color: {color};")
        self.setStyleSheet(f"StatusPill {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 13px; }}")


class Stat(QWidget):
    def __init__(self, label: str, value: str = "-", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.value = QLabel(value)
        self.value.setObjectName("statValue")
        cap = QLabel(label.upper())
        cap.setObjectName("statLabel")
        lay.addWidget(self.value)
        lay.addWidget(cap)

    def set(self, text: str) -> None:
        self.value.setText(text)


class NumberEdit(QLineEdit):
    """Number entry that accepts 433.92M, 8e6, 2_000_000 and validates live."""

    valueChanged = Signal(object)

    def __init__(self, var: script_model.ScriptVariable, value: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.var = var
        self._value = value
        self.setText(script_model.value_to_text(var, value))
        hint = "e.g. 433.92M, 8e6" if var.kind == script_model.KIND_FLOAT else "whole number"
        if var.minimum is not None or var.maximum is not None:
            lo = "" if var.minimum is None else script_model.format_number(var.minimum)
            hi = "" if var.maximum is None else script_model.format_number(var.maximum)
            hint = f"{lo} … {hi}"
        self.setPlaceholderText(hint)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.textEdited.connect(self._validate)
        self.editingFinished.connect(self._commit)

    def _validate(self, text: str) -> bool:
        try:
            self.var.coerce(text)
            ok, msg = True, ""
        except ValueError as exc:
            ok, msg = False, str(exc)
        self.setProperty("invalid", not ok)
        self.setToolTip(msg)
        self.style().unpolish(self)
        self.style().polish(self)
        return ok

    def _commit(self) -> None:
        if not self._validate(self.text()):
            return
        value = self.var.coerce(self.text())
        if value != self._value or type(value) is not type(self._value):
            self._value = value
            self.valueChanged.emit(value)

    def set_value(self, value: Any) -> None:
        self._value = value
        self.setText(script_model.value_to_text(self.var, value))
        self._validate(self.text())

    def wheelEvent(self, event) -> None:  # noqa: N802 - step with the mouse wheel when a step is defined
        if self.var.step and self.hasFocus():
            try:
                cur = self.var.coerce(self.text())
                new = cur + (self.var.step if event.angleDelta().y() > 0 else -self.var.step)
                new = self.var.coerce(new)
            except (ValueError, TypeError):
                return
            self.set_value(new)
            self._commit()
            event.accept()
            return
        super().wheelEvent(event)


def button(text: str, icon_name: str | None = None, variant: str | None = None,
           tooltip: str = "", parent: QWidget | None = None) -> QPushButton:
    b = QPushButton(text, parent)
    if variant:
        b.setProperty("variant", variant)
    if icon_name:
        p = theme.current()
        color = p.accent_text if variant == "primary" else (p.danger if variant == "danger" else p.text)
        b.setIcon(icons.icon(icon_name, color, p.surface, disabled=p.muted))
        b.setIconSize(QSize(theme.px(16), theme.px(16)))
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def muted(text: str, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(wrap)
    return lab


def page_header(title: str, subtitle: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 4)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("pageTitle")
    s = QLabel(subtitle)
    s.setObjectName("pageSubtitle")
    s.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(s)
    return w


def form_row(label: str, widget: QWidget, help_text: str = "") -> QWidget:
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(16)
    left = QVBoxLayout()
    left.setSpacing(1)
    lab = QLabel(label)
    lab.setObjectName("optionLabel")
    left.addWidget(lab)
    if help_text:
        h = QLabel(help_text)
        h.setObjectName("optionDesc")
        h.setWordWrap(True)
        left.addWidget(h)
    lay.addLayout(left, 1)
    lay.addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)
    return row
