"""Smoke-test a frozen build (run by CI after PyInstaller).

    python tools/smoke_test.py dist/RFScriptStudio [--expect-hackrf]

Checks that the CLI starts, script discovery works, the HackRF probe runs, and
that a user script can `import pyhackrf2` and enumerate devices through the
bundled runner (this exercises the libhackrf loading shim on Windows).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd), flush=True)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)
    print(res.stdout[-3000:])
    if res.stderr.strip():
        print("stderr:", res.stderr[-3000:])
    return res


def main() -> int:
    app = Path(sys.argv[1]).resolve()
    expect_hackrf = "--expect-hackrf" in sys.argv
    exe = app / ("rfstudio-cli.exe" if os.name == "nt" else "rfstudio-cli")
    internal = app / "_internal"
    failures: list[str] = []

    if run([str(exe), "--version"]).returncode != 0:
        failures.append("--version failed")

    example = internal / "examples" / "spectrum_monitor.py"
    res = run([str(exe), "--list-options", str(example)])
    if res.returncode != 0 or "CENTER_FREQ" not in res.stdout:
        failures.append("--list-options did not discover variables")

    res = run([str(exe), "--probe", "--json"])
    try:
        probe = json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        probe = {}
        failures.append("--probe did not return JSON")
    if expect_hackrf and probe:
        if not probe.get("pyhackrf2_available"):
            failures.append(f"pyhackrf2/libhackrf not loadable: {probe.get('pyhackrf2_error')}")
        if not probe.get("hackrf_transfer"):
            failures.append("hackrf_transfer not found")

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "probe_script.py"
        script.write_text(
            "import sys, numpy, scipy\nfrom scipy import signal\n"
            "FLAG = False\n"
            "try:\n"
            "    from pyhackrf2 import HackRF\n"
            "    print('ENUM', HackRF.enumerate())\n"
            "except Exception as exc:\n"
            "    print('PYHACKRF2_ERROR', exc)\n"
            "print('FLAG', FLAG, 'numpy', numpy.__version__)\n", encoding="utf-8")
        res = run([str(exe), "--run-script", "--script", str(script), "--origin", str(script)], cwd=tmp)
        if "FLAG False" not in res.stdout:
            failures.append("bundled runner could not execute a script (numpy/scipy/pyhackrf2)")

    # Child processes, as a continuous transmitter would use them: a multiprocessing worker
    # (spawn = how Windows always works) that loads pyhackrf2 and must see the app's option
    # values, plus a helper started with subprocess - both must print into our output.
    with tempfile.TemporaryDirectory() as tmp:
        origin = Path(tmp) / "mp_app.py"
        body = ("import multiprocessing as mp, os, subprocess, sys\n"
                "FLAG = {flag}\n"
                "def worker(q):\n"
                "    import pyhackrf2\n"
                "    print('worker FLAG', FLAG, os.path.basename(__file__), flush=True)\n"
                "    q.put(FLAG)\n"
                "if __name__ == '__main__':\n"
                "    ctx = mp.get_context('spawn')\n"
                "    q = ctx.Queue(); p = ctx.Process(target=worker, args=(q,)); p.start()\n"
                "    print('from worker', q.get(timeout=90), flush=True); p.join(60)\n"
                "    subprocess.run([sys.executable, '-c', 'print(\"helper child ok\")'])\n")
        origin.write_text(body.format(flag="False"), encoding="utf-8")
        patched = Path(tmp) / "patched.py"
        patched.write_text(body.format(flag="True"), encoding="utf-8")
        res = run([str(exe), "--run-script", "--script", str(patched), "--origin", str(origin)], cwd=tmp)
        for expect in ("worker FLAG True mp_app.py", "from worker True", "helper child ok"):
            if expect not in res.stdout:
                failures.append(f"child processes: missing '{expect}' in output")

    # The real start-up path of the desktop app: the supervisor (on Windows: hidden console,
    # Stop/Dump events, job object). Output must arrive, "where is it" must work, Stop must
    # let the script clean up.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from rfstudio.config import SupervisorSettings
    from rfstudio.supervisor import LaunchSpec, Supervisor

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "tx_app.py"
        script.write_text(
            "import time\nimport numpy, scipy.signal\nfrom pyhackrf2 import HackRF\n"
            "print('devices', HackRF.enumerate(), flush=True)\n"
            "print('═══ SDR ✓ é ═══')\n"
            "def wait_for_radio():\n    time.sleep(120)\n"
            "try:\n    wait_for_radio()\nfinally:\n    print('TX OFF - cleanup ran', flush=True)\n",
            encoding="utf-8")
        got: list[str] = []
        sup = Supervisor(lambda: LaunchSpec([str(exe), "--run-script", "--script", str(script),
                                             "--origin", str(script)], cwd=tmp),
                         SupervisorSettings(stop_timeout_s=15), on_output=lambda line, st: got.append(line))

        def wait_for(pred, secs):
            end = time.time() + secs
            while time.time() < end:
                if pred():
                    return True
                time.sleep(0.1)
            return False

        print("$ supervised run of", script.name, flush=True)
        sup.start()
        if not wait_for(lambda: any(g.startswith("devices") for g in got), 90):
            failures.append(f"supervised run: no output from the script (got {got[:5]})")
        if not wait_for(lambda: "═══ SDR ✓ é ═══" in got, 10):
            failures.append(f"supervised run: text encoding wrong (got {got[-3:]})")
        sup.dump_stacks()
        if not wait_for(lambda: any("wait_for_radio" in g for g in got), 20):
            failures.append("supervised run: 'show what it is doing' produced no stack")
        sup.stop(wait=True)
        if "TX OFF - cleanup ran" not in got:
            failures.append("supervised run: Stop did not let the script run its cleanup")
        print("\n".join("   | " + g for g in got[:12]), flush=True)

    res = run([str(exe), "-m", "pip", "install", "numpy", "scipy"])
    if res.returncode != 0 or "already satisfied: scipy" not in res.stdout:
        failures.append("pip emulation does not report bundled numpy/scipy")
        if expect_hackrf and "ENUM" not in res.stdout:
            failures.append("pyhackrf2 could not enumerate devices inside the bundled runner")

    mock = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--mock-lib=")), None)
    if mock:
        # Run the pyhackrf2 example in hardware mode against the simulated libhackrf
        with tempfile.TemporaryDirectory() as tmp:
            src = (internal / "examples" / "spectrum_monitor.py").read_text(encoding="utf-8")
            for a, b in (("SIMULATE = True ", "SIMULATE = False"), ("RUN_FOREVER = True ", "RUN_FOREVER = False"),
                         ("MAX_REPORTS = 60 ", "MAX_REPORTS = 2  "), ("REPORT_INTERVAL = 1.0 ", "REPORT_INTERVAL = 0.0 ")):
                src = src.replace(a, b)
            script = Path(tmp) / "hw_monitor.py"
            script.write_text(src, encoding="utf-8")
            env = dict(os.environ, LD_LIBRARY_PATH=mock)
            res = run([str(exe), "--run-script", "--script", str(script), "--origin", str(script)], cwd=tmp, env=env)
            if res.returncode != 0 or "(hardware)" not in res.stdout or "power" not in res.stdout:
                failures.append("example failed against the simulated HackRF")

    # The desktop executable must start (Qt plugins, icons, theme...) and keep running.
    gui = app / ("rfscriptstudio.exe" if os.name == "nt" else "rfscriptstudio")
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", RFSTUDIO_ALLOW_MULTIPLE="1",
                   APPDATA=tmp, LOCALAPPDATA=tmp, XDG_CONFIG_HOME=tmp, XDG_STATE_HOME=tmp)
        print("$", gui, "(offscreen, 10 s)", flush=True)
        proc = subprocess.Popen([str(gui)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            out, _ = proc.communicate(timeout=10)
            failures.append(f"desktop app exited early with code {proc.returncode}: "
                            f"{out.decode(errors='replace')[-1500:]}")
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            print("desktop app is running - OK")

    if failures:
        print("\nSMOKE TEST FAILED:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nSmoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
