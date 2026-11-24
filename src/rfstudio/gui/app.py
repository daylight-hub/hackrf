"""QApplication bootstrap: single instance, theme, tray-only startup."""

from __future__ import annotations

import getpass
import os
import signal
import sys
from argparse import Namespace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .. import APP_ID, APP_NAME, ORG_NAME, config


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"{APP_ID}-{user}"


def _activate_existing(message: bytes) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(_server_name())
    if sock.waitForConnected(400):
        sock.write(message)
        sock.flush()
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return True
    return False


def run_gui(args: Namespace) -> int:
    if sys.platform.startswith("win"):
        try:  # own taskbar group + icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{ORG_NAME}.{APP_ID}")
        except (AttributeError, OSError):
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setDesktopFileName(APP_ID)
    app.setQuitOnLastWindowClosed(False)

    if not os.environ.get("RFSTUDIO_ALLOW_MULTIPLE") and _activate_existing(b"show\n"):
        return 0  # an instance is already running; it brings itself to front

    server = QLocalServer()
    QLocalServer.removeServer(_server_name())
    server.listen(_server_name())

    from . import theme
    settings = config.load()
    theme.apply(app, settings.ui.theme, settings.ui.text_size)
    from .widgets import set_touch_mode

    set_touch_mode(settings.ui.touch_mode)

    from .main_window import MainWindow, app_icon
    app.setWindowIcon(app_icon())
    win = MainWindow(settings, args.script)

    def on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.waitForReadyRead(300)
            conn.readAll()
            win.show_window()
            conn.disconnectFromServer()

    server.newConnection.connect(on_connection)

    minimized = args.minimized or settings.ui.start_minimized
    if minimized and QSystemTrayIcon.isSystemTrayAvailable():
        pass  # tray only
    elif minimized:
        win.showMinimized()
    else:
        win.show()

    # SIGTERM/SIGINT (logout, `kill`, systemd) -> stop the script cleanly, then exit.
    def _terminate(*_a: object) -> None:
        win.isVisible = lambda: False  # type: ignore[method-assign]  # skip the "quit?" prompt
        QTimer.singleShot(0, win.quit_app)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _terminate)
        except (ValueError, OSError):
            pass
    pump = QTimer()
    pump.timeout.connect(lambda: None)  # lets Python run signal handlers while Qt is idle
    pump.start(500)

    if args.autorun or settings.ui.autorun_script:
        QTimer.singleShot(300, win.start_script)

    code = app.exec()
    server.close()
    return code
