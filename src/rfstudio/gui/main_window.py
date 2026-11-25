"""Main window: navigation, control page, tray integration and supervisor wiring."""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QFileSystemWatcher, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__, config, diagnostics, paths, script_model, session
from ..headless import make_script_logger
from ..supervisor import State, Supervisor
from . import icons, theme
from .commands_page import CommandsPage
from .console import ConsoleView
from .options_panel import OptionsPanel
from .pages import DevicePage, SettingsPage, TransferPage
from .widgets import Card, Stat, StatusPill, button, muted

POLICY_LABEL = {config.RESTART_ALWAYS: "Always", config.RESTART_ON_FAILURE: "On failure",
                config.RESTART_NEVER: "Never"}


def app_icon() -> QIcon:
    for name in ("icon.png", "icon.svg"):
        p = paths.assets_dir() / name
        if p.exists():
            return QIcon(str(p))
    return icons.icon("radio", theme.current().accent)


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h:02d}h {m:02d}m"
    return f"{h:02d}:{m:02d}:{s:02d}"


class _Bridge(QObject):
    state = Signal(str)
    event = Signal(str, str)


class MainWindow(QMainWindow):
    def __init__(self, settings: config.Settings, initial_script: str | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.spec: script_model.ScriptSpec | None = None
        self.script_path: Path | None = None
        self.values: dict[str, Any] = {}
        self.pending_changes = False
        self._quitting = False
        self._logger = None
        self._last_state = State.STOPPED
        self._tray_hint_shown = False
        self._recent_output: deque[str] = deque(maxlen=60)
        self._last_diagnosis: str = ""

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        avail = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        w, h = theme.px(1320), theme.px(860)
        if avail is not None:
            w, h = min(w, int(avail.width() * 0.92)), min(h, int(avail.height() * 0.92))
        self.resize(w, h)
        self.setMinimumSize(min(theme.px(940), w), min(theme.px(620), h))

        self.bridge = _Bridge()
        self.bridge.state.connect(self._on_state)
        self.bridge.event.connect(self._on_event)
        self.supervisor = Supervisor(
            self._launch_factory, settings.supervisor,
            on_output=self._on_output,
            on_state=lambda s: self.bridge.state.emit(s.value),
            on_event=lambda lvl, msg: self.bridge.event.emit(lvl, msg),
            fatal_check=diagnostics.fatal_reason,
        )

        self._build_ui()
        self._build_tray()
        self._build_shortcuts()

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._script_changed_on_disk)
        self._reload_timer = QTimer(self, singleShot=True, interval=400)
        self._reload_timer.timeout.connect(lambda: self.reload_script(from_disk=True))
        self._apply_timer = QTimer(self, singleShot=True, interval=700)
        self._apply_timer.timeout.connect(self._apply_changes_now)
        self._save_timer = QTimer(self, singleShot=True, interval=500)
        self._save_timer.timeout.connect(lambda: config.save(self.settings))
        self._tick = QTimer(self, interval=1000)
        self._tick.timeout.connect(self._update_stats)
        self._tick.start()

        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(settings.window_geometry.encode()))

        self._populate_script_combo()
        start = Path(initial_script).resolve() if initial_script else session.default_script(settings)
        if start:
            self.load_script(start)
        self._update_controls()
        # check for a HackRF in the background shortly after start-up (skipped if a script autostarts)
        QTimer.singleShot(1500, lambda: self.device_page.detect(quiet=True))

    # ======================================================================= UI
    def _build_ui(self) -> None:
        p = theme.current()
        root = QWidget()
        root.setObjectName("root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # ---- sidebar
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(theme.px(230))
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 18, 14, 14)
        sv.setSpacing(4)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(34, 34))
        bt = QVBoxLayout()
        bt.setSpacing(0)
        t1 = QLabel("RF Script Studio")
        t1.setObjectName("brandTitle")
        t2 = QLabel("HackRF control centre")
        t2.setObjectName("brandSub")
        bt.addWidget(t1)
        bt.addWidget(t2)
        brand.addWidget(logo)
        brand.addLayout(bt)
        brand.addStretch(1)
        sv.addLayout(brand)
        sv.addSpacing(18)

        self.stack = QStackedWidget()
        self.nav = QButtonGroup(self)
        self.nav_buttons: list[QPushButton] = []
        self.page_index = {"control": 0, "commands": 1, "device": 2, "transfer": 3, "settings": 4}
        for i, (label, ic) in enumerate((("Control", "control"), ("Commands", "log"), ("Device", "device"),
                                         ("Transfer", "transfer"), ("Settings", "settings"))):
            b = QPushButton(f"  {label}")
            b.setObjectName("navButton")
            b.setCheckable(True)
            b.setIcon(icons.icon(ic, p.text))
            b.setIconSize(QSize(theme.px(18), theme.px(18)))
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, idx=i: self.stack.setCurrentIndex(idx))
            self.nav.addButton(b, i)
            self.nav_buttons.append(b)
            sv.addWidget(b)
        self.nav_buttons[0].setChecked(True)
        sv.addStretch(1)

        self.side_device = QLabel("HackRF: not checked")
        self.side_device.setObjectName("sidebarFooter")
        self.side_device.setWordWrap(True)
        sv.addWidget(self.side_device)
        sv.addSpacing(6)
        self.side_status = StatusPill()
        sv.addWidget(self.side_status, 0, Qt.AlignLeft)
        sv.addSpacing(8)
        foot = QLabel(f"v{__version__}")
        foot.setObjectName("sidebarFooter")
        sv.addWidget(foot)
        h.addWidget(side)

        # ---- pages
        self.stack.addWidget(self._build_control_page())
        self.commands_page = CommandsPage(self.settings, lambda: self.script_path, lambda: self.spec)
        self.commands_page.startModeRequested.connect(self._start_with_mode)
        self.stack.addWidget(self.commands_page)
        self.device_page = DevicePage(self.settings, lambda: self.supervisor.is_active)
        self.transfer_page = TransferPage(self.settings, self._save_soon)
        self.settings_page = SettingsPage(self.settings, lambda: str(self.script_path) if self.script_path else None)
        self.device_page.devicesChanged.connect(self.transfer_page.set_serials)
        self.device_page.probeFinished.connect(self._device_probed)
        self.settings_page.changed.connect(self._settings_changed)
        self.settings_page.themeChanged.connect(self._theme_changed)
        for page in (self.device_page, self.transfer_page, self.settings_page):
            self.stack.addWidget(page)
        self.stack.currentChanged.connect(lambda i: self.nav_buttons[i].setChecked(True))
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        sb = QStatusBar()
        self.status_msg = QLabel("Ready")
        sb.addWidget(self.status_msg, 1)
        self.status_log = QLabel()
        sb.addPermanentWidget(self.status_log)
        self.setStatusBar(sb)

    def _build_control_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 20, 24, 16)
        v.setSpacing(14)

        # ---- script header card
        head = Card()
        head.outer.setContentsMargins(20, 16, 20, 16)
        row1 = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.script_title = QLabel("No script selected")
        self.script_title.setObjectName("scriptName")
        self.script_sub = muted("Drop your script into the repository's scripts/ folder, or open one.", False)
        self.script_sub.setTextInteractionFlags(Qt.TextSelectableByMouse)
        titles.addWidget(self.script_title)
        titles.addWidget(self.script_sub)
        row1.addLayout(titles, 1)
        self.status_pill = StatusPill()
        row1.addWidget(self.status_pill, 0, Qt.AlignTop)
        head.body.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.script_combo = QComboBox()
        self.script_combo.setMinimumWidth(theme.px(300))
        self.script_combo.activated.connect(self._combo_selected)
        row2.addWidget(self.script_combo, 1)
        self.open_btn = button("Open…", "folder", tooltip="Open a Python script (Ctrl+O)")
        self.open_btn.clicked.connect(self.open_script_dialog)
        self.reload_btn = button("", "reload", "ghost", "Re-read the script from disk")
        self.reload_btn.clicked.connect(lambda: self.reload_script(from_disk=True))
        self.edit_btn = button("", "edit", "ghost", "Open the script in your editor")
        self.edit_btn.clicked.connect(self._edit_script)
        self.folder_btn = button("", "folder", "ghost", "Open the working folder (where the script's files go)")
        self.folder_btn.clicked.connect(self._open_workdir)
        row2.addWidget(self.open_btn)
        row2.addWidget(self.reload_btn)
        row2.addWidget(self.edit_btn)
        row2.addWidget(self.folder_btn)
        row2.addSpacing(16)
        self.start_btn = button("Start", "play", "primary", "Start and supervise the script (F5)")
        self.start_btn.setMinimumWidth(theme.px(110))
        self.start_btn.clicked.connect(self.start_script)
        self.stop_btn = button("Stop", "stop", "danger", "Stop the script (Shift+F5)")
        self.stop_btn.clicked.connect(self.stop_script)
        self.restart_btn = button("Restart", "restart", None, "Restart now (Ctrl+R)")
        self.restart_btn.clicked.connect(self.restart_script)
        row2.addWidget(self.start_btn)
        row2.addWidget(self.stop_btn)
        row2.addWidget(self.restart_btn)
        head.body.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(34)
        self.stat_pid = Stat("PID")
        self.stat_uptime = Stat("Uptime")
        self.stat_restarts = Stat("Restarts")
        self.stat_exit = Stat("Last exit")
        self.stat_policy = Stat("Restart policy")
        for s in (self.stat_pid, self.stat_uptime, self.stat_restarts, self.stat_exit, self.stat_policy):
            row3.addWidget(s)
        row3.addStretch(1)
        self.mode_box = QWidget()
        mb = QVBoxLayout(self.mode_box)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(2)
        self.mode_combo = QComboBox()
        self.mode_combo.setMinimumWidth(theme.px(210))
        self.mode_combo.setToolTip("Which command-line mode Start runs (e.g. --scan)")
        self.mode_combo.activated.connect(self._mode_changed)
        mcap = QLabel("START MODE")
        mcap.setObjectName("statLabel")
        mb.addWidget(self.mode_combo)
        mb.addWidget(mcap)
        row3.addWidget(self.mode_box)
        row3.addSpacing(theme.px(12))
        args_box = QVBoxLayout()
        args_box.setSpacing(2)
        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText("command-line arguments (optional)")
        self.args_edit.setMinimumWidth(theme.px(280))
        self.args_edit.editingFinished.connect(self._args_changed)
        cap = QLabel("ARGUMENTS")
        cap.setObjectName("statLabel")
        args_box.addWidget(self.args_edit)
        args_box.addWidget(cap)
        row3.addLayout(args_box)
        head.body.addSpacing(4)
        head.body.addLayout(row3)
        v.addWidget(head)

        # ---- banner (pending changes / errors)
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(16, 10, 10, 10)
        bl.setSpacing(12)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.banner_title = QLabel()
        self.banner_title.setObjectName("bannerTitle")
        self.banner_title.setWordWrap(True)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        texts.addWidget(self.banner_title)
        texts.addWidget(self.banner_text)
        bl.addLayout(texts, 1)
        self.banner_btn = button("Restart now", "restart", "primary")
        self.banner_btn.clicked.connect(self._apply_changes_now)
        self.banner_action = button("", None, "primary")
        self.banner_action.clicked.connect(self._banner_action_clicked)
        self.banner_action.hide()
        self._banner_action_kind: str | None = None
        close = QToolButton()
        close.setText("✕")
        close.setToolTip("Dismiss")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.banner.hide)
        bl.addWidget(self.banner_action, 0, Qt.AlignVCenter)
        bl.addWidget(self.banner_btn, 0, Qt.AlignVCenter)
        bl.addWidget(close, 0, Qt.AlignTop)
        self.banner.hide()
        v.addWidget(self.banner)

        # ---- options | console
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        opt_title = QLabel("OPTIONS")
        opt_title.setObjectName("cardTitle")
        self.opt_hint = muted("Generated from the script's variables. Values are saved per script.", False)
        th = QHBoxLayout()
        th.addWidget(opt_title)
        th.addSpacing(8)
        th.addWidget(self.opt_hint, 1)
        ll.addLayout(th)
        self.options = OptionsPanel()
        self.options.valueChanged.connect(self._option_changed)
        self.options.resetAll.connect(self._options_reset)
        ll.addWidget(self.options, 1)
        split.addWidget(left)
        self.console = ConsoleView(self.settings.ui.console_max_lines)
        split.addWidget(self.console)
        split.setStretchFactor(0, 11)
        split.setStretchFactor(1, 9)
        split.setSizes([700, 560])
        v.addWidget(split, 1)
        return page

    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        self.act_show = QAction("Show window", self, triggered=self.show_window)
        self.act_start = QAction("Start script", self, triggered=self.start_script)
        self.act_stop = QAction("Stop script", self, triggered=self.stop_script)
        self.act_restart = QAction("Restart script", self, triggered=self.restart_script)
        self.act_quit = QAction("Quit", self, triggered=self.quit_app)
        for a in (self.act_show, None, self.act_start, self.act_stop, self.act_restart, None, self.act_quit):
            menu.addSeparator() if a is None else menu.addAction(a)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence("F5"), self, activated=self.start_script)
        QShortcut(QKeySequence("Shift+F5"), self, activated=self.stop_script)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.restart_script)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.open_script_dialog)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.quit_app)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.options.search.setFocus())

    # ================================================================ scripts
    def _populate_script_combo(self) -> None:
        self.script_combo.blockSignals(True)
        self.script_combo.clear()
        seen: set[str] = set()
        entries: list[Path] = []
        for p in paths.discover_scripts():
            entries.append(p)
        for r in self.settings.recent_scripts:
            if Path(r).is_file():
                entries.append(Path(r))
        if self.script_path:
            entries.append(self.script_path)
        for p in entries:
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            folder = p.parent.name
            self.script_combo.addItem(f"{p.name}    ·  {folder}/", key)
        if self.script_path:
            self.script_combo.setCurrentIndex(max(0, self.script_combo.findData(str(self.script_path))))
        self.script_combo.blockSignals(False)

    def _combo_selected(self, idx: int) -> None:
        path = self.script_combo.itemData(idx)
        if path and Path(path) != self.script_path:
            self.load_script(Path(path))

    def open_script_dialog(self) -> None:
        if self.supervisor.is_active:
            QMessageBox.information(self, "Script running", "Stop the running script before switching scripts.")
            return
        start = str(self.script_path.parent) if self.script_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Open Python script", start, "Python scripts (*.py)")
        if path:
            self.load_script(Path(path))

    def load_script(self, path: Path) -> None:
        if self.supervisor.is_active and self.script_path and path.resolve() != self.script_path:
            QMessageBox.information(self, "Script running", "Stop the running script before switching scripts.")
            self._populate_script_combo()
            return
        path = path.resolve()
        if self.script_path and path != self.script_path:
            self.console.append(f"──────── switched to {path.name} ────────", "info")
        if self.script_path and str(self.script_path) in self.watcher.files():
            self.watcher.removePath(str(self.script_path))
        self.script_path = path
        self.settings.remember_script(path)
        if path.exists():
            self.watcher.addPath(str(path))
        self._logger = make_script_logger(path)
        self.status_log.setText(f"Log: {paths.log_dir() / (path.stem + '.log')}")
        self.args_edit.setText(self.settings.script_args.get(self.settings.key(path), ""))
        self.reload_script()
        self._populate_script_combo()
        self._save_soon()

    def reload_script(self, from_disk: bool = False) -> None:
        if not self.script_path:
            return
        self.spec = script_model.parse_script(self.script_path)
        self.values = {v.name: v.default for v in self.spec.variables}
        self.values.update(session.load_overrides(self.settings, self.script_path, self.spec))
        self.options.load(self.spec, self.values)
        self.script_title.setText(self.script_path.name)
        doc = self.spec.docstring.strip().splitlines()[0] if self.spec.docstring.strip() else ""
        sub = str(self.script_path.parent)
        self.script_sub.setText(f"{doc}   ·   {sub}" if doc else sub)
        self.script_sub.setToolTip(self.spec.docstring or str(self.script_path))
        n = len(self.spec.visible())
        bools = sum(1 for v in self.spec.visible() if v.kind == script_model.KIND_BOOL)
        ns = len(self.spec.sections)

        def plural(k: int, word: str) -> str:
            return f"{k} {word}{'' if k == 1 else 's'}"

        self.opt_hint.setText(f"{plural(n, 'option')} · {plural(ns, 'section')} · {plural(bools, 'toggle')}")
        self.opt_hint.setToolTip("Generated from the script's variables. Values are saved per script.")
        self._populate_modes()
        self.commands_page.refresh()
        if self.spec.errors:
            self._show_banner("⚠ " + "; ".join(self.spec.errors), error=True)
        elif from_disk:
            self.status_msg.setText(f"Reloaded {self.script_path.name} at {time.strftime('%H:%M:%S')}")
            if self.supervisor.is_active:
                self.pending_changes = True
                self._show_banner("The script changed on disk. Restart to run the new version.")
        self._update_controls()

    def _script_changed_on_disk(self, path: str) -> None:
        # editors often replace the file: re-add the watch once it reappears
        if self.script_path and Path(path).exists() and path not in self.watcher.files():
            self.watcher.addPath(path)
        elif self.script_path and not Path(path).exists():
            QTimer.singleShot(500, lambda: self.script_path and self.script_path.exists()
                              and self.watcher.addPath(str(self.script_path)))
        self._reload_timer.start()

    def _edit_script(self) -> None:
        if self.script_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.script_path)))

    def show_page(self, name: str) -> None:
        idx = self.page_index[name]
        self.stack.setCurrentIndex(idx)
        self.nav_buttons[idx].setChecked(True)

    def _populate_modes(self) -> None:
        self.mode_combo.clear()
        modes = self.spec.commands.modes if self.spec and self.spec.commands else []
        self.mode_box.setVisible(bool(modes))
        if not modes or not self.script_path:
            return
        self.mode_combo.addItem("Normal (no flag)", "")
        for m in modes:
            self.mode_combo.addItem(f"{m.label}   {m.flag}", m.flag)
        current = session.start_mode_for(self.settings, self.script_path, self.spec)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(current)))

    def _mode_changed(self, _idx: int) -> None:
        if not self.script_path:
            return
        self.settings.start_mode[self.settings.key(self.script_path)] = self.mode_combo.currentData() or ""
        self._save_soon()
        self._mark_pending()
        self.commands_page.refresh()

    def _start_with_mode(self, flag: str) -> None:
        if not self.script_path:
            return
        self.settings.start_mode[self.settings.key(self.script_path)] = flag
        self._populate_modes()
        self.commands_page.refresh()
        self._save_soon()
        self.show_page("control")
        if self.supervisor.is_active:
            self.restart_script()
        else:
            self.start_script()

    def _args_changed(self) -> None:
        if self.script_path:
            self.settings.script_args[self.settings.key(self.script_path)] = self.args_edit.text().strip()
            self._save_soon()
            self._mark_pending()

    # ================================================================ options
    def _option_changed(self, name: str, value: Any) -> None:
        if not self.script_path or not self.spec:
            return
        var = self.spec.by_name().get(name)
        if var is None:
            return
        self.values[name] = value
        store = self.settings.overrides_for(self.script_path)
        if value == var.default and type(value) is type(var.default):
            store.pop(name, None)
        else:
            store[name] = script_model.serialize(value)
        self._save_soon()
        self._mark_pending()

    def _options_reset(self) -> None:
        if self.script_path:
            self.settings.overrides_for(self.script_path).clear()
            if self.spec:
                self.values = {v.name: v.default for v in self.spec.variables}
            self._save_soon()
            self._mark_pending()

    def _mark_pending(self) -> None:
        if not self.supervisor.is_active:
            return
        if self.settings.ui.restart_on_change:
            self._apply_timer.start()
            return
        self.pending_changes = True
        self._show_banner("Options changed. They apply the next time the script starts.")

    def _apply_changes_now(self) -> None:
        self.banner.hide()
        self.pending_changes = False
        if self.supervisor.is_active:
            self.supervisor.restart()

    def _show_banner(self, text: str, error: bool = False, title: str = "",
                     action: str | None = None, action_label: str = "") -> None:
        self.banner.setObjectName("bannerError" if error else "banner")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner_title.setText(title)
        self.banner_title.setVisible(bool(title))
        self.banner_text.setText(text)
        self.banner_btn.setVisible(not error and self.supervisor.is_active)
        self._banner_action_kind = action
        self.banner_action.setText(action_label)
        self.banner_action.setVisible(bool(action and action_label))
        self.banner.show()

    def _banner_action_clicked(self) -> None:
        kind = self._banner_action_kind
        if kind in (diagnostics.ACTION_DRIVERS, diagnostics.ACTION_DEVICE):
            self.show_page("device")
            if kind == diagnostics.ACTION_DEVICE and not self.supervisor.is_active:
                self.device_page.detect()
        elif kind == diagnostics.ACTION_SETTINGS:
            self.show_page("settings")
        elif kind == diagnostics.ACTION_EDIT:
            self._edit_script()
        elif kind == diagnostics.ACTION_FOLDER:
            self._open_workdir()

    def _open_workdir(self) -> None:
        if self.script_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(session.working_dir(self.script_path))))

    def _diagnose_failure(self) -> None:
        diag = diagnostics.diagnose(list(self._recent_output), self.supervisor.last_exit_code)
        if diag is None:
            return
        self._show_banner(diag.detail, error=True, title=diag.title, action=diag.action,
                          action_label=diag.action_label)
        key = diag.title + diag.error_line
        if key != self._last_diagnosis:  # don't repeat the same hint on every restart
            self._last_diagnosis = key
            self.console.append(f"{diag.title}: {diag.detail}", "warning")

    def _device_probed(self, res: Any) -> None:
        n = len(getattr(res, "devices", []) or [])
        if n:
            self.side_device.setText(f"HackRF: {n} connected")
        elif getattr(res, "pyhackrf2_available", False) or getattr(res, "hackrf_info", ""):
            self.side_device.setText("HackRF: none found")
        else:
            self.side_device.setText("HackRF: drivers missing")

    # ============================================================= supervisor
    def _launch_factory(self):  # runs in the supervisor thread
        if not self.script_path:
            raise RuntimeError("No script selected")
        return session.prepare_launch(self.settings, self.script_path)

    def _on_output(self, line: str, stream: str) -> None:  # supervisor thread
        self._recent_output.append(line)
        self.console.append(line, stream)
        if self._logger:
            self._logger.info(("! " if stream == "stderr" else "  ") + line)

    def _on_event(self, level: str, msg: str) -> None:
        kind = {"info": "info", "warning": "warning", "error": "error"}.get(level, "info")
        self.console.append(msg, kind)
        if self._logger:
            self._logger.info(f"[{level}] {msg}")
        self.status_msg.setText(msg)
        if level == "error" and self.tray and self.settings.ui.notify and not self.isVisible():
            self.tray.showMessage(APP_NAME, msg, QSystemTrayIcon.Critical, 6000)

    def _on_state(self, state_value: str) -> None:
        state = State(state_value)
        prev, self._last_state = self._last_state, state
        if state == State.BACKOFF and self.tray and self.settings.ui.notify and not self.isVisible():
            self.tray.showMessage(APP_NAME, f"{self.script_path.name if self.script_path else 'Script'} stopped "
                                  "unexpectedly - restarting.", QSystemTrayIcon.Warning, 4000)
        if state == State.STARTING:
            self._recent_output.clear()
        if state == State.RUNNING and prev in (State.STARTING,):
            self.pending_changes = False
            if self.banner.objectName() == "banner":
                self.banner.hide()
        if state in (State.BACKOFF, State.FAILED, State.STOPPED) and prev in (State.RUNNING, State.STARTING) \
                and self.supervisor.last_exit_code not in (0, None) and self._recent_output:
            self._diagnose_failure()
        elif state == State.FAILED:
            self._diagnose_failure()
        if state in (State.STOPPED, State.FAILED):
            self.banner_btn.setVisible(False)
            if state == State.FAILED:
                self.console.append("Supervisor stopped: the script keeps failing. Fix the error and press Start.",
                                    "error")
        self._update_controls()

    def start_script(self) -> None:
        if not self.script_path:
            self.open_script_dialog()
            return
        if self.spec and self.spec.errors:
            QMessageBox.warning(self, "Script error", "\n".join(self.spec.errors))
            return
        self.supervisor.policy = self.settings.supervisor
        self.console.append(f"Starting {self.script_path.name}", "success")
        self._last_diagnosis = ""
        if self.banner.objectName() == "bannerError":
            self.banner.hide()
        self.supervisor.start()
        self._update_controls()

    def stop_script(self) -> None:
        if self.supervisor.is_active:
            self.console.append("Stopping…", "info")
            self.supervisor.stop(wait=False)
            self.banner.hide()
        self._update_controls()

    def restart_script(self) -> None:
        if not self.script_path:
            return
        self.supervisor.policy = self.settings.supervisor
        self.banner.hide()
        self.pending_changes = False
        self.supervisor.restart()

    def _update_controls(self) -> None:
        state = self.supervisor.state.value
        active = self.supervisor.is_active
        has_script = self.script_path is not None and not (self.spec and self.spec.errors)
        self.status_pill.set_state(state)
        self.side_status.set_state(state)
        self.start_btn.setEnabled(has_script and not active)
        self.stop_btn.setEnabled(active)
        self.restart_btn.setEnabled(has_script and active)
        self.script_combo.setEnabled(not active)
        self.open_btn.setEnabled(not active)
        if self.tray:
            self.act_start.setEnabled(has_script and not active)
            self.act_stop.setEnabled(active)
            self.act_restart.setEnabled(has_script and active)
            self.tray.setIcon(icons.badge_icon(app_icon(), theme.state_color(state)))
            name = self.script_path.name if self.script_path else "no script"
            self.tray.setToolTip(f"{APP_NAME}\n{name}: {state}")
        self._update_stats()

    def _update_stats(self) -> None:
        sup = self.supervisor
        self.stat_pid.set(str(sup.pid) if sup.pid else "-")
        self.stat_uptime.set(fmt_duration(sup.uptime) if sup.state == State.RUNNING else "-")
        self.stat_restarts.set(str(sup.restart_count))
        self.stat_exit.set("-" if sup.last_exit_code is None else str(sup.last_exit_code))
        self.stat_policy.set(POLICY_LABEL.get(self.settings.supervisor.restart_policy, "?"))

    # =============================================================== settings
    def _save_soon(self) -> None:
        self._save_timer.start()

    def _settings_changed(self) -> None:
        self.supervisor.policy = self.settings.supervisor
        self.console.set_max_lines(self.settings.ui.console_max_lines)
        self._update_stats()
        self._save_soon()

    def _theme_changed(self, name: str) -> None:
        theme.apply(QApplication.instance(), name, self.settings.ui.text_size)
        self.console.refresh_theme()
        self._update_controls()

    # ================================================================== window
    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_window()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._quitting and self.tray and self.settings.ui.close_to_tray:
            event.ignore()
            self.hide()
            if not self._tray_hint_shown and self.settings.ui.notify:
                self._tray_hint_shown = True
                msg = ("Still running in the background." if self.supervisor.is_active
                       else "Minimised to the system tray.")
                self.tray.showMessage(APP_NAME, msg + " Right-click the tray icon to quit.",
                                      QSystemTrayIcon.Information, 3000)
            return
        if not self._quitting:
            event.ignore()
            self.quit_app()
            return
        event.accept()

    def quit_app(self) -> None:
        if self.supervisor.is_active and self.isVisible():
            r = QMessageBox.question(self, "Quit", "The script is running. Stop it and quit?")
            if r != QMessageBox.Yes:
                return
        self._quitting = True
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.transfer_page.stop()
            self.commands_page.stop_all()
            self.supervisor.stop(wait=True)
        finally:
            QApplication.restoreOverrideCursor()
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        config.save(self.settings)
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()
