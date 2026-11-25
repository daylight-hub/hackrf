"""Persistent settings (JSON, written atomically)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths

RESTART_ALWAYS = "always"
RESTART_ON_FAILURE = "on-failure"
RESTART_NEVER = "never"
SETTINGS_VERSION = 2


@dataclass
class SupervisorSettings:
    restart_policy: str = RESTART_ON_FAILURE  # a script that finishes cleanly stays finished
    max_restarts: int = 0            # within restart_window_s; 0 = unlimited
    restart_window_s: int = 600
    backoff_initial_s: float = 1.0
    backoff_max_s: float = 60.0
    stable_after_s: float = 30.0     # a run this long resets the backoff
    stall_timeout_s: int = 0         # restart if no output for N s; 0 = off
    stop_timeout_s: float = 8.0


@dataclass
class UiSettings:
    theme: str = "dark"
    text_size: str = "large"         # small | normal | large | xlarge
    close_to_tray: bool = True
    start_minimized: bool = False
    notify: bool = True
    autorun_script: bool = False     # start the script when the app launches
    restart_on_change: bool = False  # apply option changes immediately
    touch_mode: bool = True          # drag anywhere to scroll (touch screens)
    launch_at_login: bool = False
    console_max_lines: int = 5000


@dataclass
class HackRFSettings:
    tools_dir: str = ""              # folder with hackrf_transfer/hackrf_info
    tx_acknowledged: bool = False


@dataclass
class PythonSettings:
    mode: str = "bundled"            # bundled | custom
    interpreter: str = ""


@dataclass
class Settings:
    last_script: str = ""
    recent_scripts: list[str] = field(default_factory=list)
    overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    script_args: dict[str, str] = field(default_factory=dict)
    start_mode: dict[str, str] = field(default_factory=dict)  # script -> flag ("" = run without a flag)
    supervisor: SupervisorSettings = field(default_factory=SupervisorSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    hackrf: HackRFSettings = field(default_factory=HackRFSettings)
    python: PythonSettings = field(default_factory=PythonSettings)
    transfer: dict[str, Any] = field(default_factory=dict)
    window_geometry: str = ""
    settings_version: int = SETTINGS_VERSION

    # ---------------------------------------------------------------- per script
    @staticmethod
    def key(script: str | Path) -> str:
        return str(Path(script).resolve())

    def overrides_for(self, script: str | Path) -> dict[str, str]:
        return self.overrides.setdefault(self.key(script), {})

    def remember_script(self, script: str | Path) -> None:
        key = self.key(script)
        self.last_script = key
        if key in self.recent_scripts:
            self.recent_scripts.remove(key)
        self.recent_scripts.insert(0, key)
        del self.recent_scripts[12:]


_NESTED = {
    "supervisor": SupervisorSettings,
    "ui": UiSettings,
    "hackrf": HackRFSettings,
    "python": PythonSettings,
}


def _build(cls: type, data: dict[str, Any]) -> Any:
    known = {f.name for f in fields(cls)}
    kwargs = {}
    for k, v in (data or {}).items():
        if k not in known:
            continue
        if k in _NESTED and cls is Settings:
            v = _build(_NESTED[k], v if isinstance(v, dict) else {})
        kwargs[k] = v
    return cls(**kwargs)


def _migrate(s: Settings, data: dict[str, Any]) -> Settings:
    version = int(data.get("settings_version", 1) or 1)
    if version < 2:
        # 1.0.x defaulted to "always restart", which loops scripts that finish on purpose
        # (e.g. a one-off scan). The default is now "restart only if it crashes".
        if s.supervisor.restart_policy == RESTART_ALWAYS:
            s.supervisor.restart_policy = RESTART_ON_FAILURE
        # start modes were guessed automatically; only keep ones the user picked
        s.start_mode = {k: v for k, v in s.start_mode.items() if v}
    s.settings_version = SETTINGS_VERSION
    return s


def settings_path() -> Path:
    return paths.config_dir() / "settings.json"


def load(path: Path | None = None) -> Settings:
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        settings = _build(Settings, data)
        return _migrate(settings, data)
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError, TypeError):
        # Corrupt file: keep a copy and start fresh rather than refusing to run
        try:
            path.replace(path.with_suffix(".corrupt.json"))
        except OSError:
            pass
        return Settings()


def save(settings: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(settings), indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
