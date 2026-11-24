"""PyInstaller entry point (both the GUI and the CLI executables use it)."""

import multiprocessing
import sys

from rfstudio.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
