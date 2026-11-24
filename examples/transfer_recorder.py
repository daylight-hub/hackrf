"""Scheduled IQ recorder (hackrf_transfer example).

Records fixed-length IQ captures with the `hackrf_transfer` command-line tool.
RF Script Studio puts its bundled HackRF tools on PATH, so `hackrf_transfer`
is found without any extra setup.
"""

import datetime as dt
import os
import shutil
import subprocess
import sys
import time

# [Capture]
FREQUENCY = 915e6            # @unit Hz
SAMPLE_RATE = 10e6           # @unit Hz
DURATION_S = 2.0             # Length of each recording in seconds
LNA_GAIN = 24                # @range 0..40 @step 8
VGA_GAIN = 20                # @range 0..62 @step 2
AMP = False                  # RF amplifier
ANTENNA_POWER = False        # Bias tee

# [Schedule]
CONTINUOUS = True            # Keep recording on a loop
INTERVAL_S = 30              # Pause between recordings
DRY_RUN = True               # Print the command instead of running it

# [Storage]
OUTPUT_DIR = "captures"
KEEP_LAST = 20               # Delete older captures beyond this many (0 = keep all)


def record_once(index):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUTPUT_DIR, f"{stamp}_{int(FREQUENCY)}Hz.cs8")
    cmd = [
        shutil.which("hackrf_transfer") or "hackrf_transfer",
        "-r", path, "-f", str(int(FREQUENCY)), "-s", str(int(SAMPLE_RATE)),
        "-n", str(int(SAMPLE_RATE * DURATION_S)),
        "-l", str(LNA_GAIN), "-g", str(VGA_GAIN),
        "-a", "1" if AMP else "0", "-p", "1" if ANTENNA_POWER else "0",
    ]
    print(f"[{index}] {' '.join(cmd)}")
    if DRY_RUN:
        return
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)
    print(f"[{index}] saved {path} ({os.path.getsize(path) / 1e6:.1f} MB)")
    prune()


def prune():
    if KEEP_LAST <= 0:
        return
    files = sorted(f for f in os.listdir(OUTPUT_DIR) if f.endswith(".cs8"))
    for old in files[:-KEEP_LAST]:
        os.remove(os.path.join(OUTPUT_DIR, old))


def main():
    index = 1
    while True:
        record_once(index)
        if not CONTINUOUS:
            break
        index += 1
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
