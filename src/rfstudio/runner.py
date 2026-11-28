"""Script runner: executes a (patched) user script as ``__main__``.

This file deliberately depends on the standard library only, so it can be run
directly by an *external* Python interpreter as well as by the bundled one::

    python runner.py --script patched.py --origin original.py [-- args...]

It also installs a small compatibility layer for ``pyhackrf2``: that package
hard-codes ``CDLL("libhackrf.so.0")``, which only exists on Linux. The shim
redirects the load to the right library for the platform (``hackrf-0.dll`` /
``hackrf.dll`` on Windows, bundled or system copies on Linux) and calls ``hackrf_init()``.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
import types

# Exit code for problems a restart can't fix (syntax error, missing module):
# the supervisor stops instead of restart-looping. 78 = EX_CONFIG (sysexits.h)
EXIT_CONFIG_ERROR = 78

_HACKRF_ALIASES = {"libhackrf.so.0", "libhackrf.so", "libhackrf", "hackrf", "hackrf.dll", "hackrf-0.dll",
                   "libhackrf.dll", "libhackrf-0.dll"}
_WINDOWS_NAMES = ("hackrf-0.dll", "hackrf.dll", "libhackrf.dll", "libhackrf-0.dll")
_installed = False
loaded_library: str | None = None


def _search_dirs(hackrf_dir: str | None) -> list[str]:
    """Explicit dir, $RFSTUDIO_HACKRF_DIR, then the folder bundled with a frozen build."""
    dirs = [hackrf_dir, os.environ.get("RFSTUDIO_HACKRF_DIR")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(os.path.join(meipass, "hackrf"))
    out: list[str] = []
    for d in dirs:
        if d and os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def _library_candidates(hackrf_dir: str | None) -> list[str]:
    import ctypes.util

    dirs = _search_dirs(hackrf_dir)
    cands: list[str] = []
    if sys.platform.startswith("win"):
        for d in dirs:
            cands += [os.path.join(d, n) for n in _WINDOWS_NAMES]
        cands += list(_WINDOWS_NAMES)
    elif sys.platform == "darwin":
        cands += ["libhackrf.0.dylib", "libhackrf.dylib",
                  "/opt/homebrew/lib/libhackrf.dylib", "/usr/local/lib/libhackrf.dylib"]
        cands += [os.path.join(d, "libhackrf.0.dylib") for d in dirs]
    else:
        # Prefer the system library (matches the system libusb + udev setup)
        cands += ["libhackrf.so.0", "libhackrf.so"]
        found = ctypes.util.find_library("hackrf")
        if found:
            cands.append(found)
        cands += [os.path.join(d, "libhackrf.so.0") for d in dirs]
    seen: list[str] = []
    for c in cands:
        if c not in seen:
            seen.append(c)
    return seen


def install_hackrf_compat(hackrf_dir: str | None = None) -> None:
    """Make ``CDLL("libhackrf.so.0")`` work on every platform."""
    global _installed
    if _installed:
        return
    import ctypes

    if sys.platform.startswith("win"):
        for d in _search_dirs(hackrf_dir):
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass

    original_init = ctypes.CDLL.__init__

    def patched_init(self, name, *args, **kwargs):  # type: ignore[no-untyped-def]
        global loaded_library
        if isinstance(name, str) and os.path.basename(name) in _HACKRF_ALIASES:
            errors = []
            for cand in _library_candidates(hackrf_dir):
                try:
                    original_init(self, cand, *args, **kwargs)
                except OSError as exc:
                    errors.append(f"{cand}: {exc}")
                    continue
                loaded_library = cand
                try:
                    init = self.hackrf_init
                    init.restype = ctypes.c_int
                    init()
                except (AttributeError, OSError):
                    pass
                return
            raise OSError(
                "libhackrf could not be loaded. Install the HackRF host tools "
                "(Linux: `sudo apt install hackrf`; Windows: use the bundled build "
                "and install the WinUSB driver with Zadig). Tried:\n  " + "\n  ".join(errors)
            )
        original_init(self, name, *args, **kwargs)

    ctypes.CDLL.__init__ = patched_init  # type: ignore[method-assign]
    _installed = True


# ------------------------------------------------------------ pyhackrf2 fixes

def _patch_pyhackrf2(mod: types.ModuleType) -> None:
    """Fix bugs in pyhackrf2 1.0.x without touching the installed package.

    * `read_samples()` treated the signed int8 IQ bytes as unsigned (subtracting
      1+1j), giving a constant DC offset and wrong power; it also busy-waited at
      100% CPU. Samples are now scaled to [-1, 1) exactly like hackrf_transfer data.
    * the `lna_gain` getter returned itself -> RecursionError.
    """
    hackrf_cls = getattr(mod, "HackRF", None)
    if hackrf_cls is None or getattr(hackrf_cls, "_rfstudio_patched", False):
        return
    import time

    try:
        import numpy as np
    except ImportError:  # pragma: no cover - pyhackrf2 requires numpy anyway
        return
    mode_off = mod.TransceiverMode.HACKRF_TRANSCEIVER_MODE_OFF

    def read_samples(self, num_samples: int = 131072):  # type: ignore[no-untyped-def]
        if not num_samples:
            return np.array([], dtype=np.complex128)
        self._sample_count_limit = int(2 * num_samples)
        self.start_rx()
        while self._transceiver_mode != mode_off:
            time.sleep(0.0005)
        self.stop_rx()
        raw = np.frombuffer(bytes(self.buffer), dtype=np.int8)
        iq = raw.astype(np.float64).view(np.complex128)
        iq /= 128.0
        return iq

    hackrf_cls.read_samples = read_samples
    lna = hackrf_cls.__dict__.get("lna_gain")
    if isinstance(lna, property) and lna.fset is not None:
        hackrf_cls.lna_gain = property(lambda self: getattr(self, "_lna_gain", 16), lna.fset, None, lna.__doc__)
    hackrf_cls._rfstudio_patched = True


class _PatchingLoader:
    """Wraps the real loader (source or PyInstaller-frozen) and patches after exec."""

    def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
        self._inner = inner

    def create_module(self, spec):  # type: ignore[no-untyped-def]
        return self._inner.create_module(spec)

    def exec_module(self, module) -> None:  # type: ignore[no-untyped-def]
        self._inner.exec_module(module)
        _patch_pyhackrf2(module)

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


class _PyHackRF2Finder:
    """Meta-path hook: apply `_patch_pyhackrf2` right after pyhackrf2 is imported."""

    def find_spec(self, name, path=None, target=None):  # type: ignore[no-untyped-def]
        if name != "pyhackrf2":
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None and spec.loader is not None:
                spec.loader = _PatchingLoader(spec.loader)
                return spec
        return None


def install_pyhackrf2_fixes() -> None:
    if "pyhackrf2" in sys.modules:
        _patch_pyhackrf2(sys.modules["pyhackrf2"])
        return
    if not any(isinstance(f, _PyHackRF2Finder) for f in sys.meta_path):
        sys.meta_path.insert(0, _PyHackRF2Finder())


def run_script(script: str, origin: str, argv: list[str], hackrf_dir: str | None = None) -> None:
    install_hackrf_compat(hackrf_dir)
    install_pyhackrf2_fixes()
    with open(script, "rb") as fh:
        source = fh.read()
    code = compile(source, origin, "exec", dont_inherit=True)

    module = types.ModuleType("__main__")
    module.__file__ = origin
    module.__builtins__ = __builtins__  # type: ignore[attr-defined]
    module.__dict__["__cached__"] = None
    sys.modules["__main__"] = module
    sys.argv = [origin, *argv]
    script_dir = os.path.dirname(os.path.abspath(origin))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    exec(code, module.__dict__)


STOP_TOKEN = b"__rfstudio_stop__"


def _interrupt_main() -> None:
    """Raise KeyboardInterrupt in the script's main thread, waking it from time.sleep()."""
    import signal
    import threading

    try:
        if hasattr(signal, "pthread_kill"):  # POSIX: deliver to the main thread
            signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
        else:  # Windows: runs CPython's handler, which also wakes time.sleep()
            signal.raise_signal(signal.SIGINT)
    except (AttributeError, OSError, ValueError):
        import _thread
        _thread.interrupt_main()


