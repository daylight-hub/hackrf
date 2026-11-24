"""Process supervisor: keeps a script running and restarts it when it stops.

Pure Python (threads + subprocess) so it is shared by the desktop app and the
headless/background service. Callbacks are invoked from worker threads.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from .config import RESTART_NEVER, RESTART_ON_FAILURE, SupervisorSettings
from .runner import EXIT_CONFIG_ERROR

IS_WINDOWS = sys.platform.startswith("win")


def _linux_pdeathsig() -> None:  # runs in the child between fork and exec
    """Ask the kernel to SIGTERM the script if the supervising process dies."""
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)  # PR_SET_PDEATHSIG = 1
    except Exception:  # noqa: BLE001 - best effort, never block the launch
        pass


class _WindowsJob:
    """Job object with KILL_ON_JOB_CLOSE: children die if the supervisor is killed."""

    def __init__(self) -> None:
        self.handle = None
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class IO_COUNTERS(ctypes.Structure):  # noqa: N801
                _fields_ = [(n, ctypes.c_ulonglong) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32.CreateJobObjectW.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if job and k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                self.handle = job
                self._k32 = k32
        except Exception:  # noqa: BLE001
            self.handle = None

    def assign(self, proc: subprocess.Popen) -> None:
        if self.handle is None:
            return
        try:
            from ctypes import wintypes

            self._k32.AssignProcessToJobObject(wintypes.HANDLE(self.handle), wintypes.HANDLE(int(proc._handle)))
        except Exception:  # noqa: BLE001
            pass


class State(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    BACKOFF = "restarting"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class LaunchSpec:
    argv: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    description: str = ""
    notes: list[str] = field(default_factory=list)   # warnings
    info: list[str] = field(default_factory=list)


OutputCallback = Callable[[str, str], None]          # (line, "stdout"|"stderr")
StateCallback = Callable[[State], None]
EventCallback = Callable[[str, str], None]           # (level, message)


class Supervisor:
    def __init__(
        self,
        launch_factory: Callable[[], LaunchSpec],
        policy: SupervisorSettings | None = None,
        on_output: OutputCallback | None = None,
        on_state: StateCallback | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        self._factory = launch_factory
        self.policy = policy or SupervisorSettings()
        self._on_output = on_output or (lambda line, stream: None)
        self._on_state = on_state or (lambda state: None)
        self._on_event = on_event or (lambda level, msg: None)

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._want_running = False
        self._restart_requested = False

        self.state = State.STOPPED
        self.restart_count = 0
        self.last_exit_code: int | None = None
        self.started_at: float | None = None
        self._last_output = 0.0
        self._restart_times: deque[float] = deque()
        self._job = _WindowsJob() if IS_WINDOWS else None

    # ------------------------------------------------------------- public API
    @property
    def pid(self) -> int | None:
        proc = self._proc
        return proc.pid if proc and proc.poll() is None else None

    @property
    def uptime(self) -> float:
        if self.state == State.RUNNING and self.started_at:
            return time.monotonic() - self.started_at
        return 0.0

    @property
    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            was_wanted = self._want_running
            self._want_running = True
            if self.is_active:
                if not was_wanted:  # a stop is in flight: turn it into a restart
                    self._restart_requested = True
                    self._wake.set()
                return
            self.restart_count = 0
            self._restart_times.clear()
            self._thread = threading.Thread(target=self._run_loop, name="supervisor", daemon=True)
            self._thread.start()

    def stop(self, wait: bool = True, timeout: float | None = None) -> None:
        with self._lock:
            self._want_running = False
            self._wake.set()
            thread = self._thread
        if wait and thread and thread is not threading.current_thread():
            thread.join(timeout if timeout is not None else self.policy.stop_timeout_s + 5)

    def restart(self) -> None:
        """Manual restart: stop the current process and start again at once."""
        with self._lock:
            if not self.is_active:
                self.start()
                return
            self._restart_requested = True
            self._wake.set()

    # ------------------------------------------------------------ internals
    def _set_state(self, state: State) -> None:
        if state != self.state:
            self.state = state
            try:
                self._on_state(state)
            except Exception:  # pragma: no cover - never let a UI bug kill us
                pass

    def _event(self, level: str, msg: str) -> None:
        try:
            self._on_event(level, msg)
        except Exception:  # pragma: no cover
            pass

    def _spawn(self, spec: LaunchSpec) -> subprocess.Popen[bytes]:
        kwargs: dict = dict(
            cwd=spec.cwd or None,
            env=spec.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if IS_WINDOWS:
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            kwargs["start_new_session"] = True
            if sys.platform.startswith("linux"):
                kwargs["preexec_fn"] = _linux_pdeathsig
        proc = subprocess.Popen(spec.argv, **kwargs)
        if self._job is not None:
            self._job.assign(proc)
        return proc

    def _pump(self, stream, name: str) -> None:
        try:
            for raw in iter(stream.readline, b""):
                self._last_output = time.monotonic()
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                for part in line.split("\r"):
                    if part:
                        self._on_output(part, name)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _terminate(self, proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return
        timeout = max(1.0, float(self.policy.stop_timeout_s))
        if IS_WINDOWS:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
            return
        # POSIX: SIGINT (lets Python scripts clean up the radio) -> SIGTERM -> SIGKILL
        for sig, wait_for in ((signal.SIGINT, timeout * 0.6), (signal.SIGTERM, timeout * 0.4)):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                proc.wait(wait_for)
                return
            except subprocess.TimeoutExpired:
                continue
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            pass

    def _sleep(self, seconds: float) -> None:
        """Interruptible sleep (stop/restart requests wake it early)."""
        self._wake.wait(max(0.0, seconds))

    def _run_loop(self) -> None:
        backoff = max(0.1, float(self.policy.backoff_initial_s))
        try:
            while self._want_running:
                self._wake.clear()
                self._restart_requested = False
                self._set_state(State.STARTING)
                try:
                    spec = self._factory()
                    for note in spec.info:
                        self._event("info", note)
                    for note in spec.notes:
                        self._event("warning", note)
                    proc = self._spawn(spec)
                except Exception as exc:  # bad script path, interpreter missing...
                    self._event("error", f"Failed to start: {exc}")
                    self.last_exit_code = None
                    run_time = 0.0
                    manual = False
                else:
                    self._proc = proc
                    self.started_at = time.monotonic()
                    self._last_output = self.started_at
                    readers = [
                        threading.Thread(target=self._pump, args=(proc.stdout, "stdout"), daemon=True),
                        threading.Thread(target=self._pump, args=(proc.stderr, "stderr"), daemon=True),
                    ]
                    for r in readers:
                        r.start()
                    self._event("info", f"Started {spec.description or spec.argv[0]} (PID {proc.pid})")
                    self._set_state(State.RUNNING)

                    stalled = False
                    stop_seen = False
                    while proc.poll() is None:
                        self._wake.wait(0.25)
                        if not self._want_running or self._restart_requested:
                            stop_seen = not self._want_running
                            break
                        stall = self.policy.stall_timeout_s
                        if stall and time.monotonic() - self._last_output > stall:
                            stalled = True
                            self._event("warning", f"No output for {stall}s - treating the script as hung")
                            break
                    # A stop followed quickly by start behaves like a manual restart
                    manual = self._restart_requested or stop_seen
                    if proc.poll() is None:
                        self._set_state(State.STOPPING)
                        self._terminate(proc)
                    for r in readers:
                        r.join(2)
                    run_time = time.monotonic() - (self.started_at or time.monotonic())
                    code = proc.returncode
                    self.last_exit_code = 1 if stalled else code
                    self._proc = None
                    if self._want_running and not manual:
                        self._event("warning" if code else "info", f"Script exited with code {code} after {run_time:.1f}s")

                if not self._want_running:
                    break
                if manual:
                    self._event("info", "Restarting (requested)")
                    backoff = max(0.1, float(self.policy.backoff_initial_s))
                    continue

                if self.last_exit_code == EXIT_CONFIG_ERROR:
                    self._event("error", "Not restarting: the script has an error a restart can't fix "
                                "(see the message above).")
                    self._want_running = False
                    self._set_state(State.FAILED)
                    return

                policy = self.policy.restart_policy
                failed = self.last_exit_code not in (0,)
                if policy == RESTART_NEVER or (policy == RESTART_ON_FAILURE and not failed):
                    self._want_running = False
                    self._set_state(State.FAILED if failed else State.STOPPED)
                    return

                now = time.monotonic()
                self._restart_times.append(now)
                window = max(1, self.policy.restart_window_s)
                while self._restart_times and now - self._restart_times[0] > window:
                    self._restart_times.popleft()
                limit = self.policy.max_restarts
                if limit and len(self._restart_times) > limit:
                    self._event("error", f"Gave up: {len(self._restart_times)} restarts within {window}s")
                    self._want_running = False
                    self._set_state(State.FAILED)
                    return

                if run_time >= self.policy.stable_after_s:
                    backoff = max(0.1, float(self.policy.backoff_initial_s))
                self.restart_count += 1
                self._set_state(State.BACKOFF)
                self._event("info", f"Restarting in {backoff:.1f}s (restart #{self.restart_count})")
                self._sleep(backoff)
                backoff = min(backoff * 2, max(backoff, float(self.policy.backoff_max_s)))
        finally:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                self._terminate(proc)
            self._proc = None
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                relaunch = self._want_running and self.state != State.FAILED
                if not relaunch and self.state != State.FAILED:
                    self._set_state(State.STOPPED)
                if relaunch:  # start() raced with shutdown: keep going
                    self._thread = threading.Thread(target=self._run_loop, name="supervisor", daemon=True)
                    self._thread.start()
