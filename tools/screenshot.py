"""Render the app offscreen and save screenshots of each page (used for docs + CI smoke test).

    QT_QPA_PLATFORM=offscreen python tools/screenshot.py [out_dir] [--run]
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RFSTUDIO_ALLOW_MULTIPLE", "1")
_tmp = tempfile.mkdtemp(prefix="rfstudio-shot-")
for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA"):
    os.environ[var] = _tmp

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rfstudio import config  # noqa: E402
from rfstudio.gui import theme  # noqa: E402
from rfstudio.gui.main_window import MainWindow  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "screenshots")
    out.mkdir(parents=True, exist_ok=True)
    run = "--run" in sys.argv
    theme_name = "light" if "--light" in sys.argv else "dark"
    app = QApplication(sys.argv[:1])
    settings = config.Settings()
    settings.ui.theme = theme_name
    settings.ui.notify = False
    theme.apply(app, theme_name)
    script = ROOT / "examples" / "spectrum_monitor.py"
    settings.overrides_for(script).update({"ENABLE_AMP": "True", "AVERAGE_BLOCKS": "2"})
    win = MainWindow(settings, str(script))
    win.resize(1500, 950)
    win.show()
    suffix = "" if theme_name == "dark" else "-light"

    def shoot(name: str) -> None:
        app.processEvents()
        win.grab().save(str(out / f"{name}{suffix}.png"))
        print("saved", out / f"{name}{suffix}.png")

    steps: list[tuple[int, callable]] = []
    if run:
        steps.append((300, win.start_script))
    steps += [
        (4500 if run else 600, lambda: shoot("control")),
        (100, lambda: win.stack.setCurrentIndex(1)),
        (400, lambda: shoot("device")),
        (100, lambda: win.stack.setCurrentIndex(2)),
        (400, lambda: shoot("transfer")),
        (100, lambda: win.stack.setCurrentIndex(3)),
        (400, lambda: shoot("settings")),
        (100, win.quit_app),
    ]
    def run_step(i: int = 0) -> None:
        if i >= len(steps):
            return
        d, fn = steps[i]

        def fire() -> None:
            fn()
            run_step(i + 1)

        QTimer.singleShot(d, fire)

    run_step()
    QTimer.singleShot(60000, app.quit)  # safety net
    win.isVisible = lambda: False  # type: ignore[method-assign]  # no "quit?" prompt
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
