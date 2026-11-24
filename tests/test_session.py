import subprocess
import sys

import pytest

from rfstudio import config, hackrf_backend, runner, session


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))


def test_prepare_launch_runs_with_overrides(tmp_path):
    script = tmp_path / "demo.py"
    script.write_text(
        "import sys\nFLAG = False  # flag\nFREQ = 1e6\n"
        "print('FLAG', FLAG, 'FREQ', FREQ, 'file', __file__.endswith('demo.py'), 'args', sys.argv[1:])\n"
    )
    s = config.Settings()
    s.overrides_for(script).update({"FLAG": "True", "FREQ": "915000000.0", "OLD": "1"})
    s.script_args[s.key(script)] = "--alpha 1"
    spec = session.prepare_launch(s, script)
    out = subprocess.run(spec.argv, cwd=spec.cwd, env=spec.env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "FLAG True FREQ 915000000.0 file True args ['--alpha', '1']" in out.stdout
    assert any("FLAG=True" in i for i in spec.info)


def test_traceback_points_at_original(tmp_path):
    script = tmp_path / "boom.py"
    script.write_text("X = 1\n\nraise RuntimeError('kaboom')\n")
    spec = session.prepare_launch(config.Settings(), script)
    out = subprocess.run(spec.argv, cwd=spec.cwd, env=spec.env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 1
    assert str(script) in out.stderr and "line 3" in out.stderr


def test_missing_script(tmp_path):
    with pytest.raises(FileNotFoundError):
        session.prepare_launch(config.Settings(), tmp_path / "nope.py")


def test_settings_roundtrip(tmp_path):
    s = config.Settings()
    s.supervisor.max_restarts = 7
    s.ui.theme = "light"
    s.remember_script(tmp_path / "a.py")
    p = tmp_path / "settings.json"
    config.save(s, p)
    loaded = config.load(p)
    assert loaded.supervisor.max_restarts == 7 and loaded.ui.theme == "light"
    assert loaded.recent_scripts == [str((tmp_path / "a.py").resolve())]
    p.write_text("{not json")
    assert config.load(p).supervisor.max_restarts == 0


def test_hackrf_compat_redirects(monkeypatch):
    import ctypes
    # Map the alias onto libc to prove the redirect works without a HackRF installed
    monkeypatch.setattr(runner, "_installed", False)
    libc = "msvcrt" if sys.platform.startswith("win") else "libc.so.6"
    monkeypatch.setattr(runner, "_library_candidates", lambda d: ["/definitely/missing.so", libc])
    original = ctypes.CDLL.__init__
    try:
        runner.install_hackrf_compat()
        lib = ctypes.CDLL("libhackrf.so.0")
        assert runner.loaded_library == libc
        assert lib is not None
    finally:
        ctypes.CDLL.__init__ = original


def test_parse_hackrf_info():
    text = """hackrf_info version: 2024.02.1
libhackrf version: 2024.02.1 (0.9)
Found HackRF
Index: 0
Serial number: 0000000000000000457863c8234e1d2f
Board ID Number: 2 (HackRF One)
Firmware Version: 2024.02.1 (API:1.08)
Part ID Number: 0xa000cb3c 0x00574f60
Hardware Revision: r9
"""
    devs, tv, lv = hackrf_backend.parse_hackrf_info(text)
    assert tv == "2024.02.1" and lv.startswith("2024.02.1")
    assert len(devs) == 1 and devs[0].board == "HackRF One" and devs[0].hardware_revision == "r9"


def test_transfer_command():
    p = hackrf_backend.TransferParams(mode="rx", file="out.cs8", frequency_hz=433.92e6,
                                      sample_rate_hz=10e6, lna_gain=19, vga_gain=21, num_samples=1000)
    cmd = hackrf_backend.build_transfer_command("hackrf_transfer", p)
    assert cmd[:3] == ["hackrf_transfer", "-r", "out.cs8"]
    assert ["-l", "16"] == cmd[cmd.index("-l"):cmd.index("-l") + 2]
    assert ["-g", "20"] == cmd[cmd.index("-g"):cmd.index("-g") + 2]
    p.mode, p.repeat = "tx", True
    assert "-R" in hackrf_backend.build_transfer_command("hackrf_transfer", p)
    p.frequency_hz = 10
    with pytest.raises(ValueError):
        hackrf_backend.build_transfer_command("hackrf_transfer", p)


def test_probe_cli_runs():
    res = hackrf_backend.probe([])
    assert isinstance(res, hackrf_backend.ProbeResult)
    assert not any("Probe failed" in e for e in res.errors), res.errors
