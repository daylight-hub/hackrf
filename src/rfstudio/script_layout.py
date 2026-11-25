"""Make the generated options menu logical.

Applied after parsing (`script_model.parse_script`):

* infers units (Hz, dB, s, ms, %) and HackRF ranges (LNA 0-40/8, VGA 0-62/2,
  TX VGA 0-47) from variable names when the script gives no hints;
* flags settings that switch transmitting on (`danger`);
* groups variables that have no section header into logical sections
  (Radio, Transmit, Receive, ...) - scripts with their own headers keep them;
* puts lists/dicts into a collapsed "Advanced" section when grouping.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from . import script_model as sm

CATEGORY_ORDER = ["Radio", "Transmit", "Receive", "Detection", "Signal", "Timing", "Files & storage",
                  "Logging & display", "Network", "General", "Advanced"]

_RULES: list[tuple[str, set[str]]] = [
    ("Transmit", {"tx", "transmit", "transmitter", "transmitting", "txvga", "ptt", "beacon", "payload", "jam",
                  "repeat", "repeats", "replay", "broadcast", "dry", "hop", "hopping", "carrier"}),
    # radio hardware words win over generic signal words ("ANTENNA_POWER" is hardware)
    ("Radio", {"antenna", "bias", "tee", "amp", "amplifier", "hackrf", "serial", "device", "ppm"}),
    # explicit time words win over what is being timed ("SCAN_INTERVAL_S" is timing)
    ("Timing", {"interval", "delay", "duration", "timeout", "period", "seconds", "sec", "secs", "ms", "sleep",
                "wait", "retry", "retries", "duty", "cycle", "schedule", "minutes", "hours", "dwell", "hold",
                "time", "times", "elapsed"}),
    ("Detection", {"threshold", "squelch", "hits", "persistent", "detect", "detection", "trigger", "sensitivity",
                   "noise", "floor", "snr", "peak", "peaks", "min", "minimum"}),
    ("Receive", {"rx", "receive", "receiver", "receiving", "lna", "vga", "demod", "demodulator", "listen", "scan",
                 "scanner", "sweep", "monitor"}),
    ("Files & storage", {"path", "dir", "directory", "folder", "file", "filename", "files", "csv", "backup",
                         "backups", "save", "saving", "record", "recording", "capture", "captures", "export",
                         "keep", "archive", "database", "sqlite", "json"}),
    ("Logging & display", {"verbose", "debug", "log", "logging", "print", "show", "quiet", "display", "plot",
                           "color", "colour", "console", "report", "reports", "alert", "alerts", "notify", "beep"}),
    ("Network", {"host", "port", "url", "ip", "address", "server", "mqtt", "api", "token", "key", "password",
                 "user", "username", "webhook", "http", "socket", "udp", "tcp"}),
    ("Timing", {"continuous", "forever", "loop", "count", "max", "iterations"}),
    ("Signal", {"modulation", "mode", "waveform", "deviation", "baud", "symbol", "power", "amplitude", "tone",
                "fm", "am", "ook", "ask", "fsk", "gfsk", "psk", "level", "fft", "block", "window", "average",
                "averaging", "decimation", "samples", "num", "bits", "pattern", "message", "data"}),
    ("Radio", {"freq", "frequency", "frequencies", "center", "centre", "sample", "rate", "bandwidth", "bw",
               "filter", "gain", "channel", "channels", "band", "offset", "lo", "span", "step", "hz", "khz", "mhz",
               "ghz", "if", "baseband", "simulate", "simulation", "index"}),
]

_ACRONYMS = {"tx", "rx", "rf", "if", "iq", "fm", "am", "lna", "vga", "fft", "hackrf", "usb", "ptt", "sdr",
             "ook", "fsk", "psk", "csv", "api", "ip", "url", "mqtt", "udp", "tcp", "gps", "ppm"}


def tokens(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return [t for t in re.split(r"[_\W]+", spaced.lower()) if t]


def nice_title(text: str) -> str:
    """'RADIO SETTINGS' -> 'Radio settings', keeping acronyms ('TX CONFIG' -> 'TX config')."""
    letters = [c for c in text if c.isalpha()]
    if not letters or not all(c.isupper() for c in letters):
        return text
    words = re.split(r"(\s+|_+)", text)
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if not w.strip() or set(w) <= {"_"}:
            out.append(" " if "_" in w else w)
        elif lw in _ACRONYMS:
            out.append(w.upper())
        else:
            out.append(lw.capitalize() if i == 0 else lw)
    return "".join(out)


def category_for(var: sm.ScriptVariable) -> str:
    if var.kind == sm.KIND_LITERAL:
        return "Advanced"
    toks = set(tokens(var.name))
    for cat, words in _RULES:
        if toks & words:
            return cat
    return "General"


def _is_numeric(var: sm.ScriptVariable) -> bool:
    return var.kind in (sm.KIND_INT, sm.KIND_FLOAT) or (
        var.kind == sm.KIND_CHOICE and all(isinstance(c, (int, float)) and not isinstance(c, bool)
                                           for c in (var.choices or [])))


def infer_unit(var: sm.ScriptVariable) -> str:
    if not _is_numeric(var) or var.unit:
        return var.unit
    toks = tokens(var.name)
    last = toks[-1] if toks else ""
    val = var.default if isinstance(var.default, (int, float)) else 0
    for t, unit in (("ghz", "GHz"), ("mhz", "MHz"), ("khz", "kHz"), ("hz", "Hz"), ("dbm", "dBm"), ("db", "dB"),
                    ("ms", "ms"), ("pct", "%"), ("percent", "%")):
        if t in toks:
            return unit
    if last in ("s", "sec", "secs", "seconds"):
        return "s"
    if last in ("min", "mins", "minutes"):
        return "min"
    if ({"freq", "frequency", "center", "centre", "bandwidth", "bw", "offset", "step", "span", "deviation"}
            & set(toks)) and abs(val) >= 1000:
        return "Hz"
    if {"sample", "rate"} <= set(toks) or "samplerate" in toks:
        return "Hz"
    if "gain" in toks or "lna" in toks or "vga" in toks or "txvga" in toks:
        return "dB"
    return ""


def infer_range(var: sm.ScriptVariable) -> None:
    if var.kind not in (sm.KIND_INT, sm.KIND_FLOAT) or var.minimum is not None or var.maximum is not None:
        return
    toks = set(tokens(var.name))
    default = var.default

    def apply(lo: float, hi: float, step: float | None) -> None:
        if lo <= default <= hi:
            var.minimum, var.maximum = lo, hi
            if step and var.step is None and (default - lo) % step == 0:
                var.step = step

    if "lna" in toks:
        apply(0, 40, 8)
    elif "txvga" in toks or ({"tx", "transmit"} & toks and ({"vga", "gain"} & toks)):
        apply(0, 47, 1)
    elif "vga" in toks:
        apply(0, 62, 2)
    elif var.unit == "Hz" and ({"freq", "frequency", "center", "centre"} & toks):
        apply(1e6, 7.25e9, None)
    elif var.unit == "Hz" and ({"sample", "rate"} <= toks or "samplerate" in toks):
        apply(2e6, 20e6, None)


def is_dangerous(var: sm.ScriptVariable) -> bool:
    """Booleans that switch transmitting / RF amplification on."""
    if var.kind != sm.KIND_BOOL:
        return False
    toks = set(tokens(var.name))
    if {"no", "disable", "disabled", "dry", "simulate", "simulation", "test"} & toks:
        return False
    return bool({"tx", "transmit", "transmitting", "transmitter", "ptt", "jam", "broadcast"} & toks)


_UNIT_TOKENS = {"hz", "khz", "mhz", "ghz", "s", "sec", "secs", "seconds", "ms", "db", "dbm", "pct", "percent"}


def tidy_label(var: sm.ScriptVariable) -> None:
    """'HOP_SPEED_HZ' -> 'Hop speed' when the unit is shown next to the value anyway."""
    if var.label or not var.unit:
        return
    toks = tokens(var.name)
    if len(toks) > 1 and toks[-1] in _UNIT_TOKENS:
        var.label = sm.humanize("_".join(toks[:-1]))


def organize(spec: sm.ScriptSpec) -> None:
    if not spec.variables:
        return
    for var in spec.variables:
        var.unit = infer_unit(var)
        infer_range(var)
        var.danger = is_dangerous(var)
        tidy_label(var)
        var.section = nice_title(var.section)
        var.category = category_for(var)

    loose = [v for v in spec.variables if not v.explicit_section]
    if len(loose) < 4:
        return  # few loose variables: keep them in "General" as written
    for v in loose:
        v.section = v.category

    # Order: inferred sections (logical order) first, then the script's own sections
    inferred = [c for c in CATEGORY_ORDER if any(v.section == c and not v.explicit_section for v in spec.variables)]
    explicit = list(OrderedDict.fromkeys(v.section for v in spec.variables if v.explicit_section))
    order = {name: i for i, name in enumerate(inferred + [s for s in explicit if s not in inferred])}
    spec.variables.sort(key=lambda v: (order.get(v.section, 99), v.lineno))
