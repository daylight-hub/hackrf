"""Copy HackRF DLLs + tools from a conda-forge environment into vendor/hackrf/windows.

    python tools/collect_hackrf_windows.py <conda env prefix> [dest]

On conda-forge the Windows build is split into packages:
  hackrf      -> Library/bin/hackrf_*.exe (tools)
  libhackrf0  -> Library/bin/hackrf-0.dll (the library pyhackrf2 loads)
  libusb, fftw, libwinpthread, vc14_runtime -> their DLLs
Everything ends up in <prefix>/Library/bin (runtime DLLs may also be in <prefix>),
so we copy all DLLs from there plus the hackrf tools into one folder.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

LIB_PATTERNS = ("hackrf-0.dll", "hackrf.dll", "libhackrf*.dll", "hackrf*.dll")


def find_library(prefix: Path) -> Path | None:
    for pattern in LIB_PATTERNS:
        hits = sorted(p for p in prefix.rglob(pattern) if p.is_file())
        if hits:
            return hits[0]
    return None


def version_of(prefix: Path, name: str) -> str:
    for meta in (prefix / "conda-meta").glob(f"{name}-[0-9]*.json"):
        try:
            return json.loads(meta.read_text())["version"]
        except (OSError, ValueError, KeyError):
            pass
    return "unknown"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    prefix = Path(sys.argv[1])
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("vendor/hackrf/windows")
    if not prefix.is_dir():
        print(f"error: environment folder does not exist: {prefix}")
        return 1
    dest.mkdir(parents=True, exist_ok=True)

    lib = find_library(prefix)
    if lib is None:
        print(f"error: no libhackrf DLL found under {prefix}. DLLs/EXEs present:")
        for p in sorted(prefix.rglob("*")):
            if p.suffix.lower() in (".dll", ".exe"):
                print("  ", p.relative_to(prefix))
        print("installed packages:", ", ".join(sorted(m.stem for m in (prefix / "conda-meta").glob("*.json"))))
        return 1

    copied: list[str] = []
    for folder in dict.fromkeys([lib.parent, prefix / "Library" / "bin", prefix]):
        if not folder.is_dir():
            continue
        for f in folder.glob("*.dll"):
            if not (dest / f.name).exists():
                shutil.copy2(f, dest / f.name)
                copied.append(f.name)
    for t in sorted(prefix.rglob("hackrf_*.exe")):
        shutil.copy2(t, dest / t.name)
        copied.append(t.name)

    v_lib, v_tools = version_of(prefix, "libhackrf0"), version_of(prefix, "hackrf")
    (dest / "VERSION.txt").write_text(f"libhackrf {v_lib}, hackrf tools {v_tools} (conda-forge)\n",
                                      encoding="utf-8")
    print(f"libhackrf: {lib.name} ({v_lib}); tools {v_tools}; copied {len(copied)} files to {dest}")
    for name in sorted(copied):
        print("  ", name)
    missing = [t for t in ("hackrf_transfer.exe", "hackrf_info.exe") if t not in copied]
    if missing:
        print(f"error: missing tools: {', '.join(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