def _dump_stacks() -> None:
    import faulthandler

    try:
        sys.stdout.flush()
        print("[rfstudio] where the script is right now (most recent call first):", file=sys.stderr, flush=True)
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError, RuntimeError):
        pass


def _start_event_listener() -> None:
    """Windows: wait on the app's named Stop / Dump events and on the app process itself.

    Stop (or the app disappearing) -> KeyboardInterrupt in the script, like Ctrl+C on Linux.
    Dump -> print where every thread is (for troubleshooting a script that prints nothing)."""
    stop_name = os.environ.get("RFSTUDIO_STOP_EVENT")
    if not stop_name or not sys.platform.startswith("win"):
        return
    import ctypes
    import threading
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenEventW.restype = wintypes.HANDLE
    k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.WaitForMultipleObjects.restype = wintypes.DWORD
    k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL,
                                           wintypes.DWORD]
    synchronize = 0x00100000
    handles: list[int] = []
    kinds: list[str] = []
    for kind, name in (("stop", stop_name), ("dump", os.environ.get("RFSTUDIO_DUMP_EVENT"))):
        if name:
            h = k32.OpenEventW(synchronize, False, name)
            if h:
                handles.append(h)
                kinds.append(kind)
    try:
        ppid = int(os.environ.get("RFSTUDIO_PARENT_PID", "0"))
    except ValueError:
        ppid = 0
    if ppid:
        h = k32.OpenProcess(synchronize, False, ppid)
        if h:
            handles.append(h)
            kinds.append("parent")
    if not handles:
        return
    array = (wintypes.HANDLE * len(handles))(*handles)

    def watch() -> None:
        while True:
            r = k32.WaitForMultipleObjects(len(handles), array, False, 0xFFFFFFFF)
            if r >= len(handles):  # WAIT_FAILED etc.
                return
            if kinds[r] == "dump":
                _dump_stacks()
                continue
            _interrupt_main()  # stop requested, or the app went away
            return

    threading.Thread(target=watch, name="rfstudio-stop", daemon=True).start()


