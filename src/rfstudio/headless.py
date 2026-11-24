"""Headless mode: supervise a script with no window (used by the background service)."""

from __future__ import annotations

import logging
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import APP_NAME, __version__, config, paths, session
from .supervisor import State, Supervisor


def make_script_logger(script: Path, name: str = "script") -> logging.Logger:
    """Rotating log file per script: <log dir>/<script>.log (5 x 2 MB)."""
    logger = logging.getLogger(f"rfstudio.{name}.{script.stem}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(paths.log_dir() / f"{script.stem}.log",
                                      maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    return logger


def run(script_arg: str | None) -> int:
    settings = config.load()
    script = Path(script_arg).resolve() if script_arg else session.default_script(settings)
    if script is None or not script.is_file():
        print(f"{APP_NAME}: no script found ({script_arg or 'none configured'})", file=sys.stderr)
        return 2

    log = make_script_logger(script)
    has_console = sys.stdout is not None

    def emit(text: str) -> None:
        log.info(text)
        if has_console:
            try:
                print(text, flush=True)
            except (OSError, ValueError):
                pass

    done = threading.Event()

    def on_state(state: State) -> None:
        emit(f"[supervisor] state: {state.value}")
        if state in (State.STOPPED, State.FAILED):
            done.set()

    sup = Supervisor(
        lambda: session.prepare_launch(config.load(), script),
        policy=settings.supervisor,
        on_output=lambda line, stream: emit(("! " if stream == "stderr" else "  ") + line),
        on_state=on_state,
        on_event=lambda level, msg: emit(f"[{level}] {msg}"),
    )

    def shutdown(*_args: object) -> None:
        emit("[supervisor] shutdown requested")
        sup.stop(wait=False)
        done.set()

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            try:
                signal.signal(sig, shutdown)
            except (ValueError, OSError):
                pass

    emit(f"[supervisor] {APP_NAME} {__version__} headless - {script}")
    sup.start()
    try:
        while not done.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    sup.stop(wait=True)
    emit("[supervisor] exited")
    return 0 if sup.state != State.FAILED else 1
