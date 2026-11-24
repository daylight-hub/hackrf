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
import types

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


def run_script(script: str, origin: str, argv: list[str], hackrf_dir: str | None = None) -> None:
    install_hackrf_compat(hackrf_dir)
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
    run_script(ns.script, ns.origin or ns.script, extra, ns.hackrf_dir)


if __name__ == "__main__":
    main()
