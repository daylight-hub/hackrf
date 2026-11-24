import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RFSTUDIO_ALLOW_MULTIPLE", "1")

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture()
def window(qapp, tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path))
    from rfstudio import config
    from rfstudio.gui import theme
    from rfstudio.gui.main_window import MainWindow

    theme.apply(qapp, "dark")
    script = tmp_path / "demo.py"
    script.write_text(
        "# ===== Radio =====\nENABLE_TX = False  # transmit\nFREQ = 433.92e6\nMODE = 'fm'  # @options fm|am\n"
        "# [Output]\nVERBOSE = True\nimport time\nprint('ok')\n"
    )
    win = MainWindow(config.Settings(), str(script))
    yield win, script
    win.supervisor.stop()
    win.close()


def test_options_generated(window):
    win, _ = window
    names = [r.var.name for r in win.options.rows]
    assert names == ["ENABLE_TX", "FREQ", "MODE", "VERBOSE"]
    assert [c.title_label.text() for c, _ in win.options.cards] == ["RADIO", "OUTPUT"]


def test_toggle_persists_override(window):
    from rfstudio.gui.widgets import ToggleSwitch

    win, script = window
    row = next(r for r in win.options.rows if r.var.name == "ENABLE_TX")
    assert isinstance(row.editor, ToggleSwitch)
    row.editor.setChecked(True)
    assert win.settings.overrides_for(script) == {"ENABLE_TX": "True"}
    row.reset()
    assert win.settings.overrides_for(script) == {}


def test_filter_toggles(window):
    win, _ = window
    win.options.filter_buttons["bool"].setChecked(True)
    visible = [r.var.name for r in win.options.rows if not r.isHidden()]
    assert visible == ["ENABLE_TX", "VERBOSE"]
