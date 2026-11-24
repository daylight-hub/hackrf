# Your scripts

Put your HackRF Python scripts here. Every `.py` file in this folder:

- is bundled into the Linux and Windows builds,
- shows up in the app's script picker, and
- gets an options panel generated from its top-level variables.

Extra Python packages your scripts need go in `requirements.txt` next to them.
(`numpy` and `pyhackrf2` are always included.)

Check what the app will detect:

    python -m rfstudio --list-options scripts/your_script.py
