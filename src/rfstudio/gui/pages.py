"""Device, Transfer (hackrf_transfer) and Settings pages."""

from __future__ import annotations

import shlex
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__, autostart, config, drivers, hackrf_backend, paths, script_model, session
from . import theme
from .console import ConsoleView
from .widgets import Card, StatusPill, ToggleSwitch, button, form_row, muted, page_header, touch_scroll


class _Relay(QObject):
    done = Signal(object)


def run_in_thread(fn: Callable[[], Any], on_done: Callable[[Any], None], owner: QObject) -> None:
    relay = _Relay(owner)
    relay.done.connect(on_done)
    relay.done.connect(relay.deleteLater)

    def work() -> None:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            result = exc
        relay.done.emit(result)

    threading.Thread(target=work, daemon=True).start()


def scroll_page(*widgets: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(16)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    area.setWidget(inner)
    area.inner_layout = lay  # type: ignore[attr-defined]
    touch_scroll(area)
    return area


# ===================================================================== Device

class DevicePage(QWidget):
    devicesChanged = Signal(list)
    probeFinished = Signal(object)

    def __init__(self, settings: config.Settings, is_script_running: Callable[[], bool]) -> None:
        super().__init__()
        self.settings = settings
        self.is_script_running = is_script_running
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.addWidget(page_header("HackRF device", "Detect connected boards and check both interfaces: "
                                     "pyhackrf2 (Python/libhackrf) and the hackrf_transfer tools."), 1)
        self.detect_btn = button("Detect devices", "reload", "primary")
        self.detect_btn.clicked.connect(self.detect)
        header.addWidget(self.detect_btn, 0, Qt.AlignTop)

        self.backend_card = Card("Interfaces")
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.pills: dict[str, StatusPill] = {}
        self.details: dict[str, QLabel] = {}
        for r, (key, label) in enumerate((("pyhackrf2", "pyhackrf2 + libhackrf"),
                                          ("hackrf_transfer", "hackrf_transfer"),
                                          ("hackrf_info", "hackrf_info"))):
            name = QLabel(label)
            name.setObjectName("optionLabel")
            pill = StatusPill()
            pill.set_state("stopped", "Unknown")
            det = muted("Not checked yet")
            det.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(name, r, 0)
            grid.addWidget(pill, r, 1, Qt.AlignLeft)
            grid.addWidget(det, r, 2)
            self.pills[key], self.details[key] = pill, det
        grid.setColumnStretch(2, 1)
        self.backend_card.body.addLayout(grid)

        self.dev_card = Card("Connected devices")
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["#", "Board", "Serial", "Firmware", "HW rev", "Detected by"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setMinimumHeight(theme.px(160))
        touch_scroll(self.table)
        self.dev_card.body.addWidget(self.table)
        self.status = muted("Detection briefly opens the radio. If a script is using it, the device may be reported "
                            "as busy - stop the script first.")
        self.dev_card.body.addWidget(self.status)

        help_card = Card("Setup notes")
        help_card.body.addWidget(muted(
            "<b>Linux:</b> the .deb package pulls in <code>hackrf</code> and <code>libhackrf0</code> automatically. "
            "Otherwise press <i>Install HackRF drivers</i> above (or run <code>sudo ./install.sh</code> from the "
            "tarball). Re-plug the board afterwards; if you were just added to <code>plugdev</code>, log out and in."
            "<br><br><b>Windows:</b> the HackRF DLL and tools are bundled and pyhackrf2 is built in. Boards with "
            "stock firmware use the built-in WinUSB driver. If Windows shows the board with an error, press "
            "<i>Install / repair USB driver</i> to open <a href='https://zadig.akeo.ie/'>Zadig</a> with WinUSB "
            "preselected."))
        for lab in help_card.findChildren(QLabel):
            lab.setOpenExternalLinks(True)
            lab.setTextFormat(Qt.RichText)

        self.drv_card = self._build_drivers_card()

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addLayout(header)
        lay.addWidget(scroll_page(top, self.backend_card, self.dev_card, self.drv_card, help_card))
        self.refresh_drivers()

    # ---------------------------------------------------------------- drivers
    def _build_drivers_card(self) -> Card:
        card = Card("Drivers & dependencies")
        self.drv_grid = QGridLayout()
        self.drv_grid.setHorizontalSpacing(18)
        self.drv_grid.setVerticalSpacing(8)
        card.body.addLayout(self.drv_grid)
        self.drv_note = muted("")
        card.body.addWidget(self.drv_note)
        row = QHBoxLayout()
        self.drv_install = button("Install HackRF drivers", "save", "primary")
        self.drv_install.clicked.connect(self.install_drivers)
        self.drv_python = ToggleSwitch(False)
        self.drv_python.setToolTip("Also pip-install pyhackrf2 + numpy for the system Python "
                                   "(only needed for a custom interpreter)")
        self.drv_python_label = muted("Also install pyhackrf2 for system Python", False)
        recheck = button("Re-check", "reload", "ghost")
        recheck.clicked.connect(self.refresh_drivers)
        row.addWidget(self.drv_install)
        row.addSpacing(12)
        row.addWidget(self.drv_python)
        row.addWidget(self.drv_python_label)
        row.addStretch(1)
        row.addWidget(recheck)
        card.body.addLayout(row)
        self.drv_python.setVisible(not paths.IS_WINDOWS)
        self.drv_python_label.setVisible(not paths.IS_WINDOWS)
        self.drv_console = ConsoleView(1000)
        self.drv_console.setMinimumHeight(theme.px(180))
        self.drv_console.hide()
        card.body.addWidget(self.drv_console)
        self.drv_proc: QProcess | None = None
        return card

    def refresh_drivers(self) -> None:
        run_in_thread(drivers.status, self._show_drivers, self)

    def _show_drivers(self, st: Any) -> None:
        while self.drv_grid.count():
            item = self.drv_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if isinstance(st, Exception):
            st = drivers.DriverStatus(checks=[drivers.Check("Status", False, str(st))])
        for r, c in enumerate(st.checks):
            name = QLabel(c.name)
            name.setObjectName("optionLabel")
            pill = StatusPill()
            pill.set_state("running" if c.ok else "failed", "OK" if c.ok else "Missing")
            det = muted(c.detail)
            det.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.drv_grid.addWidget(name, r, 0)
            self.drv_grid.addWidget(pill, r, 1, Qt.AlignLeft)
            self.drv_grid.addWidget(det, r, 2)
        self.drv_grid.setColumnStretch(2, 1)
        self.drv_note.setText(st.note)
        self.drv_install.setText(st.install_label or "Install drivers")
        self.drv_install.setEnabled(st.can_install and self.drv_proc is None)
        self.drv_card.set_badge("all good" if st.all_ok else "action needed")

    def install_drivers(self) -> None:
        if paths.IS_WINDOWS:
            ok, msg = drivers.launch_zadig()
            (QMessageBox.information if ok else QMessageBox.warning)(self, "USB driver", msg)
            return
        try:
            cmd = drivers.linux_install_command(self.drv_python.isChecked())
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "Drivers", str(exc))
            return
        self.drv_console.show()
        self.drv_console.clear()
        self.drv_console.append(" ".join(cmd), "info")
        proc = QProcess(self)
        env = QProcessEnvironment()
        for k, v in paths.clean_child_env().items():
            env.insert(k, v)
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: [
            self.drv_console.append(line, "stdout")
            for line in bytes(proc.readAllStandardOutput()).decode("utf-8", "replace").splitlines() if line.strip()])
        proc.finished.connect(self._drivers_done)
        proc.errorOccurred.connect(lambda e: self.drv_console.append(f"Could not start installer: {e}", "error"))
        self.drv_proc = proc
        self.drv_install.setEnabled(False)
        proc.start(cmd[0], cmd[1:])

    def _drivers_done(self, code: int, _status: Any) -> None:
        self.drv_console.append("Finished." if code == 0 else f"Installer exited with code {code}",
                                "success" if code == 0 else "error")
        self.drv_proc = None
        self.refresh_drivers()

    def detect(self, quiet: bool = False) -> None:
        if quiet and self.is_script_running():
            return
        if not quiet and self.is_script_running():
            r = QMessageBox.question(self, "Script running",
                                     "A script is running and may be using the HackRF. Detect anyway?")
            if r != QMessageBox.Yes:
                return
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText("Detecting…")
        dirs = session.hackrf_search_dirs(self.settings)
        run_in_thread(lambda: hackrf_backend.probe(dirs), self._show, self)

    def _show(self, res: Any) -> None:
        self.detect_btn.setEnabled(True)
        self.detect_btn.setText("Detect devices")
        if isinstance(res, Exception):
            res = hackrf_backend.ProbeResult(errors=[str(res)])
        ok = res.pyhackrf2_available
        self.pills["pyhackrf2"].set_state("running" if ok else "failed", "Available" if ok else "Unavailable")
        self.details["pyhackrf2"].setText(res.library if ok else (res.pyhackrf2_error or "not installed"))
        for key, path, version in (("hackrf_transfer", res.hackrf_transfer, res.tools_version),
                                   ("hackrf_info", res.hackrf_info, res.lib_version)):
            self.pills[key].set_state("running" if path else "failed", "Found" if path else "Not found")
            extra = f"  ·  {'tools' if key == 'hackrf_transfer' else 'libhackrf'} {version}" if version else ""
            self.details[key].setText((path or "Install HackRF tools or set the tools folder in Settings") + extra)
        self.table.setRowCount(0)
        for d in res.devices:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, val in enumerate([str(d.index), d.board or "HackRF", d.serial, d.firmware,
                                     d.hardware_revision, d.source]):
                self.table.setItem(r, c, QTableWidgetItem(val))
        n = len(res.devices)
        msg = f"{n} device{'s' if n != 1 else ''} found." if n else "No HackRF devices found."
        if res.errors:
            msg += "  " + "  ".join(res.errors)
        self.status.setText(msg)
        self.dev_card.set_badge(str(n))
        self.devicesChanged.emit([d.serial for d in res.devices if d.serial])
        self.probeFinished.emit(res)


