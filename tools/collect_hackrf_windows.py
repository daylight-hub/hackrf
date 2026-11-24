"""Copy HackRF DLLs + tools from a conda-forge environment into vendor/hackrf/windows.

    python tools/collect_hackrf_windows.py <conda env prefix> [dest]

The conda-forge `hackrf` package ships hackrf.dll, hackrf_*.exe and their
dependencies (libusb, fftw, MSVC runtime). We copy all DLLs that sit next to
hackrf.dll plus the env-root runtime DLLs, so everything loads from one folder.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    prefix = Path(sys.argv[1])
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("vendor/hackrf/windows")
    dest.mkdir(parents=True, exist_ok=True)

    dlls = sorted(prefix.rglob("hackrf.dll"))
    if not dlls:
        print(f"error: hackrf.dll not found under {prefix}")
        return 1
    lib_dir = dlls[0].parent
    copied: list[str] = []
    for f in lib_dir.glob("*.dll"):
        shutil.copy2(f, dest / f.name)
        copied.append(f.name)
    for f in prefix.glob("*.dll"):  # vcruntime / msvcp from vc14_runtime
        if not (dest / f.name).exists():
            shutil.copy2(f, dest / f.name)
            copied.append(f.name)
    tools = sorted({p for p in prefix.rglob("hackrf_*.exe")})
    for t in tools:
        shutil.copy2(t, dest / t.name)
        copied.append(t.name)

    version = "unknown"
    for meta in (prefix / "conda-meta").glob("hackrf-*.json"):
        try:
            version = json.loads(meta.read_text())["version"]
        except (OSError, ValueError, KeyError):
            pass
    (dest / "VERSION.txt").write_text(f"hackrf {version} (conda-forge)\n", encoding="utf-8")
    print(f"hackrf {version}: copied {len(copied)} files to {dest}")
    for name in sorted(copied):
        print("  ", name)
    if not any(n.startswith("hackrf_transfer") for n in copied):
        print("warning: hackrf_transfer.exe not found in the package")
    return 0


if __name__ == "__main__":
    sys.exit(main())
