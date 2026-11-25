"""Turn a script's error output into a plain-English explanation + next step."""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import paths

ACTION_DRIVERS = "drivers"     # open Device page / install drivers
ACTION_DEVICE = "device"       # open Device page / detect
ACTION_SETTINGS = "settings"   # open Settings (e.g. Python interpreter)
ACTION_EDIT = "edit"           # open the script in an editor
ACTION_FOLDER = "folder"       # open the working folder


@dataclass
class Diagnosis:
    title: str
    detail: str
    action: str | None = None
    action_label: str = ""
    error_line: str = ""


def _last_error_line(lines: list[str]) -> str:
    for line in reversed(lines):
        s = line.strip()
        if not s or s.startswith(("[rfstudio]", "File ", "^", "~")):
            continue
        if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b", s) or ":" in s:
            return s
    return lines[-1].strip() if lines else ""


_PIP_RE = re.compile(r"\bpip3?(?:\.exe)?\s+install\s+(.+)", re.I)
_REQ_TOKEN = re.compile(r"^[A-Za-z][\w.\-]*(\[[\w,\-]+\])?([<>=!~]=?[\w.*]+)?$")
_STOP_WORDS = {"and", "or", "then", "to", "first", "&&", "||", ";", "|", "please", "before", "again"}


def missing_packages(lines: list[str]) -> list[str]:
    """Packages a script reported as missing ("No module named 'x'", "pip install x y")."""
    found: list[str] = []
    for line in lines:
        for m in re.finditer(r"No module named '([^']+)'", line):
            found.append(m.group(1).split(".")[0])
        m = _PIP_RE.search(line)
        if m:
            for tok in re.split(r"[\s,]+", m.group(1).strip().strip("`'\".")):
                tok = tok.strip("`'\".")
                if not tok or tok.lower() in _STOP_WORDS:
                    if found and tok.lower() in _STOP_WORDS:
                        break
                    continue
                if tok.startswith("-"):
                    continue
                if _REQ_TOKEN.match(tok):
                    found.append(re.split(r"[<>=!~\[]", tok, maxsplit=1)[0])
    seen: list[str] = []
    for f in found:
        if f.lower() not in [s.lower() for s in seen]:
            seen.append(f)
    return seen


def fatal_reason(lines: list[str], run_time: float, exit_code: int | None) -> str | None:
    """Should the supervisor stop restarting? (restarting can't fix a missing package)"""
    if run_time > 20:  # it ran for a while: a later crash is worth retrying
        return None
    pkgs = missing_packages(lines)
    if pkgs:
        return f"the script needs Python package(s) {', '.join(pkgs)}"
    return None


def diagnose(lines: list[str], exit_code: int | None = None) -> Diagnosis | None:
    """`lines` are the last stderr/stdout lines of a failed run."""
    if not lines and exit_code in (None, 0):
        return None
    text = "\n".join(lines)
    low = text.lower()
    err = _last_error_line(lines)
    win = paths.IS_WINDOWS

    if "access denied" in low or "insufficient permissions" in low or "hackrf_error_libusb" in low \
            or "libusb error" in low or "-1000" in low and "hackrf" in low:
        if win:
            detail = ("Windows found the HackRF but no program can open it yet. Install the WinUSB driver: "
                      "Device page → Install / repair USB driver, choose the HackRF in Zadig and click "
                      "Install/Replace Driver. Then unplug and re-plug the board.")
        else:
            detail = ("The HackRF is visible but your user isn't allowed to open it yet. On the Device page "
                      "click Install HackRF drivers (once), then unplug and re-plug the board. If you were "
                      "just added to the plugdev group, log out and back in.")
        return Diagnosis("No permission to open the HackRF", detail, ACTION_DRIVERS, "Open Device page", err)

    if "busy" in low and ("hackrf" in low or "devicy" in low or "resource" in low):
        return Diagnosis(
            "The HackRF is in use by another program",
            "Only one program can use the radio at a time. Close other SDR software (hackrf_transfer, GQRX, "
            "SDR++, another copy of this script) and stop the background service if you installed it, then "
            "press Start.", ACTION_DEVICE, "Open Device page", err)

    if "no hackrf devices attached" in low or "hackrf_error_not_found" in low or "not attached to host" in low:
        return Diagnosis(
            "No HackRF found",
            "Plug the HackRF in (preferably straight into the computer, not a hub) and wait a few seconds. "
            "Use Device → Detect devices to check it shows up.", ACTION_DEVICE, "Open Device page", err)

    if "libhackrf could not be loaded" in low or ("libhackrf" in low and "cannot open shared object" in low):
        return Diagnosis(
            "The HackRF library isn't installed",
            "Install the HackRF drivers from the Device page, then press Start again.",
            ACTION_DRIVERS, "Open Device page", err)

    pkgs = missing_packages(lines)
    if pkgs:
        from . import pycompat

        missing = [p for p in pkgs if not pycompat.is_available(p)]
        names = ", ".join(f"“{p}”" for p in pkgs)
        if missing:
            detail = (f"Not included in this build: {', '.join(missing)}. Add "
                      f"{'it' if len(missing) == 1 else 'them'} to scripts/requirements.txt in the repository "
                      "(one per line) and push - the next build bundles "
                      f"{'it' if len(missing) == 1 else 'them'}. Or choose a Python interpreter that has "
                      f"{'it' if len(missing) == 1 else 'them'} in Settings → Python.")
        else:
            detail = ("This build includes them, but the script's own check still failed. It may check for a "
                      "specific version or submodule - see the console. Using your own Python interpreter "
                      "(Settings → Python) is a quick workaround.")
        return Diagnosis(f"The script needs Python package{'s' if len(pkgs) > 1 else ''} {names}", detail,
                         ACTION_SETTINGS, "Open Settings", err)

    if "syntaxerror" in low or "indentationerror" in low:
        m = re.search(r'line (\d+)', text)
        where = f" on line {m.group(1)}" if m else ""
        return Diagnosis(f"The script has a syntax error{where}",
                         "Fix the script and press Start again.", ACTION_EDIT, "Edit script", err)

    if "permissionerror" in low or "read-only file system" in low:
        return Diagnosis(
            "The script isn't allowed to write a file",
            "It tried to save somewhere it has no permission. Save files with relative paths or next to the "
            "script (Path(__file__).parent) - both end up in the working folder - or in your home directory.",
            ACTION_FOLDER, "Open working folder", err)

    if "filenotfounderror" in low:
        return Diagnosis("The script couldn't find a file it needs",
                         "Relative paths are looked up in the working folder. Put the file there or use a "
                         "full path.", ACTION_FOLDER, "Open working folder", err)

    if exit_code is not None and exit_code < 0:
        return Diagnosis("The script was terminated", f"It was stopped by signal {-exit_code}.", None, "", err)

    return Diagnosis("The script stopped with an error", err or f"Exit code {exit_code}.", None, "", err)
