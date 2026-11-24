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


def _library_candidates(hackrf_dir: str | None) -> list[str]:
    import ctypes.util

    dirs = [d for d in (hackrf_dir, os.environ.get("RFSTUDIO_HACKRF_DIR")) if d]
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
        for d in (hackrf_dir, os.environ.get("RFSTUDIO_HACKRF_DIR")):
            if d and os.path.isdir(d) and hasattr(os, "add_dll_directory"):
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


def _line_buffer_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True, errors="replace")  # type: ignore[union-attr]
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
    origin = ns.origin or ns.script
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
    tb = exc.__traceback__
    here = os.path.abspath(__file__)
    internal = {"cli.py", "launcher.py", "__main__.py"}
    while tb is not None:
        fname = tb.tb_frame.f_code.co_filename
        if os.path.abspath(fname) != here and os.path.basename(fname) not in internal:
            break
        tb = tb.tb_next
    traceback.print_exception(type(exc), exc, tb if tb is not None else exc.__traceback__)
    sys.stderr.flush()


if __name__ == "__main__":
    main()
