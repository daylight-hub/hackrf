"""Turns (script + saved option values + settings) into a launchable process."""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Any

from . import paths, script_model
from .config import Settings
from .supervisor import LaunchSpec


def hackrf_search_dirs(settings: Settings) -> list[str]:
    dirs: list[str] = []
    if settings.hackrf.tools_dir:
        dirs.append(settings.hackrf.tools_dir)
    bundled = paths.bundled_hackrf_dir()
    if bundled:
        dirs.append(str(bundled))
    return dirs


def load_overrides(settings: Settings, script: Path, spec: script_model.ScriptSpec) -> dict[str, Any]:
    stored = settings.overrides_for(script)
    values: dict[str, Any] = {}
    known = spec.by_name()
    for name, text in stored.items():
        if name not in known:
            continue
        try:
            values[name] = script_model.deserialize(text)
        except (ValueError, SyntaxError):
            continue
    return values


def prepare_launch(settings: Settings, script: str | Path) -> LaunchSpec:
    """Re-read and patch the script (so edits on disk are picked up on restart)."""
    script = Path(script).resolve()
    if not script.is_file():
        raise FileNotFoundError(f"Script not found: {script}")
    source = script_model.read_source(script)
    spec = script_model.parse_script(script, source)
    if spec.errors:
        raise RuntimeError("; ".join(spec.errors))
    overrides = load_overrides(settings, script, spec)
    patched, notes = script_model.apply_overrides(source, spec, overrides)

    digest = hashlib.sha1(str(script).encode()).hexdigest()[:10]
    patched_path = paths.run_dir() / f"{script.stem}-{digest}.py"
    patched_path.write_text(patched, encoding="utf-8")

    hackrf_dirs = hackrf_search_dirs(settings)
    env = paths.clean_child_env(extra_path=hackrf_dirs)
    if hackrf_dirs:
        env["RFSTUDIO_HACKRF_DIR"] = hackrf_dirs[-1] if len(hackrf_dirs) > 1 else hackrf_dirs[0]
        for d in hackrf_dirs:
            if (Path(d) / ("hackrf.dll" if paths.IS_WINDOWS else "libhackrf.so.0")).exists():
                env["RFSTUDIO_HACKRF_DIR"] = d
                break
    env["RFSTUDIO_SCRIPT"] = str(script)

    runner_args = ["--script", str(patched_path), "--origin", str(script)]
    if settings.python.mode == "custom" and settings.python.interpreter:
        interpreter = settings.python.interpreter
        if not Path(interpreter).exists() and not paths.which(interpreter):
            raise FileNotFoundError(f"Python interpreter not found: {interpreter}")
        argv = [interpreter, "-u", str(paths.runner_file()), *runner_args]
        desc = f"{script.name} ({Path(interpreter).name})"
    else:
        argv = [*paths.cli_command(), "--run-script", *runner_args]
        desc = script.name

    extra = settings.script_args.get(settings.key(script), "").strip()
    if extra:
        argv += ["--", *shlex.split(extra, posix=not paths.IS_WINDOWS)]

    changed = [f"{k}={v!r}" for k, v in overrides.items() if spec.by_name()[k].default != v]
    info = [f"Options changed from script defaults: {', '.join(changed)}"] if changed else []
    return LaunchSpec(argv=argv, cwd=str(script.parent), env=env, description=desc,
                      notes=list(notes), info=info)


def describe_command(spec: LaunchSpec) -> str:
    return " ".join(shlex.quote(a) for a in spec.argv)


def default_script(settings: Settings) -> Path | None:
    if settings.last_script and Path(settings.last_script).is_file():
        return Path(settings.last_script)
    found = paths.discover_scripts()
    return found[0] if found else None


