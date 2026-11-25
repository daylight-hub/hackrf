import subprocess
import textwrap
from pathlib import Path

from rfstudio import config, report_format, script_model, session

ROOT = Path(__file__).resolve().parents[1]


def spec_of(tmp_path, src):
    p = tmp_path / "s.py"
    p.write_text(textwrap.dedent(src))
    return script_model.parse_script(p)


# ------------------------------------------------------------------ commands

def test_argparse_commands(tmp_path):
    spec = spec_of(tmp_path, '''
        import argparse
        def main():
            p = argparse.ArgumentParser()
            p.add_argument("--scan", action="store_true", help="Run the scanner")
            p.add_argument("--history", action="store_true", help="Show history")
            p.add_argument("-t", "--top", type=int, nargs="?", const=10, metavar="N")
            p.add_argument("--block-freq", type=float, help="Block a frequency")
            p.add_argument("--secret", action="store_true", help=argparse.SUPPRESS)
            p.add_argument("input")  # positional: not a command
    ''')
    cmds = spec.commands
    assert [m.flag for m in cmds.modes] == ["--scan"]
    assert [r.flag for r in cmds.reports] == ["--history", "--top", "--block-freq"]
    top = cmds.by_flag()["--top"]
    assert top.takes_value and top.optional_value and top.metavar == "N" and top.argv("5") == ["--top", "5"]
    assert top.argv("") == ["--top"]
    assert cmds.by_flag()["--block-freq"].label == "Block freq"


def test_sys_argv_commands(tmp_path):
    spec = spec_of(tmp_path, '''
        import sys
        args = sys.argv[1:]
        if "--scan" in sys.argv:
            pass
        elif "--persistent" in args:
            pass
        elif len(sys.argv) > 1 and sys.argv[1] == "--pairs":
            pass
        x = "--not-a-flag-check"
    ''')
    flags = {c.flag: c.kind for c in spec.commands.commands}
    assert flags == {"--scan": "mode", "--persistent": "report", "--pairs": "report"}


def test_start_mode_default_and_launch_args(tmp_path):
    script = ROOT / "examples" / "frequency_scanner.py"
    s = config.Settings()
    spec = script_model.parse_script(script)
    assert session.start_mode_for(s, script, spec) == ""  # never guessed
    launch = session.prepare_launch(s, script, args=["--top", "3"])
    assert launch.argv[-3:] == ["--", "--top", "3"]


