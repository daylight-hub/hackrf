"""Script failure handling: diagnosis, no-restart on config errors, working folder,
and end-to-end runs against a simulated libhackrf (tests/mock/mock_libhackrf.c)."""

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rfstudio import config, diagnostics, runner, session
from rfstudio.supervisor import LaunchSpec, State, Supervisor

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


# ----------------------------------------------------------------- diagnosis

@pytest.mark.parametrize("line,title,action", [
    ("RuntimeError: libusb error  (HACKRF_ERROR_LIBUSB)", "No permission", diagnostics.ACTION_DRIVERS),
    ("hackrf_open() failed: Access denied (insufficient permissions) (-1000)", "No permission",
     diagnostics.ACTION_DRIVERS),
    ("RuntimeError: Devicy busy (HACKRF_ERROR_BUSY)", "in use", diagnostics.ACTION_DEVICE),
    ("ValueError: No HackRF devices attached to host", "No HackRF found", diagnostics.ACTION_DEVICE),
    ("ModuleNotFoundError: No module named 'scipy'", "scipy", diagnostics.ACTION_SETTINGS),
    ("PermissionError: [Errno 13] Permission denied: 'captures'", "write", diagnostics.ACTION_FOLDER),
    ("OSError: libhackrf could not be loaded. Install", "library", diagnostics.ACTION_DRIVERS),
])
def test_diagnose(line, title, action):
    d = diagnostics.diagnose(["Traceback (most recent call last):", '  File "x.py", line 3', line], 1)
    assert d is not None and title in d.title and d.action == action
    assert d.error_line == line.strip()


def test_diagnose_generic():
    d = diagnostics.diagnose(["ZeroDivisionError: division by zero"], 1)
    assert d.title == "The script stopped with an error" and "ZeroDivision" in d.detail


# ------------------------------------------------- config errors: no restart

