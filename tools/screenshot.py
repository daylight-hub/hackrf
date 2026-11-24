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
    from rfstudio.gui.options_panel import OptionRow
    OptionRow._tx_confirmed = True
    win = MainWindow(settings, str(script))
    win.resize(1500, 950)
    win.show()
    suffix = "" if theme_name == "dark" else "-light"

    def load_scanner() -> None:
        # a copy in a temp folder, with some scan history, so nothing is written into the repo
        import json
        import random
        import shutil
        import time as _t
        folder = Path(_tmp) / "scanner"
        folder.mkdir(exist_ok=True)
        dst = folder / "frequency_scanner.py"
        shutil.copy2(ROOT / "examples" / "frequency_scanner.py", dst)
        rnd = random.Random(7)
        known = [315.0e6, 433.92e6, 868.35e6, 915.0e6, 462.5625e6]
        scans = [{"t": _t.time() - 60 * (40 - i), "hits": [(f, round(rnd.uniform(-70, -30), 1)) for f in known
                                                           if rnd.random() < 0.6]} for i in range(40)]
        (folder / "scan_data.json").write_text(json.dumps({"scans": scans, "blocked": [462.5625e6]}))
        win.supervisor.stop()
        win.load_script(dst)
        win.show_page("control")

    def shoot(name: str) -> None:
        app.processEvents()
        win.grab().save(str(out / f"{name}{suffix}.png"))
        print("saved", out / f"{name}{suffix}.png")

    steps: list[tuple[int, callable]] = []
    if run:
        steps.append((300, win.start_script))
    steps += [
        (4500 if run else 600, lambda: shoot("control")),
        (100, lambda: win.show_page("device")),
        (400, lambda: shoot("device")),
        (100, lambda: win.show_page("transfer")),
        (400, lambda: shoot("transfer")),
        (100, lambda: win.show_page("settings")),
        (400, lambda: shoot("settings")),
        (100, load_scanner),
        (900, lambda: shoot("control-scanner")),
        (100, lambda: (win.show_page("commands"), win.commands_page.run(["--history"], "History"))),
        (2500, lambda: win.commands_page.run(["--top", "5"], "Top")),
        (2500, lambda: shoot("commands")),
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