def _register_posix_dump() -> None:
    """SIGUSR1 -> print every thread's stack. Done in C by faulthandler, so it works even
    while the script is stuck inside a C call (e.g. waiting on the radio)."""
    try:
        import faulthandler
        import signal

        if hasattr(signal, "SIGUSR1") and sys.stderr is not None:
            faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True, chain=False)
    except (ImportError, ValueError, OSError, RuntimeError, AttributeError):
        pass


def _start_stop_listener() -> None:
    """Windows has no SIGINT for windowless processes: the supervisor writes a stop
    token to our stdin instead (EOF = the app went away). Either one raises
    KeyboardInterrupt in the script, exactly like pressing Ctrl+C, so its
    cleanup (closing the radio, switching TX off) runs."""
    import io
    import threading

    stream = sys.stdin.buffer if sys.stdin is not None else None
    sys.stdin = io.StringIO("")  # the script itself gets an empty stdin
    if stream is None:
        return

    def watch() -> None:
        try:
            for line in iter(stream.readline, b""):
                if line.strip() == STOP_TOKEN:
                    break
        except (OSError, ValueError):
            pass
        _interrupt_main()

    threading.Thread(target=watch, name="rfstudio-stop", daemon=True).start()


def install_exec_compat(force: bool = False) -> None:
    """Windows has no real exec(): os.execv() starts a new process and ends this one, so the
    supervisor would think the script stopped and start a second copy. Instead run the new
    program as a child, wait for it (forwarding Stop), and exit with its code."""
    if not (force or sys.platform.startswith("win")):
        return
    import subprocess

    def _exec(path, args, env=None):  # type: ignore[no-untyped-def]
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except (AttributeError, OSError, ValueError):
                pass
        pipe_mode = os.environ.get("RFSTUDIO_STOP_PIPE") == "1"
        child = subprocess.Popen([os.fspath(path), *[os.fspath(a) for a in list(args)[1:]]],
                                 env=env, stdin=subprocess.PIPE if pipe_mode else subprocess.DEVNULL)
        try:
            code = child.wait()
        except KeyboardInterrupt:  # Stop pressed: the new copy got the same Stop event / token
            if pipe_mode and child.stdin is not None:
                try:
                    child.stdin.write(STOP_TOKEN + b"\n")
                    child.stdin.flush()
                    child.stdin.close()
                except (OSError, ValueError):
                    pass
            try:
                code = child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
                code = 130
        os._exit(code)

    os.execv = lambda path, args: _exec(path, args)  # type: ignore[assignment]
    os.execve = lambda path, args, env: _exec(path, args, env)  # type: ignore[assignment]