# =================================================================== Transfer

def _slider(lo: int, hi: int, step: int, value: int) -> tuple[QWidget, QSlider, QLabel]:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    s = QSlider(Qt.Horizontal)
    s.setRange(lo // step, hi // step)
    s.setValue(value // step)
    s.setFixedWidth(theme.px(200))
    lab = QLabel(f"{value} dB")
    lab.setMinimumWidth(theme.px(48))
    s.valueChanged.connect(lambda v: lab.setText(f"{v * step} dB"))
    lay.addWidget(s)
    lay.addWidget(lab)
    return w, s, lab


class TransferPage(QWidget):
    """Front-end for `hackrf_transfer` (record IQ to a file / transmit a file)."""

    def __init__(self, settings: config.Settings, save: Callable[[], None]) -> None:
        super().__init__()
        self.settings = settings
        self.save_settings = save
        self.proc: QProcess | None = None
        p = hackrf_backend.TransferParams.from_dict(settings.transfer)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = page_header("hackrf_transfer", "Record IQ samples to a file or transmit a file, using the HackRF "
                           "command-line tool. Useful for quick captures alongside your script.")

        form = Card("Parameters")
        mode_w = QWidget()
        ml = QHBoxLayout(mode_w)
        ml.setContentsMargins(0, 0, 0, 0)
        self.rx = QRadioButton("Receive (record)")
        self.tx = QRadioButton("Transmit")
        grp = QButtonGroup(self)
        grp.addButton(self.rx)
        grp.addButton(self.tx)
        (self.tx if p.mode == "tx" else self.rx).setChecked(True)
        ml.addWidget(self.rx)
        ml.addWidget(self.tx)
        form.body.addWidget(form_row("Mode", mode_w))

        file_w = QWidget()
        fl = QHBoxLayout(file_w)
        fl.setContentsMargins(0, 0, 0, 0)
        self.file = QLineEdit(p.file)
        self.file.setMinimumWidth(theme.px(320))
        self.file.setPlaceholderText("capture.cs8")
        browse = button("Browse…", "folder")
        browse.clicked.connect(self._browse)
        fl.addWidget(self.file)
        fl.addWidget(browse)
        form.body.addWidget(form_row("File", file_w, "Signed 8-bit interleaved IQ (.cs8 / .iq)"))

        self.freq = QDoubleSpinBox()
        self.freq.setRange(1.0, 7250.0)
        self.freq.setDecimals(6)
        self.freq.setSuffix(" MHz")
        self.freq.setValue(p.frequency_hz / 1e6)
        self.freq.setFixedWidth(theme.px(180))
        form.body.addWidget(form_row("Frequency", self.freq))

        self.rate = QComboBox()
        self.rate.setEditable(True)
        for r in (2, 4, 5, 8, 10, 12.5, 16, 20):
            self.rate.addItem(f"{r:g} Msps", r * 1e6)
        idx = self.rate.findData(p.sample_rate_hz)
        self.rate.setCurrentIndex(idx if idx >= 0 else 4)
        self.rate.setFixedWidth(theme.px(180))
        form.body.addWidget(form_row("Sample rate", self.rate))

        self.bw = QComboBox()
        self.bw.addItem("Automatic", 0.0)
        for b in (1.75, 2.5, 3.5, 5, 5.5, 6, 7, 8, 9, 10, 12, 14, 15, 20, 24, 28):
            self.bw.addItem(f"{b:g} MHz", b * 1e6)
        self.bw.setCurrentIndex(max(0, self.bw.findData(p.baseband_filter_hz)))
        self.bw.setFixedWidth(theme.px(180))
        form.body.addWidget(form_row("Baseband filter", self.bw))

        w, self.lna, _ = _slider(0, 40, 8, p.lna_gain)
        self.lna_row = form_row("LNA (IF) gain", w, "RX only, 0-40 dB in 8 dB steps")
        form.body.addWidget(self.lna_row)
        w, self.vga, _ = _slider(0, 62, 2, p.vga_gain)
        self.vga_row = form_row("VGA (baseband) gain", w, "RX only, 0-62 dB in 2 dB steps")
        form.body.addWidget(self.vga_row)
        w, self.txvga, _ = _slider(0, 47, 1, p.tx_vga_gain)
        self.txvga_row = form_row("TX VGA gain", w, "TX only, 0-47 dB")
        form.body.addWidget(self.txvga_row)

        self.amp = ToggleSwitch(p.amp)
        form.body.addWidget(form_row("RF amplifier", self.amp, "+14 dB front-end amp"))
        self.antpow = ToggleSwitch(p.antenna_power)
        form.body.addWidget(form_row("Antenna port power", self.antpow, "3.3 V bias tee for active antennas"))
        self.repeat = ToggleSwitch(p.repeat)
        self.repeat_row = form_row("Repeat", self.repeat, "Loop the file while transmitting")
        form.body.addWidget(self.repeat_row)
        self.nsamp = QSpinBox()
        self.nsamp.setRange(0, 2_000_000_000)
        self.nsamp.setSingleStep(1_000_000)
        self.nsamp.setSpecialValueText("Unlimited")
        self.nsamp.setValue(int(p.num_samples))
        self.nsamp.setFixedWidth(theme.px(180))
        form.body.addWidget(form_row("Number of samples", self.nsamp))
        self.serial = QComboBox()
        self.serial.setEditable(True)
        self.serial.addItem("")
        if p.serial:
            self.serial.addItem(p.serial)
            self.serial.setCurrentText(p.serial)
        self.serial.setFixedWidth(theme.px(320))
        self.serial.lineEdit().setPlaceholderText("first device")
        form.body.addWidget(form_row("Device serial", self.serial))
        self.extra = QLineEdit(p.extra_args)
        self.extra.setMinimumWidth(theme.px(320))
        self.extra.setPlaceholderText("additional hackrf_transfer arguments")
        form.body.addWidget(form_row("Extra arguments", self.extra))

        run_card = Card("Command")
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("commandPreview")
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(theme.px(64))
        run_card.body.addWidget(self.preview)
        row = QHBoxLayout()
        self.run_btn = button("Start", "play", "primary")
        self.run_btn.clicked.connect(self.start)
        self.stop_btn = button("Stop", "stop", "danger")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        copy = button("Copy command", "copy", "ghost")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.preview.toPlainText()))
        self.state_pill = StatusPill()
        row.addWidget(self.run_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(self.state_pill)
        run_card.body.addLayout(row)
        self.console = ConsoleView(2000)
        self.console.setMinimumHeight(theme.px(220))
        run_card.body.addWidget(self.console)

        lay.addWidget(scroll_page(head, form, run_card))

        for sig in (self.rx.toggled, self.file.textChanged, self.freq.valueChanged, self.rate.currentTextChanged,
                    self.bw.currentIndexChanged, self.lna.valueChanged, self.vga.valueChanged,
                    self.txvga.valueChanged, self.amp.toggled, self.antpow.toggled, self.repeat.toggled,
                    self.nsamp.valueChanged, self.serial.currentTextChanged, self.extra.textChanged):
            sig.connect(self._update)
        self._update()

    def set_serials(self, serials: list[str]) -> None:
        cur = self.serial.currentText()
        self.serial.blockSignals(True)
        self.serial.clear()
        self.serial.addItem("")
        for s in serials:
            self.serial.addItem(s)
        self.serial.setCurrentText(cur)
        self.serial.blockSignals(False)

    def _browse(self) -> None:
        if self.rx.isChecked():
            path, _ = QFileDialog.getSaveFileName(self, "Record to", self.file.text() or "capture.cs8",
                                                  "IQ files (*.cs8 *.iq *.bin);;All files (*)")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Transmit file", self.file.text(),
                                                  "IQ files (*.cs8 *.iq *.bin);;All files (*)")
        if path:
            self.file.setText(path)

    def params(self) -> hackrf_backend.TransferParams:
        rate = self.rate.currentData()
        if rate is None or self.rate.currentText() != self.rate.itemText(self.rate.currentIndex()):
            try:
                rate = script_model.parse_number_text(self.rate.currentText().replace("Msps", "M").replace(" ", ""))
            except ValueError:
                rate = 10e6
        return hackrf_backend.TransferParams(
            mode="rx" if self.rx.isChecked() else "tx", file=self.file.text().strip(),
            frequency_hz=self.freq.value() * 1e6, sample_rate_hz=float(rate),
            baseband_filter_hz=float(self.bw.currentData() or 0), lna_gain=self.lna.value() * 8,
            vga_gain=self.vga.value() * 2, tx_vga_gain=self.txvga.value(), amp=self.amp.isChecked(),
            antenna_power=self.antpow.isChecked(), num_samples=self.nsamp.value(),
            repeat=self.repeat.isChecked(), serial=self.serial.currentText().strip(),
            extra_args=self.extra.text())

    def _tool(self) -> str | None:
        return hackrf_backend.find_tool("hackrf_transfer", session.hackrf_search_dirs(self.settings))

    def _update(self, *_: Any) -> None:
        rx = self.rx.isChecked()
        self.lna_row.setVisible(rx)
        self.vga_row.setVisible(rx)
        self.txvga_row.setVisible(not rx)
        self.repeat_row.setVisible(not rx)
        p = self.params()
        try:
            cmd = hackrf_backend.build_transfer_command(self._tool() or "hackrf_transfer", p)
            self.preview.setPlainText(" ".join(shlex.quote(c) for c in cmd))
            self.run_btn.setEnabled(self.proc is None)
        except ValueError as exc:
            self.preview.setPlainText(f"# {exc}")
            self.run_btn.setEnabled(False)
        self.settings.transfer = p.__dict__.copy()

    def start(self) -> None:
        tool = self._tool()
        if not tool:
            QMessageBox.warning(self, "hackrf_transfer not found",
                                "Install the HackRF tools, or set their folder in Settings → HackRF tools.")
            return
        p = self.params()
        if p.mode == "tx" and not self.settings.hackrf.tx_acknowledged:
            r = QMessageBox.warning(
                self, "Transmit",
                "Transmitting requires authorisation for the frequency you use (e.g. an amateur licence or an "
                "ISM-band allowance). Only transmit into a dummy load or where you are permitted to.\n\n"
                "Continue?", QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return
            self.settings.hackrf.tx_acknowledged = True
        try:
            cmd = hackrf_backend.build_transfer_command(tool, p)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid parameters", str(exc))
            return
        self.save_settings()
        self.proc = QProcess(self)
        env = QProcessEnvironment()
        for k, v in paths.clean_child_env(session.hackrf_search_dirs(self.settings)).items():
            env.insert(k, v)
        self.proc.setProcessEnvironment(env)
        if paths.IS_WINDOWS and hasattr(self.proc, "setCreateProcessArgumentsModifier"):
            def _no_window(args):  # type: ignore[no-untyped-def]
                args.flags |= 0x08000000  # CREATE_NO_WINDOW
            try:
                self.proc.setCreateProcessArgumentsModifier(_no_window)
            except (TypeError, AttributeError):
                pass
        self.proc.setProgram(cmd[0])
        self.proc.setArguments(cmd[1:])
        if p.file:
            parent = Path(p.file).expanduser().resolve().parent
            if parent.is_dir():
                self.proc.setWorkingDirectory(str(parent))
        self.proc.readyReadStandardOutput.connect(lambda: self._read(False))
        self.proc.readyReadStandardError.connect(lambda: self._read(True))
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(lambda e: self.console.append(f"Process error: {e}", "error"))
        self.console.append(" ".join(shlex.quote(c) for c in cmd), "info")
        self.proc.start()
        self.state_pill.set_state("running", "Transmitting" if p.mode == "tx" else "Recording")
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _read(self, err: bool) -> None:
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardError() if err else self.proc.readAllStandardOutput())
        for line in data.decode("utf-8", "replace").replace("\r", "\n").splitlines():
            if line.strip():
                # hackrf_transfer prints its progress on stderr; that's not an error
                self.console.append(line, "stdout")

    def stop(self) -> None:
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.proc.terminate()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()

    def _finished(self, code: int, _status: Any) -> None:
        self.console.append(f"hackrf_transfer exited with code {code}", "success" if code == 0 else "warning")
        self.state_pill.set_state("stopped" if code == 0 else "failed", "Finished" if code == 0 else f"Exit {code}")
        self.proc = None
        self.stop_btn.setEnabled(False)
        self._update()


