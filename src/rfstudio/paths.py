"""Filesystem locations used by the app (works from source and when frozen)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import APP_ID, ORG_NAME

IS_WINDOWS = sys.platform.startswith("win")
IS_FROZEN = bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Root that holds bundled data (scripts/, examples/, assets/, hackrf/)."""
    if IS_FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # src/rfstudio/paths.py -> repo root
    return Path(__file__).resolve().parents[2]


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def config_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        path = base / ORG_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        path = base / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        path = base / ORG_NAME
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
        path = base / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = state_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_dir() -> Path:
    """Where patched copies of scripts are written before each launch."""
    path = state_dir() / "run"
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_script_dirs() -> list[Path]:
    """`scripts/` (your scripts) first, then `examples/`."""
    root = resource_root()
    return [p for p in (root / "scripts", root / "examples") if p.is_dir()]


def discover_scripts() -> list[Path]:
    found: list[Path] = []
    for folder in bundled_script_dirs():
        for path in sorted(folder.glob("*.py")):
            if not path.name.startswith("_"):
                found.append(path)
    return found


def assets_dir() -> Path:
    return resource_root() / "assets"


# libhackrf file names: conda-forge's Windows build is hackrf-0.dll, other builds hackrf.dll
HACKRF_LIB_NAMES = (
    ("hackrf-0.dll", "hackrf.dll", "libhackrf.dll", "libhackrf-0.dll") if IS_WINDOWS
    else ("libhackrf.so.0", "libhackrf.so")
)


def find_hackrf_lib(folder: str | Path | None) -> Path | None:
    if not folder:
        return None
    for name in HACKRF_LIB_NAMES:
        cand = Path(folder) / name
        if cand.is_file():
            return cand
    return None


def bundled_hackrf_dir() -> Path | None:
    """HackRF native tools/libraries shipped with the build (if any)."""
    root = resource_root()
    candidates = [root / "hackrf"]
    if not IS_FROZEN:
        plat = "windows" if IS_WINDOWS else "linux"
        candidates.append(root / "vendor" / "hackrf" / plat)
    for c in candidates:
        if c.is_dir() and any(c.iterdir()):
            return c
    return None


def runner_file() -> Path:
    """Standalone runner used when scripts run under an external interpreter."""
    if IS_FROZEN:
        return resource_root() / "rfstudio_runner.py"
    return package_dir() / "runner.py"


def cli_command() -> list[str]:
    """Command prefix that invokes this application's console entry point."""
    if IS_FROZEN:
        exe_dir = Path(sys.executable).parent
        name = "rfstudio-cli.exe" if IS_WINDOWS else "rfstudio-cli"
        cli = exe_dir / name
        return [str(cli if cli.exists() else Path(sys.executable))]
    return [sys.executable, "-m", "rfstudio"]


def gui_command() -> list[str]:
    if IS_FROZEN:
        return [sys.executable]
    return [sys.executable, "-m", "rfstudio"]


def which(program: str, extra_dirs: list[Path | str] | None = None) -> str | None:
    """shutil.which that also searches the given directories first."""
    exe = program + (".exe" if IS_WINDOWS and not program.endswith(".exe") else "")
    for d in extra_dirs or []:
        if d:
            cand = Path(d) / exe
            if cand.is_file():
                return str(cand)
    return shutil.which(program)


def clean_child_env(extra_path: list[str] | None = None) -> dict[str, str]:
    """Environment for child processes.

    PyInstaller's bootloader rewrites LD_LIBRARY_PATH on Linux; restore the
    original so system tools (hackrf_transfer, python) load system libraries.
    """
    env = dict(os.environ)
    if IS_FROZEN and not IS_WINDOWS:
        orig = env.get("LD_LIBRARY_PATH_ORIG")
        if orig is not None:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    if not IS_FROZEN:
        # running from source: make `python -m rfstudio` importable in children
        src = str(package_dir().parent)
        parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        if src not in parts:
            env["PYTHONPATH"] = os.pathsep.join([src, *parts])
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_path:
        env["PATH"] = os.pathsep.join([*extra_path, env.get("PATH", "")])
    return env
