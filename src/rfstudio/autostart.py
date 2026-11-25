"""Launch-at-login and background-service installation (Linux + Windows).

* Launch at login starts the desktop app minimised to the tray.
* The background service runs the headless supervisor and is itself restarted
  by the OS if it ever exits (systemd ``Restart=on-failure`` / Task Scheduler
  restart-on-failure), giving two layers of "keep it running".
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

from . import APP_ID, APP_NAME, paths

SERVICE_NAME = APP_ID  # systemd unit / scheduled task name
TASK_NAME = APP_NAME


def _run(cmd: list[str]) -> tuple[bool, str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if paths.IS_WINDOWS else 0)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return out.returncode == 0, (out.stdout + out.stderr).strip()


def _quote_win(args: list[str]) -> str:
    return subprocess.list2cmdline(args)


# ------------------------------------------------------------ launch at login

def _linux_autostart_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / f"{APP_ID}.desktop"


def set_launch_at_login(enabled: bool) -> tuple[bool, str]:
    args = [*paths.gui_command(), "--minimized"]
    if paths.IS_WINDOWS:
        try:
            import winreg
        except ImportError as exc:  # pragma: no cover
            return False, str(exc)
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _quote_win(args))
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            return False, str(exc)
        return True, "Updated Windows startup entry"
    path = _linux_autostart_file()
    if not enabled:
        path.unlink(missing_ok=True)
        return True, f"Removed {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    icon = paths.assets_dir() / "icon.png"
    path.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={APP_NAME}\nExec={shlex.join(args)}\nIcon={icon}\n"
        "X-GNOME-Autostart-enabled=true\nTerminal=false\n",
        encoding="utf-8",
    )
    return True, f"Created {path}"


def launch_at_login_enabled() -> bool:
    if paths.IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, APP_NAME)
                return True
        except (ImportError, OSError):
            return False
    return _linux_autostart_file().exists()


# ------------------------------------------------------------ background service

def _headless_args(script: str) -> list[str]:
    if paths.IS_WINDOWS:
        # GUI-subsystem exe: no console window flashes up at logon
        base = paths.gui_command()
    else:
        base = paths.cli_command()
    return [*base, "--headless", "--script", str(Path(script).resolve())]


def _systemd_unit_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "systemd" / "user" / f"{SERVICE_NAME}.service"


def systemd_unit_text(script: str) -> str:
    exec_start = " ".join(shlex.quote(a) for a in _headless_args(script))
    return (
        "[Unit]\n"
        f"Description={APP_NAME} background supervisor\n"
        "After=network-online.target\n"
        "StartLimitIntervalSec=0\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "KillSignal=SIGINT\n"
        "TimeoutStopSec=20\n"
        "Environment=PYTHONUNBUFFERED=1\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def windows_task_xml(script: str) -> str:
    args = _headless_args(script)
    command, arguments = args[0], _quote_win(args[1:])
    user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape(APP_NAME)} background supervisor</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(str(Path(script).resolve().parent))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install_service(script: str, start_now: bool = True) -> tuple[bool, str]:
    if paths.IS_WINDOWS:
        xml_path = paths.state_dir() / "service-task.xml"
        xml_path.write_text(windows_task_xml(script), encoding="utf-16")
        ok, out = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"])
        if ok and start_now:
            _run(["schtasks", "/Run", "/TN", TASK_NAME])
        return ok, out or "Scheduled task created"
    unit = _systemd_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(systemd_unit_text(script), encoding="utf-8")
    ok, out = _run(["systemctl", "--user", "daemon-reload"])
    if not ok:
        return False, f"Unit written to {unit}, but systemctl failed: {out}"
    ok, out = _run(["systemctl", "--user", "enable", *(["--now"] if start_now else []), f"{SERVICE_NAME}.service"])
    msg = f"Installed {unit}"
    if ok:
        msg += ". Tip: run `loginctl enable-linger $USER` to keep it running when logged out."
    return ok, msg if ok else out


def uninstall_service() -> tuple[bool, str]:
    if paths.IS_WINDOWS:
        _run(["schtasks", "/End", "/TN", TASK_NAME])
        return _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    _run(["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.service"])
    _systemd_unit_path().unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
    return True, "Service removed"


def service_status() -> str:
    if paths.IS_WINDOWS:
        ok, out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
        if not ok:
            return "not installed"
        for line in out.splitlines():
            if line.strip().lower().startswith("status:"):
                return line.split(":", 1)[1].strip().lower()
        return "installed"
    if not _systemd_unit_path().exists():
        return "not installed"
    ok, out = _run(["systemctl", "--user", "is-active", f"{SERVICE_NAME}.service"])
    return out.strip() or ("active" if ok else "inactive")