def test_report_command_runs_and_formats(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    src = (ROOT / "examples" / "frequency_scanner.py").read_text()
    script = tmp_path / "scanner" / "frequency_scanner.py"
    script.parent.mkdir()
    script.write_text(src)
    (script.parent / "scan_data.json").write_text(
        '{"scans": [{"t": 0, "hits": [[433920000.0, -40.0], [315000000.0, -50.0]]},'
        ' {"t": 1, "hits": [[433920000.0, -41.0]]}], "blocked": []}')
    spec = session.prepare_launch(config.Settings(), script, args=["--top", "5"])
    out = subprocess.run(spec.argv, cwd=spec.cwd, env=spec.env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    blocks = report_format.parse(out.stdout)
    assert blocks[0].kind == "title" and blocks[0].text == "Top 5 frequencies"
    assert blocks[1].kind == "table" and blocks[1].headers[:2] == ["Rank", "Frequency (MHz)"]
    assert blocks[1].rows[0][1] == "433.9200"


# -------------------------------------------------------------------- format

def test_report_format_variants():
    b = report_format.parse("HISTORY\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n")
    assert [x.kind for x in b] == ["title", "table"] and b[1].headers == ["A", "B"] and b[1].rows == [["1", "2"],
                                                                                                     ["3", "4"]]
    b = report_format.parse("Signals\n-------\n433.92  12 hits\n315     7 hits\nDone.")
    assert [x.kind for x in b] == ["title", "table", "text"]
    b = report_format.parse("Total scans: 10\nUnique: 4\n")
    assert b[0].kind == "kv" and b[0].rows == [["Total scans", "10"], ["Unique", "4"]]
    b = report_format.parse("\x1b[32mok\x1b[0m done")
    assert b[0].kind == "text" and b[0].text == "ok done"
    assert sorted(["1,200 MHz", "433 MHz", "x"], key=report_format.sort_key) == ["433 MHz", "1,200 MHz", "x"]


# -------------------------------------------------------------------- layout

def test_layout_groups_scripts_without_headers():
    spec = script_model.parse_script(ROOT / "examples" / "frequency_scanner.py")
    by = spec.by_name()
    assert by["START_FREQ"].section == "Radio" and by["START_FREQ"].unit == "Hz"
    assert by["LNA_GAIN"].section == "Receive" and (by["LNA_GAIN"].minimum, by["LNA_GAIN"].maximum,
                                                     by["LNA_GAIN"].step) == (0, 40, 8)
    assert by["THRESHOLD_DB"].section == "Detection" and by["THRESHOLD_DB"].unit == "dB"
    assert by["SCAN_INTERVAL_S"].section == "Timing" and by["SCAN_INTERVAL_S"].unit == "s"
    assert by["KNOWN"].section == "Advanced"
    assert spec.sections[0] == "Radio" and spec.sections[-1] == "Advanced"
    assert script_model.value_to_text(by["START_FREQ"], 433.92e6) == "433.92 MHz"
    assert by["START_FREQ"].coerce("433.92 MHz") == 433920000.0


def test_layout_respects_headers_and_flags_tx(tmp_path):
    spec = spec_of(tmp_path, '''
        # ===== RADIO SETTINGS =====
        TX_ENABLED = False
        FREQ = 915e6
        # [Output]
        VERBOSE = True
        DRY_RUN_TX = False
    ''')
    assert spec.sections == ["Radio settings", "Output"]
    assert spec.by_name()["TX_ENABLED"].danger and not spec.by_name()["DRY_RUN_TX"].danger


def test_settings_migration_and_defaults(tmp_path):
    import json

    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"supervisor": {"restart_policy": "always"}, "start_mode": {"a": "--scan", "b": ""}}))
    s = config.load(p)
    assert s.supervisor.restart_policy == config.RESTART_ON_FAILURE
    assert s.start_mode == {"a": "--scan"} and s.settings_version == config.SETTINGS_VERSION
    p.write_text(json.dumps({"settings_version": 2, "supervisor": {"restart_policy": "always"}}))
    assert config.load(p).supervisor.restart_policy == "always"  # a deliberate choice is kept
    assert config.Settings().supervisor.restart_policy == config.RESTART_ON_FAILURE


def test_start_mode_only_when_chosen():
    script = ROOT / "examples" / "frequency_scanner.py"
    s = config.Settings()
    spec = script_model.parse_script(script)
    assert session.start_mode_for(s, script, spec) == ""
    s.start_mode[s.key(script)] = "--scan"
    assert session.start_mode_for(s, script, spec) == "--scan"


def test_finished_script_is_not_restarted(tmp_path):
    import time as _t

    from rfstudio.supervisor import State, Supervisor

    script = tmp_path / "once.py"
    script.write_text("print('Results written to: out.json')\n")
    events = []
    sup = Supervisor(lambda: session.prepare_launch(config.Settings(), script), config.Settings().supervisor,
                     on_event=lambda lvl, m: events.append(lvl))
    sup.start()
    deadline = _t.time() + 30
    while sup.is_active and _t.time() < deadline:
        _t.sleep(0.05)
    assert sup.state == State.STOPPED and sup.restart_count == 0 and "success" in events


def test_result_files_rendered(tmp_path):
    import json

    folder = tmp_path / "RF Script Studio" / "app"
    folder.mkdir(parents=True)
    (folder / "frequencies.json").write_text(json.dumps(
        {"count": 2, "frequencies": [{"freq_hz": 433920000.0, "power_db": -42.1}, {"freq_hz": 315e6, "power_db": -55}]}))
    lines = [f"    Results written to: {folder / 'frequencies.json'}"]
    files = report_format.find_output_files(lines, None)
    assert files == [str(folder / "frequencies.json")]
    blocks = report_format.file_blocks(files[0])
    table = next(b for b in blocks if b.kind == "table")
    assert table.headers == ["freq_hz", "power_db"] and table.rows[0] == ["433920000", "-42.1"]
    assert report_format.find_output_files(["saved frequencies.json"], str(folder)) == files
