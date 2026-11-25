"""Make the packaged app's `sys.executable` behave like a Python interpreter.

Inside the packaged app, a script's `sys.executable` is `rfstudio-cli`. Scripts
often use it like `python`:

* ``subprocess.check_call([sys.executable, "-m", "pip", "install", "numpy", "scipy"])``
  -> answered from the bundle: "already satisfied" when the packages are
  included, or a clear error naming the missing ones (nothing can be installed
  into a packaged app);
* ``os.execv(sys.executable, [sys.executable] + sys.argv)`` (restart itself)
  -> runs the script again through the runner, keeping the app's option values;
* ``[sys.executable, "other.py"]``, ``-m module``, ``-c code``.
"""

from __future__ import annotations

import importlib.util
import os
import re
import runpy
import sys

BUNDLED_PACKAGES = ("numpy", "scipy", "pyhackrf2")
_INTERP_FLAGS = {"-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO", "-q", "-b", "-bb", "-d", "-i", "-v", "-x"}
_FLAGS_WITH_ARG = {"-W", "-X"}
# pip name -> import name
_IMPORT_NAMES = {"scikit-learn": "sklearn", "pyyaml": "yaml", "pillow": "PIL", "opencv-python": "cv2",
                 "opencv-python-headless": "cv2", "pyserial": "serial", "beautifulsoup4": "bs4",
                 "python-dateutil": "dateutil", "pyusb": "usb", "pyzmq": "zmq", "paho-mqtt": "paho"}


def looks_like_python_call(argv: list[str]) -> bool:
    if not argv:
        return False
    first = argv[0]
    return first in _INTERP_FLAGS or first in _FLAGS_WITH_ARG or first in ("-m", "-c") or \
        (first.lower().endswith((".py", ".pyw")) and os.path.isfile(first))


def import_name(requirement: str) -> str:
    name = re.split(r"[<>=!~;\[ ]", requirement.strip(), maxsplit=1)[0].strip().lower()
    return _IMPORT_NAMES.get(name, name.replace("-", "_"))


def is_available(requirement: str) -> bool:
    try:
        return importlib.util.find_spec(import_name(requirement)) is not None
    except (ImportError, ValueError):
        return False


def _requirements(args: list[str]) -> list[str]:
    reqs: list[str] = []
    it = iter(args)
    for a in it:
        if a in ("-r", "--requirement"):
            path = next(it, "")
            try:
                with open(path, encoding="utf-8") as fh:
                    reqs += [ln.split("#")[0].strip() for ln in fh if ln.split("#")[0].strip()
                             and not ln.strip().startswith("-")]
            except OSError:
                pass
        elif a in ("-t", "--target", "--prefix", "--root", "-i", "--index-url", "--extra-index-url", "-c",
                   "--constraint", "-f", "--find-links", "--python-version", "--platform"):
            next(it, None)
        elif not a.startswith("-"):
            reqs.append(a)
    return reqs


def pip(args: list[str]) -> int:
    cmd = args[0] if args else ""
    if cmd == "install":
        reqs = _requirements(args[1:])
        missing = [r for r in reqs if not is_available(r)]
        for r in reqs:
            if r not in missing:
                print(f"Requirement already satisfied: {r} (included in RF Script Studio)")
        if missing:
            print("ERROR: packages can't be installed into the RF Script Studio app. Not included in this build: "
                  + ", ".join(missing), file=sys.stderr)
            print("To add them: list them in scripts/requirements.txt in the repository and push (the next build "
                  "bundles them), or choose a Python interpreter that has them in Settings -> Python.",
                  file=sys.stderr)
            return 1
        return 0
    if cmd in ("list", "freeze"):
        from importlib import metadata

        for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
            name, ver = dist.metadata["Name"], dist.version
            print(f"{name}=={ver}" if cmd == "freeze" else f"{name:<30} {ver}")
        return 0
    if cmd == "show" and len(args) > 1:
        from importlib import metadata

        try:
            print(f"Name: {args[1]}\nVersion: {metadata.version(args[1])}")
            return 0
        except metadata.PackageNotFoundError:
            print(f"WARNING: Package(s) not found: {args[1]}", file=sys.stderr)
            return 1
    print("pip is not available inside RF Script Studio.", file=sys.stderr)
    return 1


def run(argv: list[str]) -> int:
    """Emulate `python <argv>` inside the packaged app."""
    args = list(argv)
    while args and (args[0] in _INTERP_FLAGS or args[0] in _FLAGS_WITH_ARG or
                    re.match(r"^-[WX].+", args[0])):
        flag = args.pop(0)
        if flag in _FLAGS_WITH_ARG and args:
            args.pop(0)
    if not args:
        print("Interactive Python isn't available inside RF Script Studio.", file=sys.stderr)
        return 2

    from . import runner

    if args[0] == "-m" and len(args) > 1:
        module, rest = args[1], args[2:]
        if module == "pip":
            return pip(rest)
        if module == "ensurepip":
            return 0
        runner.install_hackrf_compat()
        runner.install_pyhackrf2_fixes()
        sys.argv = [module, *rest]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        return 0
    if args[0] == "-c" and len(args) > 1:
        runner.install_hackrf_compat()
        runner.install_pyhackrf2_fixes()
        sys.argv = ["-c", *args[2:]]
        exec(compile(args[1], "<string>", "exec"), {"__name__": "__main__", "__builtins__": __builtins__})
        return 0

    script, rest = args[0], args[1:]
    origin = os.environ.get("RFSTUDIO_SCRIPT", "")
    patched = os.environ.get("RFSTUDIO_PATCHED_SCRIPT", "")
    same = bool(origin) and os.path.normcase(os.path.abspath(script)) == os.path.normcase(os.path.abspath(origin))
    run_file = patched if same and patched and os.path.isfile(patched) else script
    runner.main(["--script", run_file, "--origin", os.path.abspath(script), "--", *rest])
    return 0