# =================================================================== Settings

class SettingsPage(QWidget):
    changed = Signal()
    themeChanged = Signal(str)

    def __init__(self, settings: config.Settings, current_script: Callable[[], str | None]) -> None:
        super().__init__()
        self.s = settings
        self.current_script = current_script
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = page_header("Settings", "How scripts are supervised, how the app runs in the background, "
                           "and where the HackRF tools live. Changes are saved automatically.")

        # -- supervision
        sup = Card("Supervision · keep the script running")
        sv = self.s.supervisor
        self.policy = QComboBox()
        for key, label in ((config.RESTART_ALWAYS, "Always restart"),
                           (config.RESTART_ON_FAILURE, "Restart only if it fails"),
                           (config.RESTART_NEVER, "Never restart")):
            self.policy.addItem(label, key)
        self.policy.setCurrentIndex(max(0, self.policy.findData(sv.restart_policy)))
        self.policy.setFixedWidth(theme.px(220))
        sup.body.addWidget(form_row("Restart policy", self.policy,
                                    "“Always” also restarts a script that finishes normally."))
        self.backoff = self._dspin(0.1, 600, sv.backoff_initial_s, " s")
        sup.body.addWidget(form_row("First retry delay", self.backoff, "Doubles after each quick failure"))
        self.backoff_max = self._dspin(1, 3600, sv.backoff_max_s, " s")
        sup.body.addWidget(form_row("Maximum retry delay", self.backoff_max))
        self.stable = self._dspin(1, 3600, sv.stable_after_s, " s")
        sup.body.addWidget(form_row("Healthy after", self.stable, "A run this long resets the retry delay"))
        self.max_restarts = self._spin(0, 10000, sv.max_restarts, "Unlimited")
        self.window = self._spin(10, 86400, sv.restart_window_s, suffix=" s")
        sup.body.addWidget(form_row("Give up after", self.max_restarts, "Restarts allowed within the window below"))
        sup.body.addWidget(form_row("Restart window", self.window))
        self.stall = self._spin(0, 86400, sv.stall_timeout_s, "Off", " s")
        sup.body.addWidget(form_row("Hang detection", self.stall,
                                    "Restart if the script prints nothing for this long"))
        self.stop_timeout = self._dspin(1, 120, sv.stop_timeout_s, " s")
        sup.body.addWidget(form_row("Stop timeout", self.stop_timeout,
                                    "Grace period (Ctrl+C) before the process is force-killed"))

        # -- background
        bg = Card("Startup & background")
        ui = self.s.ui
        self.close_to_tray = self._toggle(ui.close_to_tray)
        bg.body.addWidget(form_row("Close to system tray", self.close_to_tray,
                                   "Closing the window keeps the app and your script running"))
        self.start_min = self._toggle(ui.start_minimized)
        bg.body.addWidget(form_row("Start minimised", self.start_min))
        self.autorun = self._toggle(ui.autorun_script)
        bg.body.addWidget(form_row("Start script when the app opens", self.autorun))
        self.login = self._toggle(autostart.launch_at_login_enabled())
        bg.body.addWidget(form_row("Launch at login", self.login, "Opens minimised to the tray when you log in"))
        self.notify = self._toggle(ui.notify)
        bg.body.addWidget(form_row("Notifications", self.notify, "Tray messages when the script crashes or restarts"))
        self.apply_now = self._toggle(ui.restart_on_change)
        bg.body.addWidget(form_row("Apply option changes immediately", self.apply_now,
                                   "Restart a running script as soon as an option changes"))

        svc = Card("Background service · runs without the desktop app")
        svc.body.addWidget(muted(
            "Installs a per-user service that supervises the current script headlessly and is itself restarted by "
            "the operating system if it ever exits (systemd user unit on Linux, Task Scheduler on Windows). "
            "Stop the script here before starting the service so they don't compete for the radio."))
        srow = QHBoxLayout()
        self.svc_pill = StatusPill()
        self.svc_install = button("Install / update", "save", "primary")
        self.svc_remove = button("Remove", "trash", "danger")
        self.svc_install.clicked.connect(self._install_service)
        self.svc_remove.clicked.connect(self._remove_service)
        srow.addWidget(self.svc_pill)
        srow.addStretch(1)
        srow.addWidget(self.svc_install)
        srow.addWidget(self.svc_remove)
        svc.body.addLayout(srow)

        hw = Card("HackRF tools")
        dir_w = QWidget()
        dl = QHBoxLayout(dir_w)
        dl.setContentsMargins(0, 0, 0, 0)
        self.tools_dir = QLineEdit(self.s.hackrf.tools_dir)
        self.tools_dir.setPlaceholderText("auto (bundled, then PATH)")
        self.tools_dir.setMinimumWidth(theme.px(300))
        b = button("Browse…", "folder")
        b.clicked.connect(lambda: self._pick_dir(self.tools_dir))
        dl.addWidget(self.tools_dir)
        dl.addWidget(b)
        hw.body.addWidget(form_row("Tools folder", dir_w, "Folder containing hackrf_transfer / libhackrf"))
        bundled = paths.bundled_hackrf_dir()
        hw.body.addWidget(muted(f"Bundled tools: {bundled if bundled else 'none in this build (using system tools)'}"))

        py = Card("Python")
        self.py_mode = QComboBox()
        self.py_mode.addItem("Bundled (pyhackrf2 + numpy included)", "bundled")
        self.py_mode.addItem("Custom interpreter", "custom")
        self.py_mode.setCurrentIndex(max(0, self.py_mode.findData(self.s.python.mode)))
        self.py_mode.setFixedWidth(theme.px(300))
        py.body.addWidget(form_row("Run scripts with", self.py_mode,
                                   "Use a custom interpreter if your script needs extra packages"))
        int_w = QWidget()
        il = QHBoxLayout(int_w)
        il.setContentsMargins(0, 0, 0, 0)
        self.interp = QLineEdit(self.s.python.interpreter)
        self.interp.setPlaceholderText("python3  or  C:\\Python312\\python.exe")
        self.interp.setMinimumWidth(theme.px(300))
        ib = button("Browse…", "folder")
        ib.clicked.connect(self._pick_interp)
        il.addWidget(self.interp)
        il.addWidget(ib)
        self.interp_row = form_row("Interpreter", int_w)
        py.body.addWidget(self.interp_row)

        look = Card("Appearance")
        self.theme_box = QComboBox()
        self.theme_box.addItem("Dark", "dark")
        self.theme_box.addItem("Light", "light")
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(ui.theme)))
        self.theme_box.setFixedWidth(theme.px(160))
        look.body.addWidget(form_row("Theme", self.theme_box, "Fully applied after restarting the app"))
        self.text_size = QComboBox()
        for key, label in (("small", "Small"), ("normal", "Normal"), ("large", "Large"), ("xlarge", "Extra large")):
            self.text_size.addItem(label, key)
        self.text_size.setCurrentIndex(max(0, self.text_size.findData(ui.text_size)))
        self.text_size.setFixedWidth(theme.px(160))
        look.body.addWidget(form_row("Text size", self.text_size,
                                     "Text changes immediately; spacing updates after restarting the app"))
        self.touch = self._toggle(ui.touch_mode)
        look.body.addWidget(form_row("Touch screen scrolling", self.touch,
                                     "Drag anywhere on a page to scroll it (finger or mouse). Applies after "
                                     "restarting the app."))
        self.max_lines = self._spin(500, 200000, ui.console_max_lines, suffix=" lines")
        look.body.addWidget(form_row("Console history", self.max_lines))

        about = Card("About")
        about.body.addWidget(muted(
            f"<b>{APP_NAME}</b> {__version__}<br>Settings: {config.settings_path()}<br>"
            f"Logs: {paths.log_dir()}<br>Bundled scripts: "
            f"{', '.join(str(d) for d in paths.bundled_script_dirs()) or '-'}"))
        arow = QHBoxLayout()
        for label, target in (("Open log folder", paths.log_dir()), ("Open settings folder", paths.config_dir())):
            bb = button(label, "folder", "ghost")
            bb.clicked.connect(lambda _=False, t=target: QDesktopServices.openUrl(QUrl.fromLocalFile(str(t))))
            arow.addWidget(bb)
        arow.addStretch(1)
        about.body.addLayout(arow)

        lay.addWidget(scroll_page(head, sup, bg, svc, hw, py, look, about))

        for w in (self.backoff, self.backoff_max, self.stable, self.stop_timeout):
            w.valueChanged.connect(self._commit)
        for w in (self.max_restarts, self.window, self.stall, self.max_lines):
            w.valueChanged.connect(self._commit)
        for w in (self.close_to_tray, self.start_min, self.autorun, self.notify, self.apply_now, self.touch):
            w.toggled.connect(self._commit)
        for w in (self.policy, self.py_mode):
            w.currentIndexChanged.connect(self._commit)
        self.tools_dir.editingFinished.connect(self._commit)
        self.interp.editingFinished.connect(self._commit)
        self.login.toggled.connect(self._set_login)
        self.theme_box.currentIndexChanged.connect(lambda: (self._commit(), self.themeChanged.emit(
            self.theme_box.currentData())))
        self.text_size.currentIndexChanged.connect(lambda: (self._commit(), self.themeChanged.emit(
            self.theme_box.currentData())))
        self._sync_visibility()
        self.refresh_service()

    # helpers
    def _toggle(self, value: bool) -> ToggleSwitch:
        return ToggleSwitch(bool(value))

    def _spin(self, lo: int, hi: int, value: int, special: str = "", suffix: str = "") -> QSpinBox:
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setValue(int(value))
        if special:
            w.setSpecialValueText(special)
        w.setSuffix(suffix)
        w.setFixedWidth(theme.px(150))
        w.setAlignment(Qt.AlignRight)
        return w

    def _dspin(self, lo: float, hi: float, value: float, suffix: str) -> QDoubleSpinBox:
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(1)
        w.setValue(float(value))
        w.setSuffix(suffix)
        w.setFixedWidth(theme.px(150))
        w.setAlignment(Qt.AlignRight)
        return w

    def _pick_dir(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        if d:
            edit.setText(d)
            self._commit()

    def _pick_interp(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Python interpreter", self.interp.text())
        if f:
            self.interp.setText(f)
            self._commit()

    def _sync_visibility(self) -> None:
        self.interp_row.setVisible(self.py_mode.currentData() == "custom")

    def _commit(self, *_: Any) -> None:
        sv, ui = self.s.supervisor, self.s.ui
        sv.restart_policy = self.policy.currentData()
        sv.backoff_initial_s = self.backoff.value()
        sv.backoff_max_s = max(self.backoff_max.value(), sv.backoff_initial_s)
        sv.stable_after_s = self.stable.value()
        sv.max_restarts = self.max_restarts.value()
        sv.restart_window_s = self.window.value()
        sv.stall_timeout_s = self.stall.value()
        sv.stop_timeout_s = self.stop_timeout.value()
        ui.close_to_tray = self.close_to_tray.isChecked()
        ui.start_minimized = self.start_min.isChecked()
        ui.autorun_script = self.autorun.isChecked()
        ui.notify = self.notify.isChecked()
        ui.restart_on_change = self.apply_now.isChecked()
        ui.theme = self.theme_box.currentData()
        ui.text_size = self.text_size.currentData()
        ui.touch_mode = self.touch.isChecked()
        ui.console_max_lines = self.max_lines.value()
        self.s.hackrf.tools_dir = self.tools_dir.text().strip()
        self.s.python.mode = self.py_mode.currentData()
        self.s.python.interpreter = self.interp.text().strip()
        self._sync_visibility()
        self.changed.emit()

    def _set_login(self, on: bool) -> None:
        ok, msg = autostart.set_launch_at_login(on)
        self.s.ui.launch_at_login = on if ok else not on
        if not ok:
            QMessageBox.warning(self, "Launch at login", msg)
            self.login.blockSignals(True)
            self.login.setChecked(not on)
            self.login.blockSignals(False)
        self.changed.emit()

    def refresh_service(self) -> None:
        def done(status: Any) -> None:
            status = str(status)
            state = {"active": "running", "running": "running", "ready": "stopped",
                     "not installed": "stopped"}.get(status, "stopped" if "inactive" in status else "failed"
                                                     if "fail" in status else "stopped")
            self.svc_pill.set_state(state, f"Service: {status}")
            self.svc_remove.setEnabled(status != "not installed")
        run_in_thread(autostart.service_status, done, self)

    def _install_service(self) -> None:
        script = self.current_script()
        if not script:
            QMessageBox.information(self, "No script", "Select a script on the Control page first.")
            return
        ok, msg = autostart.install_service(script)
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Background service", msg)
        self.refresh_service()

    def _remove_service(self) -> None:
        ok, msg = autostart.uninstall_service()
        if not ok:
            QMessageBox.warning(self, "Background service", msg)
        self.refresh_service()

