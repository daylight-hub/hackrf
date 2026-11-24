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

    m = re.search(r"No module named '([^']+)'", text)
    if m:
        mod = m.group(1).split(".")[0]
        return Diagnosis(
            f"The script needs the Python package “{mod}”",
            f"“{mod}” isn't included in this build. Add a line “{mod}” to scripts/requirements.txt in the "
            "repository and push (the next build bundles it), or choose a Python interpreter that has it in "
            "Settings → Python.", ACTION_SETTINGS, "Open Settings", err)

    if "syntaxerror" in low or "indentationerror" in low:
        m = re.search(r'line (\d+)', text)
        where = f" on line {m.group(1)}" if m else ""
        return Diagnosis(f"The script has a syntax error{where}",
                         "Fix the script and press Start again.", ACTION_EDIT, "Edit script", err)

    if "permissionerror" in low or "read-only file system" in low:
        return Diagnosis(
            "The script isn't allowed to write a file",
            "It tried to save somewhere it has no permission. Relative paths are saved in the working folder; "
            "use one of those or a folder in your home directory.", ACTION_FOLDER, "Open working folder", err)

    if "filenotfounderror" in low:
        return Diagnosis("The script couldn't find a file it needs",
                         "Relative paths are looked up in the working folder. Put the file there or use a "
                         "full path.", ACTION_FOLDER, "Open working folder", err)

    if exit_code is not None and exit_code < 0:
        return Diagnosis("The script was terminated", f"It was stopped by signal {-exit_code}.", None, "", err)

    return Diagnosis("The script stopped with an error", err or f"Exit code {exit_code}.", None, "", err)
