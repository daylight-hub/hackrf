"""Turns (script + saved option values + settings) into a launchable process."""

from __future__ import annotations

import hashlib
import os
import shlex
import uuid
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


def working_dir(script: Path) -> Path:
    """Folder the script runs in (and writes relative files to).

    The script's own folder when it is writable (scripts you opened yourself);
    for scripts bundled inside an installed app (/opt/..., Program Files) that
    folder is read-only, so use ~/RF Script Studio/<script name>/ instead.
    """
    folder = script.parent
    if not _is_bundled(script) and _writable(folder):
        return folder
    target = paths.user_files_dir() / script.stem
    target.mkdir(parents=True, exist_ok=True)
    return target


def _is_bundled(script: Path) -> bool:
    """Scripts shipped inside a packaged app are never run in place."""
    if not paths.IS_FROZEN:
        return False
    try:
        script.resolve().relative_to(paths.resource_root().resolve())
        return True
    except ValueError:
        return False


def mirror_script_folder(script: Path, workdir: Path) -> Path:
    """Copy the script's folder into `workdir` and return the script's new path.

    * Python files are refreshed on every launch (they belong to the app build).
    * Other files and folders are copied only if missing, so data the script
      created or edited (backups, configs, captures) is never overwritten.
    """
    import shutil

    workdir.mkdir(parents=True, exist_ok=True)
    for entry in script.parent.iterdir():
        if entry.name.startswith((".", "__pycache__")):
            continue
        if entry == workdir or entry in workdir.parents:  # never copy a folder into itself
            continue
        target = workdir / entry.name
        try:
            if entry.is_dir():
                if not target.exists():
                    shutil.copytree(entry, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            elif entry.suffix == ".py":
                if not target.exists() or entry.read_bytes() != target.read_bytes():
                    shutil.copy2(entry, target)
                    target.chmod(0o644)
            elif not target.exists():
                shutil.copy2(entry, target)
                target.chmod(0o644)
        except OSError:
            continue
    # copies of read-only files would stay read-only; make the folder tree writable
    for p in workdir.rglob("*"):
        try:
            p.chmod(p.stat().st_mode | 0o200)
        except OSError:
            pass
    return workdir / script.name


def _writable(folder: Path) -> bool:
    import os
    import tempfile

    if not os.access(folder, os.W_OK):
        return False
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".rfstudio-", delete=True):
            return True
    except OSError:
        return False


def start_mode_for(settings: Settings, script: Path, spec: script_model.ScriptSpec) -> str:
    """Flag passed when the supervised script starts (e.g. "--scan")."""
    key = settings.key(script)
    modes = spec.commands.modes if spec.commands else []
    chosen = settings.start_mode.get(key, "")
    # only flags the user picked; a guessed flag can be a one-off task (e.g. a single scan)
    return chosen if chosen in [m.flag for m in modes] else ""


def prepare_launch(settings: Settings, script: str | Path, args: list[str] | None = None) -> LaunchSpec:
    """`args=None`: the supervised run (start mode + saved arguments).
    `args=[...]`: a one-off command run with exactly these arguments."""
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
    tmp = patched_path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(patched, encoding="utf-8")
    os.replace(tmp, patched_path)  # atomic: a command run can start while the supervised run does

    hackrf_dirs = hackrf_search_dirs(settings)
    env = paths.clean_child_env(extra_path=hackrf_dirs)
    if hackrf_dirs:
        env["RFSTUDIO_HACKRF_DIR"] = hackrf_dirs[-1] if len(hackrf_dirs) > 1 else hackrf_dirs[0]
        for d in hackrf_dirs:
            if paths.find_hackrf_lib(d):
                env["RFSTUDIO_HACKRF_DIR"] = d
                break
    workdir = working_dir(script)
    origin = script
    if workdir != script.parent:
        # Read-only install folder: run from a writable copy so __file__-relative
        # paths (Path(__file__).parent / "backups") work too.
        origin = mirror_script_folder(script, workdir)
    env["RFSTUDIO_SCRIPT"] = str(origin)
    env["RFSTUDIO_PATCHED_SCRIPT"] = str(patched_path)
    env["RFSTUDIO_BUNDLED_SCRIPT"] = str(script)
    env["RFSTUDIO_WORKDIR"] = str(workdir)

    runner_args = ["--script", str(patched_path), "--origin", str(origin)]
    if settings.python.mode == "custom" and settings.python.interpreter:
        interpreter = settings.python.interpreter
        if not Path(interpreter).exists() and not paths.which(interpreter):
            raise FileNotFoundError(f"Python interpreter not found: {interpreter}")
        argv = [interpreter, "-u", str(paths.runner_file()), *runner_args]
        desc = f"{script.name} ({Path(interpreter).name})"
    else:
        argv = [*paths.cli_command(), "--run-script", *runner_args]
        desc = script.name

    if args is None:
        run_args: list[str] = []
        mode = start_mode_for(settings, script, spec)
        if mode:
            run_args.append(mode)
        extra = settings.script_args.get(settings.key(script), "").strip()
        if extra:
            run_args += shlex.split(extra, posix=not paths.IS_WINDOWS)
    else:
        run_args = list(args)
    if run_args:
        argv += ["--", *run_args]
        desc += " " + " ".join(run_args)

    changed = [f"{k}={v!r}" for k, v in overrides.items() if spec.by_name()[k].default != v]
    info = [f"Options changed from script defaults: {', '.join(changed)}"] if changed else []
    if workdir != script.parent:
        info.append(f"Working folder: {workdir}")
    return LaunchSpec(argv=argv, cwd=str(workdir), env=env, description=desc,
                      notes=list(notes), info=info)


def describe_command(spec: LaunchSpec) -> str:
    return " ".join(shlex.quote(a) for a in spec.argv)


def default_script(settings: Settings) -> Path | None:
    if settings.last_script and Path(settings.last_script).is_file():
        return Path(settings.last_script)
    found = paths.discover_scripts()
    return found[0] if found else None


