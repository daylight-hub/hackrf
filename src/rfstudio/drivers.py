"""Driver / dependency checks and installers.

Linux:   runs the bundled `linux-setup.sh` through pkexec (graphical sudo): installs
         libhackrf + hackrf tools with the system package manager, udev rules and
         plugdev membership.
Windows: the HackRF DLL + tools are bundled. Boards with stock firmware use the
         built-in WinUSB driver automatically; if Windows doesn't bind it (older or
         third-party firmware) the bundled Zadig is launched with WinUSB preselected.
"""

from __future__ import annotations

import ctypes.util
import glob
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

HACKRF_IDS = ("1D50&PID_6089", "1D50&PID_604B", "1D50&PID_CC15")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class DriverStatus:
    checks: list[Check] = field(default_factory=list)
    can_install: bool = False
    install_label: str = ""
    note: str = ""

    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks)


# ------------------------------------------------------------------- Linux

def linux_setup_script() -> Path | None:
    for cand in (paths.resource_root() / "setup" / "linux-setup.sh",
                 paths.resource_root() / "packaging" / "linux" / "linux-setup.sh"):
        if cand.is_file():
            return cand
    return None


def _in_plugdev() -> bool:
    import grp

    try:
        gid = grp.getgrnam("plugdev").gr_gid
    except KeyError:
        return False
    return gid in os.getgroups() or gid == os.getgid()


def _udev_rule_present() -> bool:
    for d in ("/etc/udev/rules.d", "/lib/udev/rules.d", "/usr/lib/udev/rules.d"):
        for f in glob.glob(f"{d}/*.rules"):
            try:
                if "1d50" in Path(f).read_text(errors="ignore") and "6089" in Path(f).read_text(errors="ignore"):
                    return True
            except OSError:
                continue
    return False


def linux_status() -> DriverStatus:
    lib = ctypes.util.find_library("hackrf")
    tools = shutil.which("hackrf_transfer")
    st = DriverStatus(checks=[
        Check("libhackrf", bool(lib), lib or "not installed"),
        Check("hackrf tools", bool(tools), tools or "hackrf_transfer / hackrf_info not on PATH"),
        Check("udev rules", _udev_rule_present(), "lets you use the board without root"),
        Check("plugdev group", _in_plugdev(), "log out/in after being added"),
    ])
    script = linux_setup_script()
    st.can_install = bool(script) and bool(shutil.which("pkexec") or shutil.which("sudo"))
    st.install_label = "Install HackRF drivers"
    st.note = ("Installs hackrf + libhackrf with your package manager, adds udev rules and plugdev "
               "membership (asks for your password). pyhackrf2 is already bundled in the app.")
    return st


def linux_install_command(with_python: bool = False) -> list[str]:
    script = linux_setup_script()
    if not script:
        raise FileNotFoundError("linux-setup.sh is not bundled with this build")
    args = ["bash", str(script), "--user", os.environ.get("USER", "")]
    if with_python:
        args.append("--with-python")
    if shutil.which("pkexec"):
        return ["pkexec", *args]
    return ["sudo", "-n", *args]  # works only with cached credentials; GUI prefers pkexec


# ----------------------------------------------------------------- Windows

def zadig_path() -> Path | None:
    for cand in (paths.resource_root() / "drivers" / "zadig.exe",
                 paths.resource_root() / "vendor" / "drivers" / "windows" / "zadig.exe"):
        if cand.is_file():
            return cand
    return None


def _windows_devices() -> list[dict]:
    ps = ("Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_1D50&PID_(6089|604B|CC15)' } | "
          "Select-Object FriendlyName,Status,Class,InstanceId | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    text = out.stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


def windows_status() -> DriverStatus:
    bundled = paths.bundled_hackrf_dir()
    dll = paths.find_hackrf_lib(bundled)
    devices = _windows_devices()
    checks = [
        Check("libhackrf DLL", dll is not None, str(dll) if dll else "not bundled"),
        Check("hackrf tools", bool(bundled and (bundled / "hackrf_transfer.exe").exists()),
              str(bundled) if bundled else "not bundled"),
    ]
    if not devices:
        checks.append(Check("USB driver", True, "no HackRF plugged in - connect it to check the driver"))
    for d in devices:
        ok = str(d.get("Status", "")).upper() == "OK"
        detail = f"{d.get('FriendlyName') or 'HackRF'}: {d.get('Status')} ({d.get('Class') or 'no driver'})"
        checks.append(Check("USB driver", ok, detail))
    st = DriverStatus(checks=checks)
    st.can_install = zadig_path() is not None
    st.install_label = "Install / repair USB driver"
    st.note = ("Stock HackRF firmware normally gets the built-in WinUSB driver automatically. If the board shows "
               "an error, the bundled Zadig opens with WinUSB preselected: choose the HackRF in the list and "
               "click Install/Replace Driver.")
    return st


def launch_zadig() -> tuple[bool, str]:
    exe = zadig_path()
    if not exe:
        return False, "Zadig is not bundled with this build. Download it from https://zadig.akeo.ie/"
    try:
        # "runas" -> UAC prompt; working dir = Zadig folder so it picks up zadig.ini
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), None, str(exe.parent), 1)  # type: ignore[attr-defined]
    except (AttributeError, OSError) as exc:
        return False, str(exc)
    if rc <= 32:
        return False, f"Could not start Zadig (code {rc})"
    return True, "Zadig started - select your HackRF and install the WinUSB driver."


def status() -> DriverStatus:
    return windows_status() if paths.IS_WINDOWS else linux_status()
