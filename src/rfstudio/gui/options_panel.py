"""The auto-generated options panel (one card per script section)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import script_model as sm
from . import icons, theme
from .widgets import Card, NumberEdit, ToggleSwitch, button

MONO = QFont()
MONO.setFamilies(["JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "monospace"])


def _choice_label(value: Any, unit: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if unit.lower() in ("hz", "sps", "s/s"):
        for div, prefix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
            if abs(value) >= div:
                return f"{value / div:g} {prefix}{unit}"
    return sm.format_number(value) + (f" {unit}" if unit else "")


def _same(a: Any, b: Any) -> bool:
    return a == b and type(a) is type(b)


class SliderEditor(QWidget):
    valueChanged = Signal(object)

    def __init__(self, var: sm.ScriptVariable, value: Any) -> None:
        super().__init__()
        self.var = var
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.step = var.step or 1
        self.steps = int(round((var.maximum - var.minimum) / self.step))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, self.steps)
        self.slider.setPageStep(1)
        self.slider.setFixedWidth(170)
        self.label = QLabel()
        self.label.setMinimumWidth(56)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.label.setFont(MONO)
        lay.addWidget(self.slider)
        lay.addWidget(self.label)
        self.set_value(value)
        self.slider.valueChanged.connect(self._moved)
        self.slider.sliderReleased.connect(self._emit)
        self._pending = False

    def _value_at(self, idx: int) -> Any:
        v = self.var.minimum + idx * self.step
        return int(round(v)) if self.var.kind == sm.KIND_INT else float(v)

    def _moved(self, idx: int) -> None:
        self.label.setText(sm.value_to_text(self.var, self._value_at(idx)) + (f" {self.var.unit}" if self.var.unit else ""))
        if not self.slider.isSliderDown():
            self._emit()

    def _emit(self) -> None:
        self.valueChanged.emit(self._value_at(self.slider.value()))

    def set_value(self, value: Any) -> None:
        idx = int(round((float(value) - self.var.minimum) / self.step))
        self.slider.blockSignals(True)
        self.slider.setValue(max(0, min(self.steps, idx)))
        self.slider.blockSignals(False)
        self.label.setText(sm.value_to_text(self.var, value) + (f" {self.var.unit}" if self.var.unit else ""))


class OptionRow(QWidget):
    changed = Signal(str, object)

    def __init__(self, var: sm.ScriptVariable, value: Any) -> None:
        super().__init__()
        self.var = var
        self.value = value
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(14)

        text = QVBoxLayout()
        text.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.title = QLabel(var.display_name)
        self.title.setObjectName("optionLabel")
        self.dot = QLabel("●")
        self.dot.setObjectName("modifiedDot")
        self.dot.setToolTip("Changed from the script's default")
        name = QLabel(var.name)
        name.setObjectName("optionName")
        top.addWidget(self.title)
        top.addWidget(self.dot)
        top.addWidget(name)
        top.addStretch(1)
        text.addLayout(top)
        if var.description:
            d = QLabel(var.description)
            d.setObjectName("optionDesc")
            d.setWordWrap(True)
            text.addWidget(d)
        lay.addLayout(text, 1)

        self.editor = self._make_editor(value)
        lay.addWidget(self.editor, 0, Qt.AlignVCenter)
        if var.unit and not isinstance(self.editor, (SliderEditor, QComboBox)):
            u = QLabel(var.unit)
            u.setObjectName("muted")
            u.setMinimumWidth(24)
            lay.addWidget(u, 0, Qt.AlignVCenter)

        p = theme.current()
        self.reset_btn = QToolButton()
        self.reset_btn.setIcon(icons.icon("undo", p.muted))
        self.reset_btn.setIconSize(QSize(15, 15))
        self.reset_btn.setToolTip(f"Reset to script default ({var.source_text})")
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn, 0, Qt.AlignVCenter)
        self._refresh_modified()

    # ------------------------------------------------------------------ editors
    def _make_editor(self, value: Any) -> QWidget:
        v = self.var
        if v.kind == sm.KIND_BOOL:
            w = ToggleSwitch(bool(value))
            w.toggled.connect(lambda c: self._set(bool(c)))
            w.setToolTip(f"{v.name} = True / False")
            return w
        if v.kind == sm.KIND_CHOICE:
            w = QComboBox()
            for c in v.choices or []:
                w.addItem(_choice_label(c, v.unit), c)
            idx = next((i for i, c in enumerate(v.choices or []) if c == value), 0)
            w.setCurrentIndex(idx)
            w.setMinimumWidth(170)
            w.currentIndexChanged.connect(lambda i: self._set(w.itemData(i)))
            return w
        if v.kind in (sm.KIND_INT, sm.KIND_FLOAT):
            if v.minimum is not None and v.maximum is not None and v.step and \
                    0 < (v.maximum - v.minimum) / v.step <= 400:
                w = SliderEditor(v, value)
                w.valueChanged.connect(self._set)
                return w
            w = NumberEdit(v, value)
            w.setFixedWidth(170)
            w.setFont(MONO)
            w.valueChanged.connect(self._set)
            return w
        if v.kind == sm.KIND_STR:
            w = QLineEdit(str(value))
            w.setMinimumWidth(240)
            w.editingFinished.connect(lambda: self._set(w.text()))
            return w
        # literal (lists, dicts, None...) edited as Python text
        w = QLineEdit(repr(value))
        w.setFont(MONO)
        w.setMinimumWidth(240)
        w.setToolTip("Python literal, e.g. [1, 2, 3] or {'a': 1}")

        def commit() -> None:
            try:
                val = v.coerce(w.text())
            except ValueError as exc:
                w.setProperty("invalid", True)
                w.setToolTip(str(exc))
            else:
                w.setProperty("invalid", False)
                self._set(val)
            w.style().unpolish(w)
            w.style().polish(w)

        w.editingFinished.connect(commit)
        return w

    def _set(self, value: Any) -> None:
        if _same(value, self.value):
            return
        self.value = value
        self._refresh_modified()
        self.changed.emit(self.var.name, value)

    def set_value(self, value: Any, emit: bool = False) -> None:
        self.value = value
        ed = self.editor
        ed.blockSignals(True)
        try:
            if isinstance(ed, ToggleSwitch):
                ed.setChecked(bool(value))
            elif isinstance(ed, QComboBox):
                ed.setCurrentIndex(max(0, ed.findData(value)))
            elif isinstance(ed, (NumberEdit, SliderEditor)):
                ed.set_value(value)
            elif isinstance(ed, QLineEdit):
                ed.setText(str(value) if self.var.kind == sm.KIND_STR else repr(value))
        finally:
            ed.blockSignals(False)
        self._refresh_modified()
        if emit:
            self.changed.emit(self.var.name, value)

    def reset(self) -> None:
        self.set_value(self.var.default, emit=True)

    @property
    def modified(self) -> bool:
        return not _same(self.value, self.var.default)

    def _refresh_modified(self) -> None:
        self.dot.setVisible(self.modified)
        self.reset_btn.setVisible(self.modified)

    def matches(self, needle: str) -> bool:
        n = needle.lower()
        return not n or n in self.var.name.lower() or n in self.var.display_name.lower() \
            or n in self.var.description.lower() or n in self.var.section.lower()


class OptionsPanel(QWidget):
    valueChanged = Signal(str, object)
    resetAll = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[OptionRow] = []
        self.cards: list[tuple[Card, list[OptionRow]]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter options…")
        self.search.setClearButtonEnabled(True)
        p = theme.current()
        self.search.addAction(icons.icon("search", p.muted), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.apply_filter)
        bar.addWidget(self.search, 1)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.filter_buttons = {}
        for key, label in (("all", "All"), ("bool", "Toggles"), ("modified", "Changed")):
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(64)
            self.group.addButton(b)
            self.filter_buttons[key] = b
            bar.addWidget(b)
            b.toggled.connect(lambda _c: self.apply_filter())
        self.filter_buttons["all"].setChecked(True)

        self.reset_btn = button("Reset all", "undo", "ghost", "Restore every option to the script's defaults")
        self.reset_btn.clicked.connect(self._reset_all)
        bar.addWidget(self.reset_btn)
        outer.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setContentsMargins(0, 0, 6, 0)
        self.vbox.setSpacing(12)
        self.scroll.setWidget(self.container)
        outer.addWidget(self.scroll, 1)

    # ------------------------------------------------------------------ build
    def clear(self) -> None:
        while self.vbox.count():
            item = self.vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows.clear()
        self.cards.clear()

    def load(self, spec: sm.ScriptSpec | None, values: dict[str, Any]) -> None:
        self.clear()
        if spec is None:
            self._empty("Select or open a script to see its options.")
            return
        if spec.errors:
            self._empty("Could not read this script:\n" + "\n".join(spec.errors))
            return
        visible = spec.visible()
        if not visible:
            self._empty("No configurable variables found.\n\nTop-level assignments such as "
                        "ENABLE_TX = False or CENTER_FREQ = 433.92e6 appear here automatically.")
            return
        for section in spec.sections:
            vars_ = [v for v in visible if v.section == section]
            if not vars_:
                continue
            card = Card(section, f"{len(vars_)}")
            rows: list[OptionRow] = []
            for i, var in enumerate(vars_):
                if i:
                    div = QFrame()
                    div.setObjectName("rowDivider")
                    card.body.addWidget(div)
                row = OptionRow(var, values.get(var.name, var.default))
                row.changed.connect(self.valueChanged)
                row.changed.connect(lambda *_: self._update_badges())
                card.body.addWidget(row)
                rows.append(row)
                self.rows.append(row)
            card.body.setSpacing(4)
            self.vbox.addWidget(card)
            self.cards.append((card, rows))
        self.vbox.addStretch(1)
        self._update_badges()
        self.apply_filter()

    def _empty(self, text: str) -> None:
        lab = QLabel(text)
        lab.setObjectName("emptyState")
        lab.setAlignment(Qt.AlignCenter)
        lab.setWordWrap(True)
        self.vbox.addStretch(1)
        self.vbox.addWidget(lab)
        self.vbox.addStretch(2)

    def _update_badges(self) -> None:
        for card, rows in self.cards:
            changed = sum(r.modified for r in rows)
            card.set_badge(f"{len(rows)}" + (f" · {changed} changed" if changed else ""))
        self.reset_btn.setEnabled(any(r.modified for r in self.rows))

    def apply_filter(self, *_args: Any) -> None:
        needle = self.search.text().strip()
        only_bool = self.filter_buttons["bool"].isChecked()
        only_mod = self.filter_buttons["modified"].isChecked()
        for card, rows in self.cards:
            any_visible = False
            layout = card.body
            for i in range(layout.count()):
                w = layout.itemAt(i).widget()
                if isinstance(w, QFrame) and w.objectName() == "rowDivider":
                    w.setVisible(not (needle or only_bool or only_mod))
            for row in rows:
                show = row.matches(needle)
                if only_bool:
                    show = show and row.var.kind == sm.KIND_BOOL
                if only_mod:
                    show = show and row.modified
                row.setVisible(show)
                any_visible |= show
            card.setVisible(any_visible)

    def _reset_all(self) -> None:
        for row in self.rows:
            if row.modified:
                row.set_value(row.var.default)
        self._update_badges()
        self.apply_filter()
        self.resetAll.emit()

    def values(self) -> dict[str, Any]:
        return {r.var.name: r.value for r in self.rows}
