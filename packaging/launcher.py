"""PyInstaller entry point (both the GUI and the CLI executables use it)."""

import multiprocessing
import sys

from rfstudio.cli import main

if __name__ == "__main__":
    if any(a.startswith("--multiprocessing-fork") for a in sys.argv[1:]):
        # A worker started by a user script's `multiprocessing`: load the HackRF
        # library fix, the app's option values and the script itself first.
        from rfstudio import runner

        runner.prepare_multiprocessing_child()
    multiprocessing.freeze_support()
    sys.exit(main())
