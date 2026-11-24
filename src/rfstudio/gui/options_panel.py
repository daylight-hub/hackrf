"""The auto-generated options panel (one card per script section)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
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
from .widgets import Card, NumberEdit, ToggleSwitch, button, touch_mode, touch_scroll


def mono() -> QFont:
    f = QFont()
    f.setFamilies(["JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "monospace"])
    f.setPointSizeF(theme.pt(10))
    return f


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
        self.slider.setFixedWidth(theme.px(170))
        self.label = QLabel()
        self.label.setMinimumWidth(theme.px(56))
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.label.setFont(mono())
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


class OptionRow(QFrame):
    """One option. `compact=True` renders a toggle tile for the switch grid."""

    changed = Signal(str, object)
    _tx_confirmed = False  # ask once per session before enabling a transmit switch

    def __init__(self, var: sm.ScriptVariable, value: Any, compact: bool = False) -> None:
        super().__init__()
        self.var = var
        self.value = value
        self.compact = compact
        p = theme.current()

        self.title = QLabel(var.display_name)
        self.title.setObjectName("optionLabel")
        self.dot = QLabel("●")
        self.dot.setObjectName("modifiedDot")
        self.dot.setToolTip("Changed from the script's default")
        name = QLabel(var.name)
        name.setObjectName("optionName")
        badge = None
        if var.danger:
            badge = QLabel("TX")
            badge.setObjectName("txBadge")
            badge.setToolTip("Turning this on makes the HackRF transmit")
        self.reset_btn = QToolButton()
        self.reset_btn.setIcon(icons.icon("undo", p.muted))
        self.reset_btn.setIconSize(QSize(theme.px(15), theme.px(15)))
        self.reset_btn.setToolTip(f"Reset to script default ({var.source_text})")
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.clicked.connect(self.reset)
        self.editor = self._make_editor(value)
        tip = f"{var.name} = {var.source_text}" + (f"\n{var.description}" if var.description else "")

        if compact:
            self.setObjectName("toggleTile")
            self.setProperty("danger", var.danger)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(theme.px(12), theme.px(9), theme.px(10), theme.px(9))
            lay.setSpacing(2)
            top = QHBoxLayout()
            top.setSpacing(6)
            top.addWidget(self.title)
            if badge:
                top.addWidget(badge)
            top.addWidget(self.dot)
            top.addStretch(1)
            top.addWidget(self.reset_btn)
            top.addWidget(self.editor)
            lay.addLayout(top)
            sub = QLabel(var.description or var.name)
            sub.setObjectName("optionDesc" if var.description else "optionName")
            sub.setWordWrap(True)
            lay.addWidget(sub)
            self.setToolTip(tip)
            if not touch_mode():
                self.setCursor(Qt.PointingHandCursor)
        else:
            lay = QHBoxLayout(self)
            lay.setContentsMargins(0, 6, 0, 6)
            lay.setSpacing(14)
            text = QVBoxLayout()
            text.setSpacing(1)
            top = QHBoxLayout()
            top.setSpacing(8)
            top.addWidget(self.title)
            if badge:
                top.addWidget(badge)
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
            lay.addWidget(self.editor, 0, Qt.AlignVCenter)
            if var.unit and var.unit != "Hz" and not isinstance(self.editor, (SliderEditor, QComboBox)):
                u = QLabel(var.unit)
                u.setObjectName("muted")
                u.setMinimumWidth(theme.px(24))
                lay.addWidget(u, 0, Qt.AlignVCenter)
            lay.addWidget(self.reset_btn, 0, Qt.AlignVCenter)
            self.title.setToolTip(tip)
        self._refresh_modified()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - click anywhere on a tile toggles it
        if self.compact and not touch_mode() and isinstance(self.editor, ToggleSwitch) \
                and event.button() == Qt.LeftButton:
            self.editor.toggle()
            return
        super().mousePressEvent(event)  # touch mode: let the press reach the scroller (drag to scroll)

    # ------------------------------------------------------------------ editors
    def _make_editor(self, value: Any) -> QWidget:
        v = self.var
        if v.kind == sm.KIND_BOOL:
            w = ToggleSwitch(bool(value), danger=v.danger)
            w.toggled.connect(lambda c: self._toggle(w, bool(c)))
            w.setToolTip(f"{v.name} = True / False")
            return w
        if v.kind == sm.KIND_CHOICE:
            w = QComboBox()
            for c in v.choices or []:
                w.addItem(_choice_label(c, v.unit), c)
            idx = next((i for i, c in enumerate(v.choices or []) if c == value), 0)
            w.setCurrentIndex(idx)
            w.setMinimumWidth(theme.px(170))
            w.currentIndexChanged.connect(lambda i: self._set(w.itemData(i)))
            return w
        if v.kind in (sm.KIND_INT, sm.KIND_FLOAT):
            if v.minimum is not None and v.maximum is not None and v.step and \
                    0 < (v.maximum - v.minimum) / v.step <= 400:
                w = SliderEditor(v, value)
                w.valueChanged.connect(self._set)
                return w
            w = NumberEdit(v, value)
            w.setFixedWidth(theme.px(170))
            w.setFont(mono())
            w.valueChanged.connect(self._set)
            return w
        if v.kind == sm.KIND_STR:
            w = QLineEdit(str(value))
            w.setMinimumWidth(theme.px(240))
            w.editingFinished.connect(lambda: self._set(w.text()))
            return w
        # literal (lists, dicts, None...) edited as Python text
        w = QLineEdit(repr(value))
        w.setFont(mono())
        w.setMinimumWidth(theme.px(240))
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

    def _toggle(self, switch: ToggleSwitch, on: bool) -> None:
        if on and self.var.danger and not OptionRow._tx_confirmed:
            from PySide6.QtWidgets import QMessageBox

            answer = QMessageBox.warning(
                self, "Enable transmitting?",
                f"“{self.var.display_name}” makes the HackRF transmit.\n\nOnly transmit on frequencies you are "
                "authorised to use (licence / ISM rules) or into a dummy load.\n\nEnable it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                switch.blockSignals(True)
                switch.setChecked(False)
                switch.blockSignals(False)
                return
            OptionRow._tx_confirmed = True
        self._set(on)

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
        self._collapsed: dict[str, bool] = {}

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
            b.setMinimumWidth(theme.px(64))
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
        touch_scroll(self.scroll)
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
            card.make_collapsible(collapsed=self._collapsed.get(section, section == "Advanced"))
            card.body.setSpacing(theme.px(8))
            rows: list[OptionRow] = []
            toggles = [v for v in vars_ if v.kind == sm.KIND_BOOL]
            others = [v for v in vars_ if v.kind != sm.KIND_BOOL]
            has_grid = len(toggles) >= 2 or bool(toggles and others)
            if has_grid:
                grid_host = QWidget()
                grid = QGridLayout(grid_host)
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setHorizontalSpacing(theme.px(10))
                grid.setVerticalSpacing(theme.px(10))
                cols = 2 if len(toggles) > 1 else 1
                for i, var in enumerate(toggles):
                    row = OptionRow(var, values.get(var.name, var.default), compact=True)
                    grid.addWidget(row, i // cols, i % cols)
                    rows.append(row)
                for c in range(cols):
                    grid.setColumnStretch(c, 1)
                card.body.addWidget(grid_host)
                card.grid_host = grid_host  # type: ignore[attr-defined]
            else:
                others = vars_
            if has_grid and others:
                lab = QLabel("SETTINGS")
                lab.setObjectName("subTitle")
                card.body.addSpacing(theme.px(4))
                card.body.addWidget(lab)
            for i, var in enumerate(others):
                if i:
                    div = QFrame()
                    div.setObjectName("rowDivider")
                    card.body.addWidget(div)
                row = OptionRow(var, values.get(var.name, var.default))
                card.body.addWidget(row)
                rows.append(row)
            for row in rows:
                row.changed.connect(self.valueChanged)
                row.changed.connect(lambda *_: self._update_badges())
                self.rows.append(row)
            card.title_label.mousePressEvent = (  # type: ignore[method-assign]
                lambda _e, c=card, name=section: self._toggle_card(c, name))
            if card.chevron is not None:
                card.chevron.mousePressEvent = card.title_label.mousePressEvent  # type: ignore[method-assign]
                card.badge_label.mousePressEvent = card.title_label.mousePressEvent  # type: ignore[method-assign]
            self.vbox.addWidget(card)
            self.cards.append((card, rows))
        self.vbox.addStretch(1)
        self._update_badges()
        self.apply_filter()

    def _toggle_card(self, card: Card, name: str) -> None:
        card.set_collapsed(not card.collapsed)
        self._collapsed[name] = card.collapsed

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
            filtering = bool(needle or only_bool or only_mod)
            if filtering and any_visible:
                card.body_widget.setVisible(True)  # show matches inside collapsed sections
            elif not filtering:
                card.set_collapsed(card.collapsed)
            host = getattr(card, "grid_host", None)
            if host is not None:
                host.setVisible((not card.collapsed or filtering)
                                and any(r.compact and r.isVisibleTo(host) for r in rows))
            for i in range(layout.count()):
                w = layout.itemAt(i).widget()
                if isinstance(w, QLabel) and w.objectName() == "subTitle":
                    w.setVisible(not filtering)

    def _reset_all(self) -> None:
        for row in self.rows:
            if row.modified:
                row.set_value(row.var.default)
        self._update_badges()
        self.apply_filter()
        self.resetAll.emit()

    def values(self) -> dict[str, Any]:
        return {r.var.name: r.value for r in self.rows}
