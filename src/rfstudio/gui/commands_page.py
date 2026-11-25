"""Commands page: run the script's report flags (--history, --top ...) and show
their output nicely; choose the flag used for supervised runs (--scan ...)."""

from __future__ import annotations

import csv
import io
import shlex
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config, diagnostics, paths, report_format, script_model, session
from . import theme
from .widgets import Card, StatusPill, button, muted, page_header, touch_scroll


class _SortItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:  # numeric-aware sorting
        return report_format.sort_key(self.text()) < report_format.sort_key(other.text())


def _mono_css() -> str:
    return ('font-family: "JetBrains Mono", "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace; '
            f"font-size: {theme.pt(10):g}pt;")


class ResultView(QWidget):
    """Output of one command run: formatted (tables, summaries) or raw."""

    rerun = Signal(list)
    stopRequested = Signal()

    def __init__(self, title: str, args: list[str], cwd: str | None = None) -> None:
        super().__init__()
        self.args = args
        self.cwd = cwd
        self.files: list[str] = []
        self.raw = ""
        self.blocks: list[report_format.Block] = []
        self.started = time.time()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.px(14), theme.px(12), theme.px(14), theme.px(12))
        lay.setSpacing(theme.px(10))

        head = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("scriptName")
        cmd = QLabel(" ".join(shlex.quote(a) for a in args) if args else "result files")
        cmd.setObjectName("optionName")
        tl = QVBoxLayout()
        tl.setSpacing(2)
        tl.addWidget(t)
        tl.addWidget(cmd)
        head.addLayout(tl, 1)
        self.pill = StatusPill()
        self.pill.set_state("starting", "Running…")
        head.addWidget(self.pill, 0, Qt.AlignTop)
        lay.addLayout(head)

        bar = QHBoxLayout()
        self.toggle_raw = button("Show raw output", "log", "ghost")
        self.toggle_raw.setCheckable(True)
        self.toggle_raw.toggled.connect(self._show_mode)
        copy_btn = button("Copy", "copy", "ghost")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.raw))
        save_btn = button("Save…", "save", "ghost")
        save_btn.clicked.connect(self._save)
        again = button("Run again", "reload", "ghost")
        again.clicked.connect(lambda: self.rerun.emit(self.args))
        self.stop_btn = button("Stop", "stop", "danger")
        self.stop_btn.clicked.connect(self.stopRequested.emit)
        self.again_btn = again
        again.hide()
        self.meta = muted("", False)
        for w in (self.stop_btn, self.toggle_raw, copy_btn, save_btn, again):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.meta)
        lay.addLayout(bar)

        self.diag = QFrame()
        self.diag.setObjectName("bannerError")
        dl = QVBoxLayout(self.diag)
        dl.setContentsMargins(theme.px(14), theme.px(8), theme.px(14), theme.px(8))
        self.diag_title = QLabel()
        self.diag_title.setObjectName("bannerTitle")
        self.diag_text = QLabel()
        self.diag_text.setWordWrap(True)
        dl.addWidget(self.diag_title)
        dl.addWidget(self.diag_text)
        self.diag.hide()
        lay.addWidget(self.diag)

        self.formatted = QScrollArea()
        self.formatted.setWidgetResizable(True)
        self.formatted.setFrameShape(QFrame.NoFrame)
        self.inner = QWidget()
        self.inner_lay = QVBoxLayout(self.inner)
        self.inner_lay.setContentsMargins(0, 0, theme.px(6), 0)
        self.inner_lay.setSpacing(theme.px(10))
        self.formatted.setWidget(self.inner)
        touch_scroll(self.formatted)
        self.raw_view = QPlainTextEdit()
        self.raw_view.setObjectName("console")
        self.raw_view.setReadOnly(True)
        touch_scroll(self.raw_view, mouse_drag=False)
        lay.addWidget(self.formatted, 1)
        lay.addWidget(self.raw_view, 1)
        self.raw_view.hide()
        self._waiting = muted("Waiting for output…", False)
        self.inner_lay.addWidget(self._waiting)
        self.inner_lay.addStretch(1)
        self.toggle_raw.setChecked(bool(args))  # live output while it runs

    def append(self, text: str) -> None:
        self.raw += text
        self.raw_view.setPlainText(self.raw)
        self.raw_view.verticalScrollBar().setValue(self.raw_view.verticalScrollBar().maximum())
        lines = self.raw.count("\n")
        self._waiting.setText(f"Running… {lines} line{'s' if lines != 1 else ''} so far")

    def finish(self, code: int, stderr_tail: list[str]) -> None:
        self.stop_btn.hide()
        self.again_btn.show()
        dur = time.time() - self.started
        ok = code == 0
        self.pill.set_state("running" if ok else "failed", "Done" if ok else f"Exit code {code}")
        self.meta.setText(f"{time.strftime('%H:%M:%S', time.localtime(self.started))} · {dur:.1f} s")
        if not ok:
            d = diagnostics.diagnose(stderr_tail, code)
            if d:
                self.diag_title.setText(d.title)
                self.diag_text.setText(d.detail)
                self.diag.show()
        self.files = report_format.find_output_files(self.raw.splitlines(), self.cwd)
        self._render()

    def show_files(self, files: list[str]) -> None:
        """File-only view (results of a supervised run that finished)."""
        self.files = files
        self.pill.set_state("running", "Results")
        self.meta.setText(time.strftime("%H:%M:%S"))
        self.toggle_raw.hide()
        self.stop_btn.hide()
        self._render()

    def _render(self) -> None:
        while self.inner_lay.count():
            item = self.inner_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.blocks = report_format.parse(self.raw) if self.raw else []
        if not self.blocks and not self.files:
            self.inner_lay.addWidget(muted("The command finished without printing anything.", False))
        if self.files:  # result files first: that's usually what the user wants to see
            for path in self.files:
                self.inner_lay.addWidget(self._file_card(path))
            if self.blocks:
                out_title = QLabel("OUTPUT")
                out_title.setObjectName("subTitle")
                self.inner_lay.addWidget(out_title)
        current: Card | None = None
        for b in self.blocks:
            if b.kind == "title":
                current = Card(b.text)
                self.inner_lay.addWidget(current)
                continue
            target = current.body if current is not None else self.inner_lay
            if b.kind in ("table", "kv"):
                target.addWidget(self._table(b))
            else:
                lab = QLabel(b.text)
                lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
                lab.setWordWrap(True)
                lab.setStyleSheet(_mono_css())
                target.addWidget(lab)
        self.inner_lay.addStretch(1)
        has_structure = any(b.kind in ("table", "kv", "title") for b in self.blocks) or bool(self.files)
        # formatted view when there's something to format; plain text reads better as raw output
        self.toggle_raw.setChecked(not has_structure and bool(self.blocks))

    def _file_card(self, path: str) -> Card:
        card = Card(Path(path).name, "result file")
        row = QHBoxLayout()
        loc = muted(str(Path(path).parent), False)
        loc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        open_btn = button("Open file", "edit", "ghost")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        folder_btn = button("Open folder", "folder", "ghost")
        folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent))))
        row.addWidget(loc, 1)
        row.addWidget(open_btn)
        row.addWidget(folder_btn)
        card.body.addLayout(row)
        self.blocks_from_files = getattr(self, "blocks_from_files", [])
        for b in report_format.file_blocks(path):
            if b.kind == "title":
                if b.text != Path(path).name:
                    t = QLabel(b.text.upper())
                    t.setObjectName("subTitle")
                    card.body.addWidget(t)
                continue
            self.blocks_from_files.append(b)
            if b.kind in ("table", "kv"):
                card.body.addWidget(self._table(b))
            else:
                lab = QLabel(b.text)
                lab.setWordWrap(True)
                lab.setStyleSheet(_mono_css())
                card.body.addWidget(lab)
        return card

    def _table(self, b: report_format.Block) -> QTableWidget:
        t = QTableWidget(len(b.rows), len(b.headers))
        t.setHorizontalHeaderLabels(b.headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setAlternatingRowColors(True)
        t.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        t.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        for r, row in enumerate(b.rows):
            for c, cell in enumerate(row):
                item = _SortItem(cell)
                if b.kind == "table" and report_format._is_numberish(cell):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(r, c, item)
        hh = t.horizontalHeader()
        if b.kind == "table":
            hh.setSectionsClickable(True)
            hh.setSortIndicatorShown(False)
            state = {"col": -1, "order": Qt.AscendingOrder}

            def sort_by(col: int) -> None:
                order = Qt.DescendingOrder if state["col"] == col and state["order"] == Qt.AscendingOrder \
                    else Qt.AscendingOrder
                state.update(col=col, order=order)
                hh.setSortIndicatorShown(True)
                hh.setSortIndicator(col, order)
                t.sortItems(col, order)

            hh.sectionClicked.connect(sort_by)
            t.setToolTip("Click a column header to sort")
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        if b.kind == "kv":
            hh.setVisible(False)
        row_h = t.verticalHeader().defaultSectionSize()
        visible = min(len(b.rows), 18)
        t.setMinimumHeight(row_h * visible + (0 if b.kind == "kv" else hh.sizeHint().height()) + theme.px(8))
        if len(b.rows) <= 18:
            t.setMaximumHeight(t.minimumHeight())
            t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        touch_scroll(t)
        return t

    def _show_mode(self, raw: bool) -> None:
        self.raw_view.setVisible(raw)
        self.formatted.setVisible(not raw)
        self.toggle_raw.setText("Show formatted" if raw else "Show raw output")

    def _save(self) -> None:
        tables = [b for b in [*getattr(self, "blocks_from_files", []), *self.blocks] if b.kind in ("table", "kv")]
        default = (self.args[0].lstrip("-") if self.args else "output") + (".csv" if len(tables) == 1 else ".txt")
        path, _ = QFileDialog.getSaveFileName(self, "Save output", default, "CSV (*.csv);;Text (*.txt)")
        if not path:
            return
        if path.lower().endswith(".csv") and tables:
            buf = io.StringIO()
            w = csv.writer(buf)
            for i, b in enumerate(tables):
                if i:
                    w.writerow([])
                w.writerow(b.headers)
                w.writerows(b.rows)
            Path(path).write_text(buf.getvalue(), encoding="utf-8")
        else:
            Path(path).write_text(self.raw, encoding="utf-8")


class CommandsPage(QWidget):
    startModeRequested = Signal(str)   # main window: set start mode and start

    def __init__(self, settings: config.Settings, script: Callable[[], Path | None],
                 spec: Callable[[], script_model.ScriptSpec | None]) -> None:
        super().__init__()
        self.settings = settings
        self.get_script = script
        self.get_spec = spec
        self.procs: list[QProcess] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.px(24), theme.px(20), theme.px(24), theme.px(16))
        lay.setSpacing(theme.px(12))
        lay.addWidget(page_header("Commands", "Your script's command-line options. Reports run once and show their "
                                  "output here; run modes start the script under supervision."))

        split = QSplitter(Qt.Horizontal)
        self.left = QScrollArea()
        self.left.setWidgetResizable(True)
        self.left.setFrameShape(QFrame.NoFrame)
        self.left_inner = QWidget()
        self.left_lay = QVBoxLayout(self.left_inner)
        self.left_lay.setContentsMargins(0, 0, theme.px(6), 0)
        self.left_lay.setSpacing(theme.px(12))
        self.left.setWidget(self.left_inner)
        self.left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.left.setMinimumWidth(theme.px(440))
        touch_scroll(self.left)
        split.addWidget(self.left)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.tabs.removeTab)
        self.empty_results = muted("Run a report on the left - its output appears here, with tables you can "
                                   "sort, copy and save.", True)
        self.empty_results.setAlignment(Qt.AlignCenter)
        self.results_stack = QWidget()
        rs = QVBoxLayout(self.results_stack)
        rs.setContentsMargins(0, 0, 0, 0)
        rs.addWidget(self.empty_results, 1)
        rs.addWidget(self.tabs, 1)
        self.tabs.hide()
        split.addWidget(self.results_stack)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 7)
        split.setSizes([theme.px(480), theme.px(760)])
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)
        self.refresh()

    # ------------------------------------------------------------------ build
    def refresh(self) -> None:
        while self.left_lay.count():
            item = self.left_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        spec = self.get_spec()
        cmds = spec.commands if spec and spec.commands else None
        if not cmds or not (cmds.reports or cmds.modes):
            lab = muted("This script has no command-line options.\n\nCommands appear here automatically when a "
                        "script defines flags with argparse (parser.add_argument('--history', ...)) or checks "
                        "sys.argv for flags such as \"--scan\".", True)
            self.left_lay.addWidget(lab)
            self.left_lay.addStretch(1)
            return

        if cmds.modes:
            card = Card("Run modes", str(len(cmds.modes)))
            card.body.addWidget(muted("Run once shows the output and any result files here. Keep running makes it "
                                      "the Start mode on the Control page (restarted if it crashes).", True))
            script = self.get_script()
            current = session.start_mode_for(self.settings, script, spec) if script else ""
            for m in cmds.modes:
                row = QVBoxLayout()
                row.setSpacing(theme.px(6))
                txt = QVBoxLayout()
                txt.setSpacing(1)
                name = QLabel(m.label + ("   ✓ start mode" if m.flag == current else ""))
                name.setObjectName("optionLabel")
                flag = QLabel(m.flag)
                flag.setObjectName("optionName")
                txt.addWidget(name)
                txt.addWidget(flag)
                if m.help:
                    h = muted(m.help, True)
                    txt.addWidget(h)
                row.addLayout(txt)
                btns = QHBoxLayout()
                once = button("Run once", "play")
                once.setToolTip(f"Run {m.flag} once here and show its output and result files")
                once.clicked.connect(lambda _=False, f=m.flag, lab=m.label: self.run([f], lab))
                keep = button("Keep running", "restart", "primary")
                keep.setToolTip(f"Make {m.flag} the start mode and run it supervised from the Control page")
                keep.clicked.connect(lambda _=False, f=m.flag: self.startModeRequested.emit(f))
                btns.addWidget(once)
                btns.addWidget(keep)
                btns.addStretch(1)
                row.addLayout(btns)
                card.body.addLayout(row)
            self.left_lay.addWidget(card)

        if cmds.reports:
            card = Card("Reports", str(len(cmds.reports)))
            for i, c in enumerate(cmds.reports):
                if i:
                    div = QFrame()
                    div.setObjectName("rowDivider")
                    card.body.addWidget(div)
                card.body.addWidget(self._report_row(c))
            self.left_lay.addWidget(card)

        custom = Card("Custom")
        row = QHBoxLayout()
        self.custom_args = QLineEdit()
        self.custom_args.setPlaceholderText("any arguments, e.g. --top 20")
        self.custom_args.returnPressed.connect(self._run_custom)
        run = button("Run", "play")
        run.clicked.connect(self._run_custom)
        row.addWidget(self.custom_args, 1)
        row.addWidget(run)
        custom.body.addLayout(row)
        self.left_lay.addWidget(custom)
        self.left_lay.addStretch(1)

    def _report_row(self, c: Any) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, theme.px(4), 0, theme.px(4))
        lay.setSpacing(theme.px(4))
        top = QHBoxLayout()
        txt = QVBoxLayout()
        txt.setSpacing(1)
        name = QLabel(c.label)
        name.setObjectName("optionLabel")
        flag = QLabel(c.flag + (f" <{c.metavar or c.type_name}>" if c.takes_value else ""))
        flag.setObjectName("optionName")
        txt.addWidget(name)
        txt.addWidget(flag)
        top.addLayout(txt, 1)
        value_edit: QLineEdit | None = None
        if c.takes_value:
            value_edit = QLineEdit()
            value_edit.setFixedWidth(theme.px(130))
            hint = c.metavar or c.type_name
            if c.default not in (None, False, ""):
                value_edit.setText(str(c.default))
            value_edit.setPlaceholderText(("optional " if c.optional_value else "") + hint.lower())
            top.addWidget(value_edit, 0, Qt.AlignVCenter)
        run = button("Run", "play", "primary")
        run.setToolTip(f"Run the script with {c.flag} and show its output")
        top.addWidget(run, 0, Qt.AlignVCenter)
        lay.addLayout(top)
        if c.help:
            lay.addWidget(muted(c.help, True))

        def go() -> None:
            val = value_edit.text().strip() if value_edit is not None else None
            if c.takes_value and not c.optional_value and not val:
                value_edit.setFocus()
                value_edit.setPlaceholderText("value required")
                return
            self.run(c.argv(val), c.label)

        run.clicked.connect(go)
        if value_edit is not None:
            value_edit.returnPressed.connect(go)
        return w

    def _run_custom(self) -> None:
        text = self.custom_args.text().strip()
        if text:
            self.run(shlex.split(text, posix=not paths.IS_WINDOWS), text)

    # -------------------------------------------------------------------- run
    def run(self, args: list[str], title: str | None = None) -> None:
        script = self.get_script()
        if script is None:
            return
        view = ResultView(title or " ".join(args), args, cwd=str(session.working_dir(script)))
        view.rerun.connect(lambda a: self.run(a, title))
        self.tabs.show()
        self.empty_results.hide()
        idx = self.tabs.addTab(view, (title or " ".join(args))[:28])
        self.tabs.setCurrentIndex(idx)
        try:
            spec = session.prepare_launch(self.settings, script, args=args)
        except Exception as exc:  # noqa: BLE001
            view.append(str(exc))
            view.finish(-1, [str(exc)])
            return
        proc = QProcess(self)
        env = QProcessEnvironment()
        for k, v in (spec.env or {}).items():
            env.insert(k, v)
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(spec.cwd or str(script.parent))
        if paths.IS_WINDOWS and hasattr(proc, "setCreateProcessArgumentsModifier"):
            def _no_window(a):  # type: ignore[no-untyped-def]
                a.flags |= 0x08000000
            try:
                proc.setCreateProcessArgumentsModifier(_no_window)
            except (TypeError, AttributeError):
                pass
        stderr_tail: list[str] = []

        def read_out() -> None:
            view.append(bytes(proc.readAllStandardOutput()).decode("utf-8", "replace"))

        def read_err() -> None:
            text = bytes(proc.readAllStandardError()).decode("utf-8", "replace")
            stderr_tail.extend(text.splitlines()[-60:])
            del stderr_tail[:-60]
            view.append(text)

        proc.readyReadStandardOutput.connect(read_out)
        proc.readyReadStandardError.connect(read_err)
        proc.finished.connect(lambda code, _s: (read_out(), read_err(), view.finish(code, stderr_tail),
                                                self.procs.remove(proc) if proc in self.procs else None))
        proc.errorOccurred.connect(lambda e: view.append(f"Could not run: {e}\n"))
        view.stopRequested.connect(proc.kill)
        self.procs.append(proc)
        proc.start(spec.argv[0], spec.argv[1:])

    def show_files(self, files: list[str], title: str = "Results") -> None:
        view = ResultView(title, [])
        self.tabs.show()
        self.empty_results.hide()
        idx = self.tabs.addTab(view, title[:28])
        self.tabs.setCurrentIndex(idx)
        view.show_files(files)

    def stop_all(self) -> None:
        for p in list(self.procs):
            p.kill()
            p.waitForFinished(2000)