def test_missing_module_does_not_restart(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("import definitely_not_a_module\n")
    events = []
    sup = Supervisor(lambda: session.prepare_launch(config.Settings(), script),
                     config.SupervisorSettings(backoff_initial_s=0.1),
                     on_event=lambda lvl, m: events.append(m))
    sup.start()
    deadline = time.time() + 30
    while sup.is_active and time.time() < deadline:
        time.sleep(0.05)
    assert sup.state == State.FAILED
    assert sup.last_exit_code == runner.EXIT_CONFIG_ERROR
    assert sup.restart_count == 0
    assert any("Not restarting" in e for e in events)


def test_crash_still_restarts(tmp_path):
    sup = Supervisor(lambda: LaunchSpec([sys.executable, "-c", "raise SystemExit(2)"]),
                     config.SupervisorSettings(backoff_initial_s=0.05, max_restarts=2))
    sup.start()
    deadline = time.time() + 20
    while sup.is_active and time.time() < deadline:
        time.sleep(0.05)
    assert sup.restart_count == 2


# ------------------------------------------------------------ working folder

@pytest.mark.skipif(sys.platform.startswith("win") or os.geteuid() == 0,
                    reason="needs a non-root POSIX user to make a read-only folder")
def test_read_only_script_folder_uses_user_folder(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    script = ro / "writer.py"
    script.write_text("open('out.txt', 'w').write('hi')\nimport os\nprint(os.getcwd())\n")
    ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        wd = session.working_dir(script)
        assert wd == Path.home() / "RF Script Studio" / "writer"
        spec = session.prepare_launch(config.Settings(), script)
        out = subprocess.run(spec.argv, cwd=spec.cwd, env=spec.env, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert (wd / "out.txt").read_text() == "hi"
    finally:
        ro.chmod(stat.S_IRWXU)


def test_read_only_folder_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "_writable", lambda folder: False)
    script = tmp_path / "s.py"
    script.write_text("print(1)\n")
    assert session.working_dir(script) == Path.home() / "RF Script Studio" / "s"
    spec = session.prepare_launch(config.Settings(), script)
    assert spec.cwd == str(Path.home() / "RF Script Studio" / "s")
    assert any("Working folder" in i for i in spec.info)


# ------------------------------------------------------- simulated hardware

@pytest.fixture(scope="module")
def mock_lib(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc or not sys.platform.startswith("linux"):
        pytest.skip("needs a C compiler on Linux")
    out = tmp_path_factory.mktemp("mockhrf")
    subprocess.run([cc, "-shared", "-fPIC", "-pthread", "-O2", "-o", str(out / "libhackrf.so.0"),
                    str(ROOT / "tests" / "mock" / "mock_libhackrf.c")], check=True)
    pytest.importorskip("pyhackrf2", reason="pyhackrf2 not installed")
    return out


def run_with_mock(mock_lib, tmp_path, code, **env):
    script = tmp_path / "hw.py"
    script.write_text(code)
    spec = session.prepare_launch(config.Settings(), script)
    e = dict(spec.env)
    e["LD_LIBRARY_PATH"] = f"{mock_lib}:{e.get('LD_LIBRARY_PATH', '')}"
    e.update(env)
    return subprocess.run(spec.argv, cwd=spec.cwd, env=e, capture_output=True, text=True, timeout=120)


def test_pyhackrf2_read_samples_fixed(mock_lib, tmp_path):
    out = run_with_mock(mock_lib, tmp_path, (
        "import numpy as np\nfrom pyhackrf2 import HackRF\n"
        "s = HackRF(); s.sample_rate = 8e6; s.center_freq = 433.92e6; s.lna_gain = 24\n"
        "x = s.read_samples(65536)\n"
        "print('N', len(x), x.dtype, 'MAX', float(np.abs(x.real).max()) < 1.0, 'LNA', s.lna_gain)\n"
        "print('SERIAL', s.get_serial_no())\n"))
    assert out.returncode == 0, out.stderr
    assert "N 65536 complex128 MAX True LNA 24" in out.stdout
    assert "SERIAL 0000000000000000" in out.stdout


@pytest.mark.parametrize("env,expected", [
    ({"MOCK_HACKRF_OPEN": "-1000"}, "No permission"),
    ({"MOCK_HACKRF_OPEN": "-6"}, "in use"),
    ({"MOCK_HACKRF_COUNT": "0"}, "No HackRF found"),
])
def test_hardware_errors_are_diagnosed(mock_lib, tmp_path, env, expected):
    out = run_with_mock(mock_lib, tmp_path, "from pyhackrf2 import HackRF\nHackRF()\n", **env)
    assert out.returncode == 1
    d = diagnostics.diagnose(out.stderr.splitlines(), out.returncode)
    assert expected in d.title


def test_example_runs_on_simulated_hardware(mock_lib, tmp_path):
    src = (ROOT / "examples" / "spectrum_monitor.py").read_text()
    src = src.replace("SIMULATE = True ", "SIMULATE = False").replace("RUN_FOREVER = True ", "RUN_FOREVER = False")
    src = src.replace("MAX_REPORTS = 60 ", "MAX_REPORTS = 2  ").replace("REPORT_INTERVAL = 1.0 ", "REPORT_INTERVAL = 0.0 ")
    out = run_with_mock(mock_lib, tmp_path, src)
    assert out.returncode == 0, out.stderr
    assert "HackRF opened" in out.stdout and "(hardware)" in out.stdout


def test_read_only_folder_runs_from_writable_copy(tmp_path, monkeypatch):
    """Mirrors a real report: BACKUP_DIR = Path(__file__).parent / 'frequency_backups' in /opt."""
    monkeypatch.setattr(session, "_writable", lambda folder: False)
    bundled = tmp_path / "opt" / "scripts"
    bundled.mkdir(parents=True)
    (bundled / "helper.py").write_text("VALUE = 7\n")
    (bundled / "presets.json").write_text('{"f": 433}')
    script = bundled / "tx_app.py"
    script.write_text(
        "import json\nfrom pathlib import Path\nimport helper\n"
        "BACKUP_DIR = Path(__file__).parent / 'frequency_backups'\n"
        "BACKUP_DIR.mkdir(exist_ok=True)\n"
        "(BACKUP_DIR / 'b.txt').write_text('saved')\n"
        "print('preset', json.loads((Path(__file__).parent / 'presets.json').read_text())['f'], helper.VALUE)\n"
        "(Path(__file__).parent / 'presets.json').write_text('{\"f\": 915}')\n"
    )
    workdir = Path.home() / "RF Script Studio" / "tx_app"
    for expected in ("preset 433 7", "preset 915 7"):  # 2nd run keeps the script's own edits
        spec = session.prepare_launch(config.Settings(), script)
        out = subprocess.run(spec.argv, cwd=spec.cwd, env=spec.env, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert expected in out.stdout
    assert (workdir / "frequency_backups" / "b.txt").read_text() == "saved"
    assert not (bundled / "frequency_backups").exists()
    # an app update to the script itself is picked up
    script.write_text(script.read_text().replace("print('preset'", "print('v2 preset'"))
    spec = session.prepare_launch(config.Settings(), script)
    out = subprocess.run(spec.argv, cwd=spec.cwd, env=spec.env, capture_output=True, text=True, timeout=60)
    assert "v2 preset 915 7" in out.stdout


def test_traceback_starts_at_user_script(tmp_path):
    script = tmp_path / "boom2.py"
    script.write_text("def f():\n    raise PermissionError('denied')\n\nf()\n")
    spec = session.prepare_launch(config.Settings(), script)
    out = subprocess.run(spec.argv, cwd=spec.cwd, env=spec.env, capture_output=True, text=True, timeout=60)
    lines = out.stderr.strip().splitlines()
    assert lines[0] == "Traceback (most recent call last):"
    assert str(script) in lines[1] and "runner.py" not in out.stderr


@pytest.mark.parametrize("how", ["token", "eof"])
def test_stop_pipe_runs_script_cleanup(tmp_path, how):
    """Windows-style stop: token on stdin (or the app vanishing = EOF) -> KeyboardInterrupt."""
    script = tmp_path / "tx.py"
    script.write_text("import time\nprint('transmitting', flush=True)\n"
                      "try:\n    time.sleep(60)\nfinally:\n    print('TX OFF - cleanup ran', flush=True)\n")
    spec = session.prepare_launch(config.Settings(), script)
    env = dict(spec.env, RFSTUDIO_STOP_PIPE="1")
    proc = subprocess.Popen(spec.argv, cwd=spec.cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout.readline().strip() == b"transmitting"
    start = time.time()
    if how == "token":
        proc.stdin.write(runner.STOP_TOKEN + b"\n")
        proc.stdin.flush()
    proc.stdin.close()
    # (not communicate(): on Python <= 3.12 it flushes the already-closed stdin)
    try:
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    out, err = proc.stdout.read(), proc.stderr.read()
    assert b"TX OFF - cleanup ran" in out, err
    assert proc.returncode == 130
    assert time.time() - start < 10  # did not wait for the 60 s sleep


def test_frozen_runner_finds_bundled_hackrf_dir(tmp_path, monkeypatch):
    bundle = tmp_path / "_internal"
    (bundle / "hackrf").mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.delenv("RFSTUDIO_HACKRF_DIR", raising=False)
    assert runner._search_dirs(None) == [str(bundle / "hackrf")]
    monkeypatch.setattr(sys, "platform", "win32")
    cands = runner._library_candidates(None)
    assert cands[0] == str(bundle / "hackrf" / "hackrf-0.dll")


# ------------------------------------------------- python-compatible executable

def _cli(args, **env):
    e = dict(os.environ, PYTHONPATH=str(ROOT / "src"), **env)
    return subprocess.run([sys.executable, "-m", "rfstudio", *args], capture_output=True, text=True,
                          timeout=60, env=e)


def test_pip_install_answered_from_bundle():
    out = _cli(["-m", "pip", "install", "--user", "-q", "numpy>=1.20", "scipy"])
    assert out.returncode == 0, out.stderr
    assert "already satisfied: numpy" in out.stdout and "scipy" in out.stdout
    out = _cli(["-m", "pip", "install", "numpy", "definitely-not-a-package"])
    assert out.returncode == 1 and "definitely-not-a-package" in out.stderr
    assert "requirements.txt" in out.stderr


def test_script_restart_via_executable_keeps_options(tmp_path):
    origin = tmp_path / "app.py"
    origin.write_text("FLAG = False\n")
    patched = tmp_path / "patched.py"
    patched.write_text("import sys\nFLAG = True\nprint('FLAG', FLAG, sys.argv[1:], __file__.endswith('app.py'))\n")
    out = _cli([str(origin), "--scan"], RFSTUDIO_SCRIPT=str(origin), RFSTUDIO_PATCHED_SCRIPT=str(patched))
    assert out.returncode == 0, out.stderr
    assert "FLAG True ['--scan'] True" in out.stdout


def test_missing_package_stops_restart_loop(tmp_path):
    script = tmp_path / "needs.py"
    script.write_text("import sys\ntry:\n    import notarealpkg\nexcept ImportError:\n"
                      "    print('Missing dependencies. Please run: pip install numpy notarealpkg')\n"
                      "    sys.exit(1)\n")
    events = []
    sup = Supervisor(lambda: session.prepare_launch(config.Settings(), script),
                     config.SupervisorSettings(backoff_initial_s=0.1),
                     on_event=lambda lvl, m: events.append(m), fatal_check=diagnostics.fatal_reason)
    sup.start()
    deadline = time.time() + 30
    while sup.is_active and time.time() < deadline:
        time.sleep(0.05)
    assert sup.state == State.FAILED and sup.restart_count == 0
    assert any("notarealpkg" in e and "Not restarting" in e for e in events)
    d = diagnostics.diagnose(["Please run: pip install numpy notarealpkg"], 1)
    assert "notarealpkg" in d.title and "notarealpkg" in d.detail and "numpy" not in d.detail.split(":")[1]


def test_long_running_crash_still_restarts():
    assert diagnostics.fatal_reason(["pip install foo"], 120.0, 1) is None


def test_windows_style_exec_waits_for_restarted_copy(tmp_path):
    """os.execv emulation: parent waits for the new copy and exits with its code."""
    script = tmp_path / "re.py"
    script.write_text("import os, sys\nprint('run', os.environ.get('N', '0'), flush=True)\n"
                      "if not os.environ.get('N'):\n    os.environ['N'] = '1'\n"
                      "    os.execv(sys.executable, [sys.executable, __file__])\nsys.exit(3)\n")
    spec = session.prepare_launch(config.Settings(), script)
    env = dict(spec.env, RFSTUDIO_EXEC_COMPAT="1")
    out = subprocess.run(spec.argv, cwd=spec.cwd, env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.split() == ["run", "0", "run", "1"], out.stderr
    assert out.returncode == 3
