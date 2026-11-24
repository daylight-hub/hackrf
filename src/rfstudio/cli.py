"""Command-line entry point.

    rfstudio                      open the desktop app
    rfstudio --minimized          start in the system tray
    rfstudio --headless --script my.py      supervise without a window
    rfstudio --probe              list HackRF devices / backends
    rfstudio --list-options my.py           show what the GUI would display
"""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rfstudio", description=f"{APP_NAME} {__version__}")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    p.add_argument("--minimized", action="store_true", help="start hidden in the system tray")
    p.add_argument("--script", help="script to open / supervise")
    p.add_argument("--autorun", action="store_true", help="start the script immediately")
    p.add_argument("--headless", action="store_true", help="supervise the script without a GUI")
    p.add_argument("--probe", action="store_true", help="detect HackRF devices and backends")
    p.add_argument("--json", action="store_true", help="machine-readable output (with --probe)")
    p.add_argument("--hackrf-dir", action="append", default=[], help="extra folder with HackRF tools/libs")
    p.add_argument("--list-options", metavar="SCRIPT", help="print the options discovered in a script")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Internal: execute a user script (used by the supervisor).
    if argv[:1] == ["--run-script"]:
        from . import runner
        runner.main(argv[1:])
        return 0

    args = _parser().parse_args(argv)

    if args.probe:
        from . import config, hackrf_backend, session
        dirs = args.hackrf_dir or session.hackrf_search_dirs(config.load())
        res = hackrf_backend.probe_in_process(dirs)
        if args.json:
            print(res.to_json())
        else:
            print(f"pyhackrf2: {'OK' if res.pyhackrf2_available else 'unavailable'} {res.pyhackrf2_error}")
            print(f"library:   {res.library or '-'}")
            print(f"hackrf_info:     {res.hackrf_info or 'not found'}")
            print(f"hackrf_transfer: {res.hackrf_transfer or 'not found'}")
            for d in res.devices:
                print(f"  device #{d.index}: {d.board or 'HackRF'} serial={d.serial} fw={d.firmware}")
            if not res.devices:
                print("  no devices found")
            for e in res.errors:
                print(f"  ! {e}")
        return 0

    if args.list_options:
        from . import script_model
        spec = script_model.parse_script(args.list_options)
        for err in spec.errors:
            print(f"error: {err}")
        for section in spec.sections:
            print(f"[{section}]")
            for v in spec.variables:
                if v.section == section:
                    flag = " (hidden)" if v.hidden else ""
                    print(f"  {v.name:<24} {v.kind:<8} = {v.source_text}{flag}  {v.description}")
        if spec.commands and spec.commands.commands:
            print("[Commands]")
            for c in spec.commands.commands:
                val = f" <{c.metavar or c.type_name}>" if c.takes_value else ""
                print(f"  {c.flag + val:<26} {c.kind:<8} {c.help}")
        return 1 if spec.errors else 0

    if args.headless:
        from . import headless
        return headless.run(args.script)

    from .gui.app import run_gui
    return run_gui(args)
