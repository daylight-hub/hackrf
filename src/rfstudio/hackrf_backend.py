"""HackRF integration: device detection (pyhackrf2 + hackrf_info) and hackrf_transfer."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

from . import paths

BOARD_NAMES = {0: "Jellybean", 1: "Jawbreaker", 2: "HackRF One", 3: "rad1o", 4: "HackRF One r9"}


@dataclass
class DeviceInfo:
    serial: str = ""
    board: str = ""
    firmware: str = ""
    hardware_revision: str = ""
    part_id: str = ""
    index: int = 0
    source: str = ""


@dataclass
class ProbeResult:
    pyhackrf2_available: bool = False
    pyhackrf2_error: str = ""
    library: str = ""
    hackrf_info: str = ""
    hackrf_transfer: str = ""
    tools_version: str = ""
    lib_version: str = ""
    devices: list[DeviceInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> ProbeResult:
        data = json.loads(text)
        devices = [DeviceInfo(**d) for d in data.pop("devices", [])]
        return cls(devices=devices, **data)


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if paths.IS_WINDOWS else 0


def find_tool(name: str, search_dirs: list[str]) -> str | None:
    return paths.which(name, search_dirs)


def parse_hackrf_info(text: str) -> tuple[list[DeviceInfo], str, str]:
    """Parse `hackrf_info` output -> (devices, tools version, lib version)."""
    devices: list[DeviceInfo] = []
    tools_v = lib_v = ""
    current: DeviceInfo | None = None
    for line in text.splitlines():
        line = line.strip()
        if m := re.match(r"hackrf_info version:\s*(.+)", line):
            tools_v = m.group(1).strip()
        elif m := re.match(r"libhackrf version:\s*(.+)", line):
            lib_v = m.group(1).strip()
        elif line.startswith("Found HackRF"):
            current = DeviceInfo(source="hackrf_info", index=len(devices))
            devices.append(current)
        elif current is not None and ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip().lower(), val.strip()
            if key == "index":
                try:
                    current.index = int(val)
                except ValueError:
                    pass
            elif key == "serial number":
                current.serial = val
            elif key.startswith("board id"):
                m = re.search(r"\((.+)\)", val)
                current.board = m.group(1) if m else val
            elif key.startswith("firmware version"):
                current.firmware = val
            elif key.startswith("part id"):
                current.part_id = val
            elif key.startswith("hardware revision"):
                current.hardware_revision = val
    return devices, tools_v, lib_v


def probe_in_process(search_dirs: list[str]) -> ProbeResult:
    """Detect devices. Runs inside a helper process (see `probe`)."""
    from . import runner

    res = ProbeResult()
    lib_dir = next((d for d in search_dirs if paths.find_hackrf_lib(d)), None)
    runner.install_hackrf_compat(lib_dir)

    try:
        import pyhackrf2  # noqa: F401
        from pyhackrf2 import HackRF

        res.pyhackrf2_available = True
        res.library = runner.loaded_library or ""
        try:
            serials = HackRF.enumerate()
            for i, s in enumerate(serials):
                res.devices.append(DeviceInfo(serial=s, index=i, source="pyhackrf2"))
        except Exception as exc:  # noqa: BLE001
            res.errors.append(f"pyhackrf2 enumerate failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - ImportError, OSError from CDLL...
        res.pyhackrf2_error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__

    res.hackrf_info = find_tool("hackrf_info", search_dirs) or ""
    res.hackrf_transfer = find_tool("hackrf_transfer", search_dirs) or ""
    if res.hackrf_info:
        try:
            out = subprocess.run([res.hackrf_info], capture_output=True, text=True, timeout=15,
                                 env=paths.clean_child_env(search_dirs), creationflags=_creationflags())
            devs, tv, lv = parse_hackrf_info(out.stdout + "\n" + out.stderr)
            res.tools_version, res.lib_version = tv, lv
            by_serial = {d.serial: d for d in res.devices}
            for d in devs:
                if d.serial in by_serial:
                    tgt = by_serial[d.serial]
                    tgt.board, tgt.firmware = d.board, d.firmware
                    tgt.part_id, tgt.hardware_revision = d.part_id, d.hardware_revision
                    tgt.source = "pyhackrf2 + hackrf_info"
                else:
                    res.devices.append(d)
            both = (out.stdout + out.stderr).lower()
            if "resource busy" in both or "hackrf_error_busy" in both:
                res.errors.append("The HackRF is busy - another program (or your script) is using it.")
            if "access denied" in both or "insufficient permissions" in both:
                res.errors.append("A HackRF is connected but you don't have permission to open it. Click "
                                  "'Install HackRF drivers' below, then re-plug the board (and log out/in once)."
                                  if not paths.IS_WINDOWS else
                                  "A HackRF is connected but its USB driver isn't usable. Click "
                                  "'Install / repair USB driver' below.")
        except (OSError, subprocess.SubprocessError) as exc:
            res.errors.append(f"hackrf_info failed: {exc}")
    for d in res.devices:
        if d.serial:
            d.serial = d.serial.lower()
    return res


def probe(search_dirs: list[str], timeout: float = 25.0) -> ProbeResult:
    """Run detection in a child process so a native crash can't take the GUI down."""
    cmd = [*paths.cli_command(), "--probe", "--json"]
    for d in search_dirs:
        cmd += ["--hackrf-dir", d]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             env=paths.clean_child_env(), creationflags=_creationflags())
    except subprocess.TimeoutExpired:
        return ProbeResult(errors=["Device probe timed out"])
    except OSError as exc:
        return ProbeResult(errors=[f"Could not run probe: {exc}"])
    for line in reversed(out.stdout.strip().splitlines()):
        if line.startswith("{"):
            try:
                return ProbeResult.from_json(line)
            except (ValueError, TypeError):
                break
    return ProbeResult(errors=[f"Probe failed (exit {out.returncode}): {out.stderr.strip()[-400:]}"])


