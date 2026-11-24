# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for RF Script Studio.
#   pyinstaller packaging/rfstudio.spec --noconfirm
#
# Produces dist/RFScriptStudio/ with two executables sharing one runtime:
#   rfscriptstudio(.exe)  - desktop app (no console window on Windows)
#   rfstudio-cli(.exe)    - console entry: runs scripts, headless service, probe
#
# Everything in scripts/ (your scripts) and examples/ is bundled, and the
# modules your scripts import are collected automatically.

import ast
import importlib.util
import shutil
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
IS_WIN = sys.platform.startswith("win")
BUILD = ROOT / "build" / "spec-extra"
BUILD.mkdir(parents=True, exist_ok=True)

STDLIB = set(getattr(sys, "stdlib_module_names", ()))


def script_imports():
    mods = set()
    for folder in ("scripts", "examples"):
        for py in sorted((ROOT / folder).glob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError) as exc:
                print(f"[spec] warning: cannot parse {py}: {exc}")
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    mods.add(node.module)
    return mods


def importable(name):
    try:
        return importlib.util.find_spec(name.split(".")[0]) is not None
    except (ImportError, ValueError):
        return False


hidden = {"pyhackrf2", "pyhackrf2.cinterface", "numpy"}
for m in script_imports():
    top = m.split(".")[0]
    if top in STDLIB or importable(m):
        hidden.add(m)
    else:
        print(f"[spec] warning: script imports '{m}' but it is not installed - "
              "add it to scripts/requirements.txt")

# The standalone runner is shipped as a plain file for "custom interpreter" mode
shutil.copy2(ROOT / "src" / "rfstudio" / "runner.py", BUILD / "rfstudio_runner.py")

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "examples"), "examples"),
    (str(BUILD / "rfstudio_runner.py"), "."),
]
if (ROOT / "scripts").is_dir():
    datas.append((str(ROOT / "scripts"), "scripts"))

# Native HackRF bits are copied verbatim (no dependency rewriting)
plat = "windows" if IS_WIN else "linux"
hackrf_vendor = ROOT / "vendor" / "hackrf" / plat
if hackrf_vendor.is_dir() and any(hackrf_vendor.iterdir()):
    datas.append((str(hackrf_vendor), "hackrf"))
    print(f"[spec] bundling HackRF tools from {hackrf_vendor}")
drivers_vendor = ROOT / "vendor" / "drivers" / plat
if drivers_vendor.is_dir() and any(drivers_vendor.iterdir()):
    datas.append((str(drivers_vendor), "drivers"))
if not IS_WIN:
    datas.append((str(ROOT / "packaging" / "linux" / "linux-setup.sh"), "setup"))

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(hidden),
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtPdf", "matplotlib.tests", "numpy.tests"],
    noarchive=False,
)
# Never ship the build machine's libhackrf/libusb/libudev on Linux: PyInstaller picks them
# up from pyhackrf2's CDLL("libhackrf.so.0"), but an old bundled copy would shadow the
# system library that the .deb / install.sh installs (and that matches the udev setup).
if not IS_WIN:
    _system_only = ("libhackrf", "libusb-1.0", "libudev")
    a.binaries = [b for b in a.binaries if not Path(b[0]).name.startswith(_system_only)]

pyz = PYZ(a.pure)

icon = str(ROOT / "assets" / ("icon.ico" if IS_WIN else "icon.png"))

gui = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="rfscriptstudio",
    console=False,
    icon=icon,
    upx=False,
)
cli = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="rfstudio-cli",
    console=True,
    icon=icon,
    upx=False,
)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="RFScriptStudio", upx=False)