# --------------------------------------------- child processes (Windows parity)

def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def install_child_output_capture(force: bool = False) -> None:
    """Windows: programs the script starts (hackrf_transfer, helper scripts...) would
    write to an invisible console instead of the app. Give them our stdout/stderr
    (like Linux does) and an empty stdin so they can't swallow the Stop request."""
    if not (force or sys.platform.startswith("win")):
        return
    import subprocess

    if getattr(subprocess.Popen, "_rfstudio_capture", False):
        return
    original_init = subprocess.Popen.__init__

    def __init__(self, args, *pos, **kw):  # type: ignore[no-untyped-def]
        if not pos:  # (positional bufsize/executable/stdin... is rare; leave those alone)
            if kw.get("stdout") is None and _fd_ok(1):
                kw["stdout"] = 1
            if kw.get("stderr") is None and _fd_ok(2):
                kw["stderr"] = 2
            if kw.get("stdin") is None:
                kw["stdin"] = subprocess.DEVNULL
        original_init(self, args, *pos, **kw)

    subprocess.Popen.__init__ = __init__  # type: ignore[method-assign]
    subprocess.Popen._rfstudio_capture = True  # type: ignore[attr-defined]


def _fd_ok(fd: int) -> bool:
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


def install_multiprocessing_support(force: bool = False) -> None:
    """Parent side, for `multiprocessing` with the spawn start method (always used on Windows).

    * A packaged Windows app normally doesn't tell workers where the main script is,
      so functions defined in the script can't be found in the worker -> add it.
    * Workers would get no stdout/stderr on Windows -> share ours, like Linux.
    """
    if not (force or sys.platform.startswith("win")):
        return
    import multiprocessing.spawn as spawn

    if getattr(spawn, "_rfstudio_patched", False):
        return
    original = spawn.get_preparation_data

    def get_preparation_data(name):  # type: ignore[no-untyped-def]
        data = original(name)
        if "init_main_from_path" not in data and "init_main_from_name" not in data:
            main = sys.modules.get("__main__")
            path = getattr(main, "__file__", None)
            if path:
                data["init_main_from_path"] = os.path.abspath(path)
        return data

    spawn.get_preparation_data = get_preparation_data
    spawn._rfstudio_patched = True

    if not sys.platform.startswith("win"):
        return
    try:
        import msvcrt
        import multiprocessing.popen_spawn_win32 as psw
        import subprocess
    except ImportError:
        return
    real = psw._winapi

    class _WinApi:
        def __getattr__(self, name):  # type: ignore[no-untyped-def]
            return getattr(real, name)

        def CreateProcess(self, app, cmd, pattr, tattr, inherit, flags, env, cwd, startup):  # noqa: N802
            try:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESTDHANDLES
                devnull = os.open(os.devnull, os.O_RDONLY)
                handles = [msvcrt.get_osfhandle(devnull), msvcrt.get_osfhandle(1), msvcrt.get_osfhandle(2)]
                for h in handles:
                    os.set_handle_inheritable(h, True)
                si.hStdInput, si.hStdOutput, si.hStdError = handles
                return real.CreateProcess(app, cmd, pattr, tattr, True, flags, env, cwd, si)
            except OSError:
                return real.CreateProcess(app, cmd, pattr, tattr, inherit, flags, env, cwd, startup)

    psw._winapi = _WinApi()


def _fixup_main_from_path(main_path: str) -> None:
    """Worker side: load the script as __mp_main__ - with the app's option values
    (the patched copy) while keeping __file__ pointing at the real script."""
    origin = os.environ.get("RFSTUDIO_SCRIPT", "")
    patched = os.environ.get("RFSTUDIO_PATCHED_SCRIPT", "")
    source = patched if patched and origin and os.path.isfile(patched) and _same_file(main_path, origin) \
        else main_path
    with open(source, "rb") as fh:
        code = compile(fh.read(), main_path, "exec", dont_inherit=True)
    module = types.ModuleType("__mp_main__")
    module.__file__ = main_path
    module.__builtins__ = __builtins__  # type: ignore[attr-defined]
    sys.modules["__main__"] = sys.modules["__mp_main__"] = module
    folder = os.path.dirname(os.path.abspath(main_path))
    if folder not in sys.path:
        sys.path.insert(0, folder)
    exec(code, module.__dict__)