# ---------------------------------------------------------------- transfer

LNA_STEPS = list(range(0, 41, 8))
VGA_STEPS = list(range(0, 63, 2))


@dataclass
class TransferParams:
    mode: str = "rx"                # rx | tx
    file: str = ""
    frequency_hz: float = 433.92e6
    sample_rate_hz: float = 10e6
    baseband_filter_hz: float = 0.0  # 0 = automatic
    lna_gain: int = 16
    vga_gain: int = 20
    tx_vga_gain: int = 0
    amp: bool = False
    antenna_power: bool = False
    num_samples: int = 0            # 0 = unlimited
    repeat: bool = False
    serial: str = ""
    extra_args: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransferParams:
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


def build_transfer_command(tool: str, p: TransferParams) -> list[str]:
    if not p.file:
        raise ValueError("Choose a file to record to / transmit from")
    if not (1e6 <= p.frequency_hz <= 7.25e9):
        raise ValueError("Frequency must be between 1 MHz and 7.25 GHz")
    if not (2e6 <= p.sample_rate_hz <= 20e6):
        raise ValueError("Sample rate must be between 2 and 20 Msps")
    cmd = [tool, "-r" if p.mode == "rx" else "-t", p.file,
           "-f", str(int(p.frequency_hz)), "-s", str(int(p.sample_rate_hz))]
    if p.baseband_filter_hz:
        cmd += ["-b", str(int(p.baseband_filter_hz))]
    if p.mode == "rx":
        lna = max(0, min(40, int(p.lna_gain) // 8 * 8))
        vga = max(0, min(62, int(p.vga_gain) // 2 * 2))
        cmd += ["-l", str(lna), "-g", str(vga)]
    else:
        cmd += ["-x", str(max(0, min(47, int(p.tx_vga_gain))))]
        if p.repeat:
            cmd.append("-R")
    cmd += ["-a", "1" if p.amp else "0"]
    if p.antenna_power:
        cmd += ["-p", "1"]
    if p.num_samples:
        cmd += ["-n", str(int(p.num_samples))]
    if p.serial:
        cmd += ["-d", p.serial]
    if p.extra_args.strip():
        import shlex
        cmd += shlex.split(p.extra_args, posix=not paths.IS_WINDOWS)
    return cmd
