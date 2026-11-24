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
            "import sys, numpy\n"
            "FLAG = False\n"
            "try:\n"
            "    from pyhackrf2 import HackRF\n"
            "    print('ENUM', HackRF.enumerate())\n"
            "except Exception as exc:\n"
            "    print('PYHACKRF2_ERROR', exc)\n"
            "print('FLAG', FLAG, 'numpy', numpy.__version__)\n", encoding="utf-8")
        res = run([str(exe), "--run-script", "--script", str(script), "--origin", str(script)], cwd=tmp)
        if "FLAG False" not in res.stdout:
            failures.append("bundled runner could not execute a script")
        if expect_hackrf and "ENUM" not in res.stdout:
            failures.append("pyhackrf2 could not enumerate devices inside the bundled runner")

    if failures:
        print("\nSMOKE TEST FAILED:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nSmoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