def prepare_multiprocessing_child() -> None:
    """Called first thing in a worker process started by the packaged app."""
    _line_buffer_stdio()
    install_hackrf_compat()
    install_pyhackrf2_fixes()
    install_child_output_capture()
    install_exec_compat()
    import multiprocessing.spawn as spawn

    spawn._fixup_main_from_path = _fixup_main_from_path  # type: ignore[attr-defined]


def _line_buffer_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            # UTF-8 like the app expects (a frozen Windows app may default to the ANSI code page)
            stream.reconfigure(encoding="utf-8", line_buffering=True, errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(args: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a script with HackRF compatibility shims")
    parser.add_argument("--script", required=True, help="file to execute (patched copy)")
    parser.add_argument("--origin", help="original script path (for __file__ and tracebacks)")
    parser.add_argument("--hackrf-dir", default=None)
    parser.add_argument("script_args", nargs=argparse.REMAINDER)
    ns = parser.parse_args(args)
    extra = ns.script_args[1:] if ns.script_args[:1] == ["--"] else ns.script_args
    _line_buffer_stdio()
    if os.environ.get("RFSTUDIO_STOP_PIPE") == "1":
        _start_stop_listener()
    _start_event_listener()
    _register_posix_dump()
    install_exec_compat(force=os.environ.get("RFSTUDIO_EXEC_COMPAT") == "1")
    install_child_output_capture(force=os.environ.get("RFSTUDIO_CHILD_CAPTURE") == "1")
    install_multiprocessing_support(force=os.environ.get("RFSTUDIO_MP_SUPPORT") == "1")
    origin = ns.origin or ns.script
    # children (multiprocessing workers, helper scripts) find the real script + the app's values
    os.environ["RFSTUDIO_SCRIPT"] = os.path.abspath(origin)
    if not _same_file(ns.script, origin):
        os.environ["RFSTUDIO_PATCHED_SCRIPT"] = os.path.abspath(ns.script)
    if os.environ.get("RFSTUDIO_SUPERVISED") == "1" and str(os.getppid()) == os.environ.get("RFSTUDIO_PARENT_PID"):
        # proves the output path works even if the script itself stays quiet
        import platform

        print(f"[rfstudio] Python {platform.python_version()} on {platform.system()} "
              f"{platform.release()} is running {os.path.basename(origin)}", flush=True)
    try:
        run_script(ns.script, origin, extra, ns.hackrf_dir)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.exit(130)  # stopped from the app: no traceback
    except (SyntaxError, ImportError) as exc:
        # Restarting can't fix these: report and exit with a distinct code
        _print_user_traceback(exc, origin)
        kind = "Syntax error" if isinstance(exc, SyntaxError) else "Missing module"
        print(f"[rfstudio] {kind} - fix the script; it will not be restarted automatically.",
              file=sys.stderr, flush=True)
        sys.exit(EXIT_CONFIG_ERROR)
    except BaseException as exc:  # noqa: BLE001
        _print_user_traceback(exc, origin)
        sys.exit(1)


def _print_user_traceback(exc: BaseException, origin: str) -> None:
    """Print the traceback starting at the user's script (hide runner frames)."""
    full = exc.__traceback__
    target = os.path.normcase(os.path.abspath(origin))
    tb = full
    while tb is not None and os.path.normcase(os.path.abspath(tb.tb_frame.f_code.co_filename)) != target:
        tb = tb.tb_next
    if tb is None:  # error before the script started (e.g. syntax error): drop runner frames only
        tb = full
        while tb is not None and os.path.basename(tb.tb_frame.f_code.co_filename) in (
                "runner.py", "cli.py", "launcher.py", "__main__.py"):
            tb = tb.tb_next
    traceback.print_exception(type(exc), exc, tb if tb is not None else full)
    sys.stderr.flush()


if __name__ == "__main__":
    main()
