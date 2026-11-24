"""Frequency scanner with reports (command-line example).

    python frequency_scanner.py --scan            run the scanner (keeps running)
    python frequency_scanner.py --history         show recent detections
    python frequency_scanner.py --top 10          most active frequencies
    python frequency_scanner.py --pairs           frequencies that often appear together
    python frequency_scanner.py --persistent      signals seen in many scans
    python frequency_scanner.py --blocks          list blocked frequencies
    python frequency_scanner.py --block-freq HZ   block a frequency

RF Script Studio turns these flags into buttons (Commands page) and shows the
output as tables. Variables below become the options panel - there are no
section headers, so the app groups them by name.
"""

import argparse
import json
import random
import time
from collections import Counter
from itertools import combinations
from pathlib import Path

SIMULATE = True              # Fake detections instead of using a HackRF
START_FREQ = 300e6           # Start of the scan range
STOP_FREQ = 930e6            # End of the scan range
STEP_FREQ = 5e6              # Tuning step
SAMPLE_RATE = 10e6
LNA_GAIN = 24
VGA_GAIN = 20
AMP_ENABLE = False           # 14 dB front-end amplifier
THRESHOLD_DB = -45.0         # Report signals stronger than this
DWELL_MS = 50                # Time spent on each step
SCAN_INTERVAL_S = 2.0        # Pause between sweeps
PERSISTENT_MIN_HITS = 3      # Scans a signal must appear in to count as persistent
VERBOSE = True               # Print every detection
DATA_FILE = "scan_data.json"

DATA = Path(__file__).parent / DATA_FILE
KNOWN = [315.0e6, 433.92e6, 868.35e6, 915.0e6, 462.5625e6]


def load():
    if DATA.exists():
        return json.loads(DATA.read_text())
    return {"scans": [], "blocked": []}


def save(db):
    DATA.write_text(json.dumps(db))


def scan_once(db):
    found = []
    if SIMULATE:
        for f in KNOWN:
            if f not in db["blocked"] and START_FREQ <= f <= STOP_FREQ and random.random() < 0.7:
                found.append((f, round(random.uniform(-70, -30), 1)))
    else:  # pragma: no cover - needs hardware
        from pyhackrf2 import HackRF
        import numpy as np
        sdr = HackRF()
        sdr.sample_rate, sdr.lna_gain, sdr.vga_gain, sdr.amplifier_on = SAMPLE_RATE, LNA_GAIN, VGA_GAIN, AMP_ENABLE
        f = START_FREQ
        while f <= STOP_FREQ:
            sdr.center_freq = f
            x = sdr.read_samples(int(SAMPLE_RATE * DWELL_MS / 1000))
            p = 10 * np.log10(np.mean(np.abs(x) ** 2) + 1e-12)
            if p > THRESHOLD_DB and f not in db["blocked"]:
                found.append((f, round(float(p), 1)))
            f += STEP_FREQ
        sdr.close()
    hits = [(f, p) for f, p in found if p > THRESHOLD_DB]
    db["scans"].append({"t": time.time(), "hits": hits})
    db["scans"] = db["scans"][-500:]
    return hits


def run_scanner():
    db = load()
    print(f"Scanning {START_FREQ / 1e6:.1f}-{STOP_FREQ / 1e6:.1f} MHz ({'simulation' if SIMULATE else 'HackRF'})")
    try:
        while True:
            hits = scan_once(db)
            save(db)
            if VERBOSE:
                for f, p in hits:
                    print(f"detected {f / 1e6:9.4f} MHz  {p:6.1f} dB")
            print(f"sweep {len(db['scans'])}: {len(hits)} signal(s)")
            time.sleep(SCAN_INTERVAL_S)
    except KeyboardInterrupt:
        save(db)
        print("Scanner stopped.")


def show_history(n=15):
    db = load()
    print("=== Recent detections ===")
    print(f"{'Time':<10}  {'Frequency (MHz)':>15}  {'Power (dB)':>10}")
    print("-" * 40)
    rows = [(s["t"], f, p) for s in db["scans"] for f, p in s["hits"]][-n:]
    for t, f, p in reversed(rows):
        print(f"{time.strftime('%H:%M:%S', time.localtime(t)):<10}  {f / 1e6:>15.4f}  {p:>10.1f}")
    print()
    print(f"Total scans: {len(db['scans'])}")
    print(f"Total detections: {sum(len(s['hits']) for s in db['scans'])}")


def show_top(n=10):
    db = load()
    c = Counter(f for s in db["scans"] for f, _ in s["hits"])
    print(f"=== Top {n} frequencies ===")
    print("Rank  Frequency (MHz)  Hits  Share")
    total = sum(c.values()) or 1
    for i, (f, k) in enumerate(c.most_common(n), 1):
        print(f"{i:<4}  {f / 1e6:<15.4f}  {k:<4}  {100 * k / total:.1f}%")


def show_pairs():
    db = load()
    c = Counter()
    for s in db["scans"]:
        fs = sorted({f for f, _ in s["hits"]})
        c.update(combinations(fs, 2))
    print("=== Frequency pairs ===")
    print("| Frequency A (MHz) | Frequency B (MHz) | Together |")
    print("|---|---|---|")
    for (a, b), k in c.most_common(10):
        print(f"| {a / 1e6:.4f} | {b / 1e6:.4f} | {k} |")


def show_persistent():
    db = load()
    c = Counter(f for s in db["scans"] for f in {f for f, _ in s["hits"]})
    print("=== Persistent signals ===")
    print(f"Minimum hits: {PERSISTENT_MIN_HITS}")
    print()
    print("Frequency (MHz)  Scans  Seen")
    for f, k in sorted(c.items()):
        if k >= PERSISTENT_MIN_HITS:
            print(f"{f / 1e6:<15.4f}  {k:<5}  {100 * k / max(1, len(db['scans'])):.0f}%")


def show_blocks():
    db = load()
    print("=== Blocked frequencies ===")
    if not db["blocked"]:
        print("No frequencies are blocked.")
    for f in db["blocked"]:
        print(f"{f / 1e6:.4f} MHz")


def block(freq):
    db = load()
    db["blocked"] = sorted(set(db["blocked"]) | {float(freq)})
    save(db)
    print(f"Blocked {float(freq) / 1e6:.4f} MHz ({len(db['blocked'])} blocked)")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--scan", action="store_true", help="Run the scanner continuously")
    p.add_argument("--history", action="store_true", help="Show recent detections")
    p.add_argument("--top", type=int, nargs="?", const=10, metavar="N", help="Show the N most active frequencies")
    p.add_argument("--pairs", action="store_true", help="Show frequencies that appear together")
    p.add_argument("--persistent", action="store_true", help="List signals seen in many scans")
    p.add_argument("--blocks", action="store_true", help="List blocked frequencies")
    p.add_argument("--block-freq", type=float, metavar="HZ", help="Block a frequency (Hz)")
    a = p.parse_args()
    if a.history:
        show_history()
    elif a.top is not None:
        show_top(a.top)
    elif a.pairs:
        show_pairs()
    elif a.persistent:
        show_persistent()
    elif a.blocks:
        show_blocks()
    elif a.block_freq:
        block(a.block_freq)
    else:  # --scan (or no flag)
        run_scanner()


if __name__ == "__main__":
    main()
