#!/usr/bin/env python3
"""
Continuous HackRF Frequency Generator Sweep
===========================================

Combined continuous transmitter + multi-range AM scanner.

Supports both HackRF (TX + RX) and RTL-SDR Blog v4 (RX only).
When USE_RTLSDR = True the scanner uses rtl_power / rtl_sdr.
TX is automatically disabled if no HackRF is detected.

- Fast continuous frequency hopping transmit sweep over frequencies
  listed in frequencies.json.
- Every hour (configurable) it runs the built-in scanner to refresh
  the frequency list, then reloads the list automatically.
- All configuration variables are at the top of this file.

Designed to be portable. Requires either:
  - HackRF tools (hackrf_transfer, hackrf_sweep), or
  - RTL-SDR tools (rtl_power, rtl_sdr) when USE_RTLSDR = True.

Usage:
    python3 continuous_transmit_combined.py                  # normal mode
    python3 continuous_transmit_combined.py --scan           # run scanner once
    python3 continuous_transmit_combined.py --history        # full history stats
    python3 continuous_transmit_combined.py --top            # top frequencies
    python3 continuous_transmit_combined.py --pairs          # co-occurring pairs
    python3 continuous_transmit_combined.py --persistent     # freqs in most scans

Stop with Ctrl+C.
"""

import json
import os
import sys
import time
import subprocess
import math
import signal as os_signal
import shutil
import tempfile
import threading
import random
from datetime import datetime
from pathlib import Path
from collections import defaultdict

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None

try:
    import scipy.signal as scipy_signal
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    scipy_signal = None

# =============================================================================
# CONFIGURATION VARIABLES  (edit these for your setup)
# =============================================================================

# Paths (relative to this script by default — portable)
SCRIPT_DIR = Path(__file__).resolve().parent
JSON_PATH = SCRIPT_DIR / "frequencies.json"
BLOCKS_FILE = SCRIPT_DIR / "active_blocks.json"   # current transmit blocks (updated after each scan)

# Manually add extra frequencies here (in MHz). These are always included.
MANUAL_FREQUENCIES = [
    {"mhz": 438.00,  "bandwidth": 12.5, "step": 1.0},
    {"mhz": 2565.00, "bandwidth": 6.0, "step": 1.0},
]

# Source enable switches (True = include that source in the TX blocks)
USE_MANUAL = True          # frequencies listed in MANUAL_FREQUENCIES
USE_SCANNED = True         # top frequencies from the latest scan (frequencies.json)
USE_SMART = True           # top-ranked frequencies from the smart list (ranked_signals.json)

# Defaults used when a manual entry does not specify bandwidth or step
MANUAL_BANDWIDTH_MHZ = 12.5          # total width (± half on each side). 0 = no expansion
MANUAL_STEP_MHZ = 1.0               # step size when expanding

# How often to re-run the scanner and reload the frequency list (seconds)
UPDATE_INTERVAL_SEC = 3600          # 1 hour
SCAN_TIMEOUT_SEC = 1500             # kill a stuck scan after this many seconds

# =============================================================================
# HACKRF TRANSMIT OPTIONS  (matches working PortaPack settings)
# =============================================================================

TRANSMIT_TYPE = "rand_cw"             # rand_cw, noise, cw_sweep, fm_tone, rand_fsk, ...
TRANSMIT_SPEED_HZ = 10000             # 10 kHz
TRANSMIT_HOP_SEC = 0.005               # 10 ms per 1 MHz segment
TRANSMIT_SEGMENT_HZ = 1_000_000       # 1 MHz hops (PortaPack style)
TRANSMIT_TX_SEC = 60
TRANSMIT_SLEEP_SEC = 0          # 0 = no pause between hops (was 1s; 1s * 500 hops = ~8 min of idle)
TRANSMIT_JITTER_SEC = 0
TRANSMIT_GAIN = 47
TRANSMIT_AMP = 0                      # 0 = amp off (matches your working setting)
TRANSMIT_AMPLITUDE = 1.0

# Cluster nearby frequencies into blocks
BLOCK_GAP_MHZ = 5.0                 # group freqs if gap between them <= this
BLOCK_MARGIN_MHZ = 1.0              # extra width on each side of a cluster
BLOCK_MIN_WIDTH_MHZ = 1.0
BLOCK_MAX_WIDTH_MHZ = 24.0          # PortaPack-style cap

# Reduce clicking / hiss
CLICK_SMOOTH = True
CLICK_JITTER_FRAC = 0.25
FILL_NOISE = True
FILL_NOISE_LEVEL = 0.12

# Always include the exact target frequencies as hops
LOCK_ON_TARGETS = True

USE_TRANSMIT_OPTIONS = True

# =============================================================================
# BLOCK ASSIGNMENT (nearby frequencies + matching modulation)
# =============================================================================
BLOCK_MATCH_MODULATION = True

# =============================================================================
# MODULATION INVERSION
# =============================================================================
INVERT_MODULATIONS = [
    "fm", "am", "digital", "cw",
    "fm_capture", "hopping", "pulsed",
]

INVERT_WAVE_TYPE = "noise"
INVERT_USE_SCANNED_WAVEFORM = True
INVERT_LEVEL_MODE = "one_minus"

MOD_TO_WAVE = {
    "cw": "carrier",
    "am": "sine",
    "fm": "fm_tone",
    "nbfm": "fm_tone",
    "fm_capture": "fm_tone",
    "fsk": "rand_fsk",
    "digital": "rand_fsk",
    "hopping": "brute",
    "pulsed": "carrier",
    "noise": "noise",
    "unknown": "noise",
    "manual": None,
}

# =============================================================================
# AUTO-ADJUST BAND
# =============================================================================
AUTO_BAND_MIN_MHZ = 400.0
AUTO_BAND_MAX_MHZ = 800.0

# --- Noise-floor auto-level ---
NOISE_FLOOR_COMPENSATE = True
NOISE_FLOOR_MARGIN_DB  = 6.0
LOCAL_FLOOR_SPAN_MHZ   = 2.0

# --- Desense + FM capture ---
DESENSE_POWER_DB = 10.0
FM_CAPTURE_CHECK = True
FM_CAPTURE_RATIO_DB = 8.0

# Reference window
NOISE_FLOOR_REF_QUIET_DB = -95.0
NOISE_FLOOR_REF_LOUD_DB  = -55.0
NOISE_FLOOR_MIN_AMP      = 0.30

# --- Replicate + replay unrecognised signals ---
REPLAY_UNKNOWN      = True
REPLAY_INVERT       = True
REPLAY_SNIPPET_SEC  = 0.05
REPLAY_DIR          = SCRIPT_DIR / "captures"
CAPTURE_KEEP        = 200

# --- Keep exact frequencies ---
FORCE_WHOLE_MHZ = False

# =============================================================================
# HARDENING / AUTO-RESTART
# =============================================================================
IS_WINDOWS = (os.name == "nt")
RESTART_AFTER_HOURS = 24
RESTART_AFTER_EACH_SCAN = False
FAIL_BURSTS_BEFORE_RESET   = 2
FAIL_BURSTS_BEFORE_RESTART = 5
BACKUP_KEEP = 48

# =============================================================================
# END TRANSMIT OPTIONS
# =============================================================================

# Shared settings
SAMPLE_RATE = 8_000_000             # Hz
ANTENNA_ENABLE = 0                  # bias-tee / antenna power

# Scanner backend
USE_RTLSDR = False
RTL_GAIN = 40
RTL_DEVICE = 0

# Multi-HackRF support
HACKRF_DEVICES = []                  # empty = auto-detect all devices

# Optional: assign specific ranges to specific devices
RANGE_DEVICE_MAP = {}

# =============================================================================
# MULTI-HACKRF CONFIGURATION
# =============================================================================
ENABLE_MULTI_HACKRF_TX = True

# --- Option 1: Multi-HackRF TX Distribution ---
OPTION1_FIXED_RANGE_SPLIT = False

# --- Option 2: Phase Shift Assignment ---
OPTION2_PHASE_SHIFT_ASSIGNMENT = True

# --- Option 3: Real-time Inverse Modulation ---
OPTION3_REALTIME_INVERSE_RX_TX = False
OPTION3_INVERSE_RX_DEVICE_INDEX = 1
OPTION3_INVERSE_TX_DEVICE_INDEX = 0
OPTION3_RX_DURATION_SEC = 0.05
OPTION3_INVERSE_TX_DELAY_SEC = 0.01
OPTION3_INVERSE_TX_WAIT_SEC = 0.5

# Overlapped RX/TX (daisy-chain). True = one radio TXes while the other RXes
# the next hop, across the device band splits below (not limited to 400-800).
# False = classic sequential: listen, then TX inverse, only inside AUTO_BAND.
OPTION3_OVERLAP_RX_TX = False

# Editable band ownership by detection index (no serial needed).
# index 0 = first HackRF found, index 1 = second, etc.
DEVICE_BAND_SPLITS = [
    {"index": 0, "start_mhz": 400.0,  "stop_mhz": 2000.0},
    {"index": 1, "start_mhz": 2000.0, "stop_mhz": 6000.0},
]
# Minimum MHz separation between a live TX and a simultaneous RX to reduce desense.
# Below this, overlap falls back to sequential for that pair.
OPTION3_MIN_TX_RX_SEPARATION_MHZ = 100.0

# Phase shift (180°) for lower bands
APPLY_180_PHASE_SHIFT = True
PHASE_SHIFT_MIN_MHZ = 410.0
PHASE_SHIFT_MAX_MHZ = 800.0

# Validation: Only one option can be true at a time
if OPTION3_REALTIME_INVERSE_RX_TX:
    OPTION2_PHASE_SHIFT_ASSIGNMENT = False
    OPTION1_FIXED_RANGE_SPLIT = False
    REPLAY_UNKNOWN = False
elif OPTION2_PHASE_SHIFT_ASSIGNMENT:
    OPTION1_FIXED_RANGE_SPLIT = False
    if not APPLY_180_PHASE_SHIFT:
        OPTION2_PHASE_SHIFT_ASSIGNMENT = False

# Derived from TRANSMIT_* options
TX_TYPE = TRANSMIT_TYPE
TX_SPEED_HZ = TRANSMIT_SPEED_HZ
TX_HOP_SEC = TRANSMIT_HOP_SEC
TX_DURATION_SEC = TRANSMIT_TX_SEC
TX_SLEEP_SEC = TRANSMIT_SLEEP_SEC
TX_JITTER_SEC = TRANSMIT_JITTER_SEC
TX_GAIN = TRANSMIT_GAIN
AMP_ENABLE = TRANSMIT_AMP
TX_AMPLITUDE = TRANSMIT_AMPLITUDE
DWELL_SEC = TRANSMIT_HOP_SEC if TRANSMIT_HOP_SEC > 0 else 0.05
NUM_SAMPLES = max(8192, int(SAMPLE_RATE * max(DWELL_SEC, 0.002)))
TX_TONE_OFFSET_HZ = 0
ALWAYS_PRINT = True
LOG_FILE = SCRIPT_DIR / "generator_log.txt"
LOG_MAX_BYTES = 5_000_000
LOG_KEEP = 3
HISTORY_FILE = SCRIPT_DIR / "frequency_history.jsonl"
HISTORY_MAX_BYTES = 20_000_000
HISTORY_KEEP = 5

WHOLE_MHZ_TOLERANCE = 0.05
SCAN_ALWAYS = True

SMART_LIST_PATH = SCRIPT_DIR / "ranked_signals.json"
SMART_PREV_PATH = SCRIPT_DIR / "ranked_signals_prev.json"
SMART_TOP_N = 15
SMART_MIN_SCORE = 1.0
SMART_BLOCK_BANDWIDTH_MHZ = 1.0

W_PERSISTENCE = 3.0
W_HOPPING = 4.0
W_WAVES = 3.0
W_MOD = {"digital": 2.5, "fm": 2.0, "am": 2.0, "hopping": 3.0,
         "pulsed": 1.5, "cw": 0.5, "noise": -1.0}
W_POWER = 0.02
W_PAIR = 2.0
W_TONE = 1.0

HACKRF_TRANSFER = None

BASEBAND_FILTER_HZ = 1_750_000

PRINT_SWEEP_SUMMARY = True
MAX_FREQUENCIES = 200

# =============================================================================
# SCANNER CONFIGURATION
# =============================================================================

POWER_THRESHOLD_DB = -72.0
TOP_N_PER_RANGE = 4
CAPTURE_SECONDS = 0.5          # was 3; 3s @ 8 Msps = ~192-366 MiB and fails on Windows
LNA_GAIN = 24
VGA_GAIN = 36

TONE_MIN_HZ = 200
TONE_MAX_HZ = 3000
TONE_PROMINENCE_DB = 12

HOP_BLOCK_MS = 20
HOP_ON_THRESHOLD_RATIO = 3.0
MIN_HOPS_FOR_FLAG = 4

PAIR_SWEEPS = 12
PAIR_CORRELATION_THRESHOLD = -0.4

HIGH_POWER_DB = -58.0
OUTPUT_FILE = SCRIPT_DIR / "frequencies.json"
BACKUP_DIR = SCRIPT_DIR / "frequency_backups"

def round_freq_mhz(freq_hz):
    """Round to nearest 0.1 MHz for cleaner whole-ish numbers."""
    return round(freq_hz / 1e5) / 10.0

# =============================================================================
# RANGE ENABLE / DISABLE SWITCHES
# =============================================================================
ENABLE_RANGE_410_430   = True
ENABLE_RANGE_420_800   = True
ENABLE_RANGE_735_760   = True
ENABLE_RANGE_2300_2600 = True
ENABLE_RANGE_MANUAL    = True  # Manual frequencies range
ENABLE_RANGE_3200_3750 = True
ENABLE_RANGE_3920_3935 = True

# Capture switches
CAPTURE_RANGE_410_430   = True
CAPTURE_RANGE_420_800   = True
CAPTURE_RANGE_735_760   = True
CAPTURE_RANGE_2300_2600 = True
CAPTURE_RANGE_MANUAL    = True  # Capture manual frequencies
CAPTURE_RANGE_3200_3750 = True
CAPTURE_RANGE_3920_3935 = True

# Transmit enable / disable switches
TX_ENABLE_RANGE_410_430   = True
TX_ENABLE_RANGE_420_800   = True
TX_ENABLE_RANGE_735_760   = True
TX_ENABLE_RANGE_2300_2600 = False
TX_ENABLE_RANGE_MANUAL    = True  # Enable manual TX
TX_ENABLE_RANGE_3200_3750 = False
TX_ENABLE_RANGE_3920_3935 = False

TX_ENABLED_BY_RANGE = {
    "410-430":   TX_ENABLE_RANGE_410_430,
    "420-800":   TX_ENABLE_RANGE_420_800,
    "735-760":   TX_ENABLE_RANGE_735_760,
    "2300-2600": TX_ENABLE_RANGE_2300_2600,
    "manual":    TX_ENABLE_RANGE_MANUAL,
    "3200-3750": TX_ENABLE_RANGE_3200_3750,
    "3920-3935": TX_ENABLE_RANGE_3920_3935,
}

RANGES = [
    {"name": "410-430",    "start_mhz": 410,  "stop_mhz": 430,  "enabled": ENABLE_RANGE_410_430,  "capture": CAPTURE_RANGE_410_430,
     "mode": "both", "bin_hz": 100000, "threshold_db": -78.0},
    {"name": "420-800",    "start_mhz": 420,  "stop_mhz": 800,  "enabled": ENABLE_RANGE_420_800,  "capture": CAPTURE_RANGE_420_800,
     "mode": "both", "bin_hz": 250000, "threshold_db": POWER_THRESHOLD_DB},
    {"name": "735-760",    "start_mhz": 735,  "stop_mhz": 760,  "enabled": ENABLE_RANGE_735_760,  "capture": CAPTURE_RANGE_735_760,
     "mode": "both", "bin_hz": 100000, "threshold_db": POWER_THRESHOLD_DB},
    {"name": "2300-2600",  "start_mhz": 2300, "stop_mhz": 2600, "enabled": ENABLE_RANGE_2300_2600, "capture": CAPTURE_RANGE_2300_2600,
     "mode": "both", "bin_hz": 250000, "threshold_db": POWER_THRESHOLD_DB},
    {"name": "manual",     "start_mhz": 400,  "stop_mhz": 2600, "enabled": ENABLE_RANGE_MANUAL,   "capture": CAPTURE_RANGE_MANUAL,
     "mode": "both", "bin_hz": 100000, "threshold_db": POWER_THRESHOLD_DB},
    {"name": "3200-3750",  "start_mhz": 3200, "stop_mhz": 3750, "enabled": ENABLE_RANGE_3200_3750, "capture": CAPTURE_RANGE_3200_3750,
     "mode": "both", "bin_hz": 250000, "threshold_db": POWER_THRESHOLD_DB},
    {"name": "3920-3935",  "start_mhz": 3920, "stop_mhz": 3935, "enabled": ENABLE_RANGE_3920_3935, "capture": CAPTURE_RANGE_3920_3935,
     "mode": "both", "bin_hz": 50000,  "threshold_db": POWER_THRESHOLD_DB},
]

# Ensure backup / capture directories exist
BACKUP_DIR.mkdir(exist_ok=True)
try:
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

def range_name_for_mhz(mhz):
    """Best matching enabled scan-range name for a frequency, or None."""
    try:
        mhz = float(mhz)
    except (TypeError, ValueError):
        return None
    best = None
    best_width = None
    for rng in RANGES:
        if rng["name"] == "manual":
            continue
        if rng["start_mhz"] <= mhz <= rng["stop_mhz"]:
            width = rng["stop_mhz"] - rng["start_mhz"]
            if best is None or width < best_width:
                best = rng["name"]
                best_width = width
    return best


def is_tx_enabled(range_name, mhz=None):
    """Return True if this frequency may be transmitted.

    Specific TX_ENABLE_RANGE_* switches always win, regardless of whether the
    entry came from manual, scanned, or smart. A manual frequency at 2565 MHz
    is blocked when TX_ENABLE_RANGE_2300_2600 is False.
    """
    if mhz is not None:
        try:
            mhz = float(mhz)
        except (TypeError, ValueError):
            mhz = None
    if mhz is not None:
        for rng in RANGES:
            if rng["name"] == "manual":
                continue
            if rng["start_mhz"] <= mhz <= rng["stop_mhz"]:
                if not TX_ENABLED_BY_RANGE.get(rng["name"], True):
                    return False
    if not range_name and mhz is not None:
        range_name = range_name_for_mhz(mhz)
    if range_name and not TX_ENABLED_BY_RANGE.get(range_name, True):
        return False
    return True


def in_auto_band(mhz):
    """True if mhz is inside the single auto-adjust band."""
    try:
        return AUTO_BAND_MIN_MHZ <= float(mhz) <= AUTO_BAND_MAX_MHZ
    except (TypeError, ValueError):
        return False


def cluster_into_blocks(freq_list):
    """Group nearby frequencies into blocks for HackRF-style hopping."""
    if not freq_list:
        return []
    mhz_list = sorted(set(round(f["mhz"], 3) for f in freq_list))
    groups = []
    current = [mhz_list[0]]
    for mhz in mhz_list[1:]:
        if mhz - current[-1] <= BLOCK_GAP_MHZ:
            current.append(mhz)
        else:
            groups.append(current)
            current = [mhz]
    groups.append(current)

    result = []
    for members in groups:
        lo = min(members)
        hi = max(members)
        center = (lo + hi) / 2.0
        width = (hi - lo) + 2 * BLOCK_MARGIN_MHZ
        width = max(BLOCK_MIN_WIDTH_MHZ, min(BLOCK_MAX_WIDTH_MHZ, width))
        half = width / 2.0
        result.append({
            "center_mhz": round(center, 3),
            "width_mhz": round(width, 3),
            "start_mhz": round(center - half, 3),
            "stop_mhz": round(center + half, 3),
            "members": members,
        })
    return result


def _amp_at(mhz, member_amps, fallback):
    """Amplitude at mhz, linearly interpolated between detected members."""
    if not member_amps:
        return fallback
    pts = member_amps
    if len(pts) == 1:
        return pts[0][1]
    if mhz <= pts[0][0]:
        return pts[0][1]
    if mhz >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        x0, a0 = pts[i - 1]
        x1, a1 = pts[i]
        if x0 <= mhz <= x1:
            if x1 == x0:
                return a1
            frac = (mhz - x0) / (x1 - x0)
            return round(a0 + frac * (a1 - a0), 3)
    return fallback


def build_block_hop_list(blocks):
    """Build hop segments for every block, then interleave them."""
    seg_mhz = TRANSMIT_SEGMENT_HZ / 1e6 if TRANSMIT_SEGMENT_HZ > 0 else 1.0
    per_block = []
    for blk in blocks:
        start = blk["start_mhz"]
        stop = blk["stop_mhz"]
        center = blk["center_mhz"]
        tx_wave = blk.get("tx_wave", TRANSMIT_TYPE)
        tx_amp = blk.get("tx_amplitude", TX_AMPLITUDE)
        member_amps = blk.get("member_amps")
        cap = blk.get("capture_file")
        mod = blk.get("modulation", "unknown")
        device_serial = blk.get("assigned_device_serial")

        avg_lvl = blk.get("avg_waveform_level", 0.0)
        def _seg(mhz, locked=False):
            return {
                "mhz": round(mhz, 3),
                "hz": int(round(mhz * 1_000_000)),
                "block_center": center,
                "range": f"block@{center:.1f}",
                "wave_type": tx_wave,
                "amplitude": _amp_at(mhz, member_amps, tx_amp),
                "capture_file": cap,
                "modulation": mod,
                "mod_waveform_level": avg_lvl,
                "locked": locked,
                "device_serial": device_serial,
            }

        n_down = int((center - start) / seg_mhz)
        n_up = int((stop - center) / seg_mhz)
        segs = []
        for k in range(-n_down, n_up + 1):
            mhz = round(center + k * seg_mhz, 3)
            if mhz < start - 0.01 or mhz > stop + 0.01:
                continue
            segs.append(_seg(mhz))
        if not segs:
            segs = [_seg(center)]
        if LOCK_ON_TARGETS:
            for m in blk["members"]:
                segs.append(_seg(round(m, 3), locked=True))
        per_block.append(segs)

    if not per_block:
        return []

    # Round-robin interleave
    hops = []
    max_len = max(len(s) for s in per_block)
    for i in range(max_len):
        for segs in per_block:
            hops.append(segs[i % len(segs)])
    return hops


def cluster_nearby_matching_mod(items, gap_mhz=None):
    """Cluster items that are nearby AND share the same modulation."""
    if gap_mhz is None:
        gap_mhz = BLOCK_GAP_MHZ
    if not items:
        return []
    sorted_items = sorted(items, key=lambda x: float(x["mhz"]))
    clusters = []
    current = [sorted_items[0]]
    for item in sorted_items[1:]:
        last = current[-1]
        same_mod = (str(item.get("modulation", "unknown")).lower() ==
                    str(last.get("modulation", "unknown")).lower())
        if (float(item["mhz"]) - float(last["mhz"]) <= gap_mhz) and same_mod:
            current.append(item)
        else:
            clusters.append(current)
            current = [item]
    clusters.append(current)
    return clusters


def _wave_for_modulation(modulation):
    """Return the TX waveform that best matches a scanned modulation."""
    mod = (modulation or "unknown").lower().strip()
    mapped = MOD_TO_WAVE.get(mod)
    if mapped is None:
        return TRANSMIT_TYPE
    return mapped


def _inverse_amplitude(avg_level):
    """Convert a 0–1 recorded waveform level into an inverse TX amplitude."""
    try:
        lvl = float(avg_level)
    except (TypeError, ValueError):
        lvl = 0.0
    lvl = max(0.0, min(1.0, lvl))
    if INVERT_LEVEL_MODE == "reciprocal":
        amp = 1.0 / (lvl + 0.15)
        return max(0.1, min(1.0, amp * 0.3))
    return max(0.1, min(1.0, 1.0 - lvl))


def _should_invert(modulation):
    """Return True if this modulation should use the inverted waveform."""
    if not INVERT_MODULATIONS:
        return False
    mod = (modulation or "").lower().strip()
    return mod in {m.lower().strip() for m in INVERT_MODULATIONS}


def _tx_wave_for(modulation):
    """Return the waveform type to use for a given modulation."""
    if _should_invert(modulation) and INVERT_USE_SCANNED_WAVEFORM:
        return _wave_for_modulation(modulation)
    if _should_invert(modulation):
        return INVERT_WAVE_TYPE
    return TRANSMIT_TYPE


def _tx_wave_band(modulation, center_mhz):
    """Legacy-path waveform: inversion only applies inside the auto band."""
    if in_auto_band(center_mhz):
        return _tx_wave_for(modulation)
    return TRANSMIT_TYPE


def build_blocks_from_clusters(clusters, default_bandwidth=None):
    """Turn clusters into transmit blocks."""
    if default_bandwidth is None:
        default_bandwidth = MANUAL_BANDWIDTH_MHZ
    blocks = []
    for cluster in clusters:
        if not cluster:
            continue
        members = [round(float(it["mhz"]), 3) for it in cluster]
        lo = min(members)
        hi = max(members)
        center = (lo + hi) / 2.0
        bw = cluster[0].get("bandwidth")
        if bw is None or bw <= 0:
            width = (hi - lo) + 2 * BLOCK_MARGIN_MHZ
            width = max(BLOCK_MIN_WIDTH_MHZ, min(BLOCK_MAX_WIDTH_MHZ, width))
        else:
            width = max(BLOCK_MIN_WIDTH_MHZ, min(BLOCK_MAX_WIDTH_MHZ, float(bw)))
        half = width / 2.0
        mod = cluster[0].get("modulation", "unknown")
        source = cluster[0].get("source", "")

        levels = [float(it.get("mod_waveform_level") or 0.0) for it in cluster]
        avg_level = sum(levels) / len(levels) if levels else 0.0

        in_band = in_auto_band(center)
        capture_file = None
        inverted = False
        tx_wave = TRANSMIT_TYPE
        tx_amp = TX_AMPLITUDE
        inverse_leveled = False

        # When Option 3 is off, use last captured modulation at inverse amplitude.
        # This applies with any number of HackRFs (including a single device).
        # Manual entries inside the auto/phase band are also inverted as carriers
        # so eligible-range blocks are never left non-inverted.
        if not OPTION3_REALTIME_INVERSE_RX_TX:
            force_invert = (
                _should_invert(mod)
                or (REPLAY_UNKNOWN and str(mod).lower() == "unknown")
                or (in_band and str(mod).lower() in ("manual", "unknown", ""))
            )
            if force_invert:
                inverted = True
                if str(mod).lower() == "manual":
                    tx_wave = TRANSMIT_TYPE          # carrier / configured wave
                else:
                    tx_wave = _tx_wave_for(mod)
                # Prefer a real capture file whenever one exists (auto-band or unknown)
                cap = next((it.get("capture_file") for it in cluster
                            if it.get("capture_file")), None)
                if cap:
                    capture_file = cap
                    if str(mod).lower() == "unknown" or REPLAY_UNKNOWN:
                        tx_wave = "replay"
                tx_amp = _inverse_amplitude(avg_level) if avg_level > 0 else TX_AMPLITUDE
                inverse_leveled = avg_level > 0
            elif in_band:
                tx_wave = _tx_wave_for(mod)
        else:
            # Option 3 active: leave waveform/amp for the real-time path
            if in_band:
                inverted = _should_invert(mod)
                tx_wave = _tx_wave_for(mod)

        member_meta = [{
            "mhz": round(float(it["mhz"]), 3),
            "power_db": it.get("power_db"),
            "floor_db": it.get("noise_floor_db"),
            "modulation": it.get("modulation", mod),
        } for it in cluster]

        blocks.append({
            "center_mhz": round(center, 3),
            "width_mhz": round(width, 3),
            "start_mhz": round(center - half, 3),
            "stop_mhz": round(center + half, 3),
            "members": members,
            "member_meta": member_meta,
            "modulation": mod,
            "source": source,
            "tx_wave": tx_wave,
            "capture_file": capture_file,
            "tx_amplitude": tx_amp,
            "avg_waveform_level": round(avg_level, 4),
            "inverted": inverted,
            "inverse_leveled": inverse_leveled,
            "in_band": in_band,
            "exact": False,
            "assigned_device_serial": None,
        })
    return blocks

# =============================================================================
# END OF CONFIGURATION
# =============================================================================

# Global state
running = True
frequencies = []
last_update_time = 0
hackrf_bin = None
_process_start = time.time()
HACKRF_SERIALS = []
_hackrf_devs = {}
_use_persistent = False
_hackrf_tx_active = {}

# Last successful inverse IQ (complex) keyed by rounded MHz, for Option 3 silent-RX fallback
_last_inverse_iq = {}          # mhz -> np.ndarray (complex64)
_last_inverse_meta = {}        # mhz -> {"wave_type", "amplitude", "capture_file", "modulation", "source"}


def signal_handler(sig, frame):
    """Two Ctrl+C presses within 5 seconds stop the program."""
    global running, _stop_requested, _last_sigint
    now = time.time()
    if _stop_requested or (now - _last_sigint) < 5.0:
        print("\n[+] Stopping continuous generator...")
        _stop_requested = True
        running = False
    else:
        _last_sigint = now
        print("\n[!] Interrupt ignored (press Ctrl+C again within 5s to stop)")


_stop_requested = False
_last_sigint = 0.0

for _sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
    _sig = getattr(os_signal, _sig_name, None)
    if _sig is not None:
        try:
            os_signal.signal(_sig, signal_handler)
        except (ValueError, OSError, RuntimeError):
            pass


def _rotate_file(path, max_bytes, keep):
    """Rotate path if it exceeds max_bytes."""
    try:
        if not path or not Path(path).exists():
            return
        if Path(path).stat().st_size < max_bytes:
            return
        for i in range(keep, 0, -1):
            src = Path(f"{path}.{i}")
            dst = Path(f"{path}.{i+1}")
            if i == keep:
                if src.exists():
                    src.unlink()
            elif src.exists():
                src.rename(dst)
        Path(path).rename(Path(f"{path}.1"))
    except Exception:
        pass


def log(msg, also_print=True):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    if also_print:
        print(line, flush=True)
    if LOG_FILE:
        try:
            _rotate_file(LOG_FILE, LOG_MAX_BYTES, LOG_KEEP)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def find_hackrf():
    """Detect available HackRF devices and their serial numbers.
    Auto-detects all connected HackRFs."""
    global hackrf_bin, HACKRF_SERIALS
    HACKRF_SERIALS = []
    
    # Check explicitly provided path first
    if HACKRF_TRANSFER and Path(HACKRF_TRANSFER).exists():
        hackrf_bin = HACKRF_TRANSFER
    else:
        # Search PATH and common locations
        candidates = [
            "hackrf_transfer",
            "hackrf_transfer.exe",
            "/usr/bin/hackrf_transfer",
            "/usr/local/bin/hackrf_transfer",
            "/opt/homebrew/bin/hackrf_transfer",
            r"C:\HackRF\bin\hackrf_transfer.exe",
            r"C:\Program Files\HackRF\bin\hackrf_transfer.exe",
        ]
        found = False
        for c in candidates:
            try:
                r = subprocess.run([c, "-h"], capture_output=True, timeout=3)
                if r.returncode == 0 or b"Usage" in r.stderr or b"Usage" in r.stdout:
                    hackrf_bin = c
                    found = True
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                continue
        if not found:
            log("hackrf_transfer binary not found in PATH or common locations.")
            return False

    # Use hackrf_info to find all serial numbers
    try:
        result = subprocess.run(["hackrf_info"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            serials = []
            for line in result.stdout.splitlines():
                if "Serial number:" in line:
                    serial = line.split("Serial number:")[1].strip()
                    if serial:
                        serials.append(serial)
            if serials:
                HACKRF_SERIALS = serials
                log(f"Auto-detected {len(HACKRF_SERIALS)} HackRF device(s): {', '.join(HACKRF_SERIALS)}")
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log(f"Error running hackrf_info: {e}")
    
    # Fallback to HACKRF_DEVICES config if set
    if HACKRF_DEVICES:
        HACKRF_SERIALS = list(HACKRF_DEVICES)
        log(f"Using configured HackRF devices: {', '.join(HACKRF_SERIALS)}")
        return True
    
    return False


def init_persistent_hackrf():
    """Try to open HackRF devices and keep them open for faster TX.
    Handles multiple devices if available."""
    global _use_persistent, _hackrf_devs, _hackrf_tx_active
    _hackrf_devs = {}
    _hackrf_tx_active = {}
    _use_persistent = False

    if not HACKRF_SERIALS:
        log("No HackRF devices found. Cannot initialize persistent mode.")
        return False

    try:
        from pyhackrf2 import HackRF
        log("Attempting to initialize persistent mode with pyhackrf2...")
        for i, serial in enumerate(HACKRF_SERIALS):
            dev = None
            last_err = None
            # Try several constructor styles used by different pyhackrf2 versions
            for kwargs in (
                {"serial_number": serial},
                {"serial": serial},
                {"device_index": i},
                {},
            ):
                try:
                    dev = HackRF(**kwargs)
                    break
                except TypeError as e:
                    last_err = e
                    continue
                except Exception as e:
                    last_err = e
                    break
            if dev is None:
                log(f"Failed to open HackRF {serial}: {last_err}")
                continue
            try:
                dev.sample_rate = SAMPLE_RATE
                dev.txvga_gain = TX_GAIN
                dev.amplifier_on = bool(AMP_ENABLE)
                dev.bias_tee_on = bool(ANTENNA_ENABLE)
                if BASEBAND_FILTER_HZ is not None:
                    try:
                        dev.baseband_filter = BASEBAND_FILTER_HZ
                    except Exception as e:
                        log(f"Could not set baseband filter for {serial}: {e}")
                _hackrf_devs[serial] = dev
                _hackrf_tx_active[serial] = False
                log(f"  Opened HackRF {serial} via pyhackrf2")
            except Exception as e:
                log(f"Failed to configure HackRF {serial}: {e}")
        if _hackrf_devs:
            _use_persistent = True
            log(f"Fast mode: {len(_hackrf_devs)} HackRF(s) kept open (pyhackrf2)")
            return True
        log("pyhackrf2 present but no device could be opened — falling back to hackrf_transfer")
    except ImportError:
        log("pyhackrf2 not found. Falling back to hackrf_transfer for persistent mode (slower).")
        log("For much faster sweeps install: pip install pyhackrf2")
    except Exception as e:
        msg = str(e)
        log(f"Error initializing pyhackrf2 devices: {msg}. Falling back to hackrf_transfer.")
        if IS_WINDOWS and ("libhackrf.so" in msg or "hackrf.dll" in msg.lower() or "Could not find module" in msg):
            log("Windows note: pyhackrf2 is looking for a Linux .so. On Windows it needs hackrf.dll "
                "on PATH (from the HackRF Windows package). TX will use hackrf_transfer instead.")

    # Fallback to using hackrf_transfer for each call
    log("Using hackrf_transfer for transmit.")
    _use_persistent = True
    return True


def close_persistent_hackrf():
    """Best-effort close of HackRF devices."""
    global _hackrf_devs, _use_persistent, _hackrf_tx_active
    if not _use_persistent:
        return

    for serial, dev in _hackrf_devs.items():
        try:
            if hasattr(dev, "stop_tx") and _hackrf_tx_active.get(serial, False):
                dev.stop_tx()
            if hasattr(dev, "close"):
                dev.close()
        except Exception as e:
            log(f"Error closing pyhackrf2 device {serial}: {e}")
    _hackrf_devs = {}
    _hackrf_tx_active = {}

    _use_persistent = False
    time.sleep(0.4)
    log("HackRF devices closed.")


def restart_process(reason=""):
    """Re-exec this script from scratch."""
    if _stop_requested:
        return
    log(f"Restarting process: {reason}")
    try:
        close_persistent_hackrf()
    except Exception:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        log(f"execv restart failed ({e}); exiting 42 for the supervisor loop")
        os._exit(42)


def _load_replay_iq(capture_file, num_samples, amp):
    """Load a saved int8 IQ snippet, tile/trim to num_samples, scale to amp."""
    if not capture_file or not HAS_NUMPY:
        return None
    try:
        p = Path(capture_file)
        if not p.exists():
            return None
        raw = np.fromfile(str(p), dtype=np.int8)
        if raw.size < 4:
            return None
        iq = (raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)) / 127.0
        if iq.size == 0:
            return None
        if iq.size < num_samples:
            reps = int(np.ceil(num_samples / iq.size))
            iq = np.tile(iq, reps)
        iq = iq[:num_samples]
        peak = np.max(np.abs(iq)) + 1e-9
        return (iq / peak * amp).astype(np.complex64)
    except Exception:
        return None


def generate_iq_samples(num_samples, tone_offset_hz=0, amplitude=0.5, apply_phase_shift=False, wave_type=None, speed_hz=None, capture_file=None):
    """Generate interleaved int8 IQ samples for the selected waveform type."""
    if wave_type is None:
        wave_type = TX_TYPE
    if speed_hz is None:
        speed_hz = TX_SPEED_HZ
    wave_type = (wave_type or "sine").lower()

    if not HAS_NUMPY:
        raw = bytearray(b"\x00\x40" * (num_samples // 2))[:num_samples * 2]
        if apply_phase_shift:
            raw = bytearray((-b) & 0xFF for b in raw)
        return raw

    t = np.arange(num_samples, dtype=np.float32) / SAMPLE_RATE
    amp = float(amplitude)

    if wave_type in ("carrier", "cw"):
        iq = np.full(num_samples, amp, dtype=np.complex64)
    elif wave_type == "sine":
        freq = tone_offset_hz if tone_offset_hz else speed_hz
        iq = amp * np.exp(2j * np.pi * freq * t).astype(np.complex64)
    elif wave_type == "square":
        freq = tone_offset_hz if tone_offset_hz else speed_hz
        phase = 2 * np.pi * freq * t
        iq = amp * np.sign(np.sin(phase)).astype(np.complex64)
    elif wave_type == "sawtooth":
        freq = tone_offset_hz if tone_offset_hz else speed_hz
        phase = (freq * t) % 1.0
        iq = amp * (2 * phase - 1).astype(np.complex64)
    elif wave_type == "triangle":
        freq = tone_offset_hz if tone_offset_hz else speed_hz
        phase = (freq * t) % 1.0
        tri = 2 * np.abs(2 * phase - 1) - 1
        iq = amp * tri.astype(np.complex64)
    elif wave_type == "chirp":
        f0 = speed_hz * 0.1
        f1 = speed_hz
        k = (f1 - f0) / max(t[-1], 1e-6)
        phase = 2 * np.pi * (f0 * t + 0.5 * k * t * t)
        iq = amp * np.exp(1j * phase).astype(np.complex64)
    elif wave_type == "noise":
        iq = amp * (np.random.randn(num_samples) + 1j * np.random.randn(num_samples)).astype(np.complex64)
        win = max(1, int(SAMPLE_RATE / 1_000_000))
        if win > 1 and num_samples > win * 2:
            kernel = np.ones(win, dtype=np.float32) / win
            iq = (np.convolve(iq.real, kernel, mode="same") +
                  1j * np.convolve(iq.imag, kernel, mode="same")).astype(np.complex64)
        peak = np.max(np.abs(iq)) + 1e-9
        iq = (iq / peak * amp).astype(np.complex64)
    elif wave_type == "gauss":
        noise = np.random.randn(num_samples) + 1j * np.random.randn(num_samples)
        env = np.exp(-0.5 * ((t - t.mean()) / (t.std() + 1e-9))**2)
        iq = amp * noise * env
        iq = iq.astype(np.complex64)
    elif wave_type == "fm_tone":
        mod = np.sin(2 * np.pi * speed_hz * t)
        deviation = speed_hz * 2
        phase = 2 * np.pi * (tone_offset_hz * t + deviation * np.cumsum(mod) / SAMPLE_RATE)
        iq = amp * np.exp(1j * phase).astype(np.complex64)
    elif wave_type == "rand_fsk":
        symbols = np.random.randint(0, 2, num_samples)
        hold = max(1, int(SAMPLE_RATE / max(speed_hz, 1)))
        for i in range(0, num_samples, hold):
            symbols[i:i+hold] = symbols[i]
        f1 = speed_hz
        f2 = speed_hz * 1.5
        freqs = np.where(symbols, f2, f1)
        phase = 2 * np.pi * np.cumsum(freqs) / SAMPLE_RATE
        iq = amp * np.exp(1j * phase).astype(np.complex64)
    elif wave_type == "cw_sweep":
        f0 = -speed_hz
        f1 = speed_hz
        k = (f1 - f0) / max(t[-1], 1e-6)
        phase = 2 * np.pi * (f0 * t + 0.5 * k * t * t)
        iq = amp * np.exp(1j * phase).astype(np.complex64)
    elif wave_type in ("rand_cw", "random_cw"):
        offset = np.random.uniform(-2000, 2000)
        rand_amp = amp * np.random.uniform(0.7, 1.0)
        iq = rand_amp * np.exp(2j * np.pi * offset * t).astype(np.complex64)
    elif wave_type == "brute":
        hop_len = max(1, int(SAMPLE_RATE / max(speed_hz, 10)))
        iq = np.zeros(num_samples, dtype=np.complex64)
        pos = 0
        while pos < num_samples:
            end = min(pos + hop_len, num_samples)
            f = np.random.uniform(-speed_hz, speed_hz)
            tt = np.arange(end - pos) / SAMPLE_RATE
            iq[pos:end] = amp * np.exp(2j * np.pi * f * tt)
            pos = end
    elif wave_type == "replay":
        iq = _load_replay_iq(capture_file, num_samples, amp)
        if iq is None:
            iq = amp * (np.random.randn(num_samples) + 1j * np.random.randn(num_samples)).astype(np.complex64)
        elif REPLAY_INVERT:
            iq = (-iq[::-1]).astype(np.complex64)
            apply_phase_shift = False
    else:
        freq = tone_offset_hz if tone_offset_hz else speed_hz
        iq = amp * np.exp(2j * np.pi * freq * t).astype(np.complex64)

    if apply_phase_shift:
        iq = -iq
    if FILL_NOISE and FILL_NOISE_LEVEL > 0 and HAS_NUMPY:
        noise = (np.random.randn(num_samples) + 1j * np.random.randn(num_samples)).astype(np.complex64)
        noise *= FILL_NOISE_LEVEL * amp / (np.max(np.abs(noise)) + 1e-9)
        iq = (iq + noise).astype(np.complex64)
        peak = np.max(np.abs(iq)) + 1e-9
        if peak > amp:
            iq = (iq / peak * amp).astype(np.complex64)
    i = np.clip(iq.real * 127, -127, 127).astype(np.int8)
    q = np.clip(iq.imag * 127, -127, 127).astype(np.int8)
    return bytearray(np.column_stack((i, q)).ravel().tobytes())


def transmit_on_frequency(freq_hz, tone_hz=None, dwell_sec=None, wave_type=None, amplitude=None, capture_file=None, device_serial=None):
    """Transmit a short burst on the given frequency using the specified device."""
    global _hackrf_tx_active

    mhz = freq_hz / 1e6
    if tone_hz is None:
        tone_hz = TX_TONE_OFFSET_HZ
    else:
        try:
            tone_hz = float(tone_hz)
        except (TypeError, ValueError):
            tone_hz = TX_TONE_OFFSET_HZ

    actual_dwell = DWELL_SEC if dwell_sec is None else float(dwell_sec)
    if actual_dwell <= 0:
        actual_dwell = 0.01

    use_wave = wave_type if wave_type else TX_TYPE
    use_amp = TX_AMPLITUDE if amplitude is None else float(amplitude)
    use_amp = max(0.05, min(1.0, use_amp))

    # Determine phase shift.
    # With 2+ devices and Option 2: device 1 is phase-shifted, device 0 is normal.
    # With 1 device (or Option 2 off): still apply 180° phase shift on the eligible band
    # so a single HackRF inverts the carrier in that range.
    apply_shift = False
    in_phase_band = APPLY_180_PHASE_SHIFT and (PHASE_SHIFT_MIN_MHZ <= mhz <= PHASE_SHIFT_MAX_MHZ)
    if (OPTION2_PHASE_SHIFT_ASSIGNMENT and ENABLE_MULTI_HACKRF_TX
            and len(HACKRF_SERIALS) >= 2):
        if device_serial == HACKRF_SERIALS[1] and in_phase_band:
            apply_shift = True
    else:
        apply_shift = in_phase_band

    if apply_shift:
        # Log once per whole-MHz so eligible-range carrier inversion is visible
        # without flooding the log on every 1 MHz hop segment.
        mhz_bucket = int(mhz)
        if mhz_bucket not in getattr(transmit_on_frequency, "_logged_phase", set()):
            if not hasattr(transmit_on_frequency, "_logged_phase"):
                transmit_on_frequency._logged_phase = set()
            transmit_on_frequency._logged_phase.add(mhz_bucket)
            log(f"  [PhaseShift] 180° carrier inversion at {mhz:.3f} MHz "
                f"(band {PHASE_SHIFT_MIN_MHZ:.0f}-{PHASE_SHIFT_MAX_MHZ:.0f})")

    # Persistent Mode (pyhackrf2)
    if _use_persistent and device_serial in _hackrf_devs:
        dev = _hackrf_devs[device_serial]
        try:
            dev.center_freq = freq_hz
            dev.txvga_gain = TX_GAIN
            dev.amplifier_on = bool(AMP_ENABLE)
            dev.bias_tee_on = bool(ANTENNA_ENABLE)

            n_samp = max(4096, int(SAMPLE_RATE * max(actual_dwell, 0.02)))
            iq_data = generate_iq_samples(
                n_samp, 0, use_amp,
                apply_phase_shift=apply_shift,
                wave_type=use_wave, speed_hz=TX_SPEED_HZ,
                capture_file=capture_file
            )
            dev.buffer = iq_data

            if not _hackrf_tx_active.get(device_serial, False):
                dev.start_tx()
                _hackrf_tx_active[device_serial] = True
            time.sleep(actual_dwell)
            return True
        except Exception as e:
            log(f"Persistent TX error on {device_serial} ({mhz:.3f} MHz): {e} — falling back to hackrf_transfer", also_print=True)
            _hackrf_tx_active[device_serial] = False

    # Fallback Mode (hackrf_transfer subprocess)
    if not hackrf_bin:
        log(f"No hackrf_transfer binary available for {mhz:.3f} MHz", also_print=True)
        return False

    n_samp = max(8192, int(SAMPLE_RATE * actual_dwell))
    iq_data = generate_iq_samples(
        n_samp, 0, use_amp,
        apply_phase_shift=apply_shift,
        wave_type=use_wave, speed_hz=TX_SPEED_HZ,
        capture_file=capture_file
    )

    cmd = [
        hackrf_bin,
        "-t", "-",
        "-f", str(freq_hz),
        "-s", str(SAMPLE_RATE),
        "-n", str(n_samp),
        "-x", str(TX_GAIN),
        "-a", str(AMP_ENABLE),
        "-p", str(ANTENNA_ENABLE),
    ]
    if device_serial:
        cmd.extend(["-d", device_serial])

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0
        )
        stdout, stderr = proc.communicate(input=iq_data, timeout=10)
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")[:300] if stderr else "(no stderr)"
            log(f"TX fail on {device_serial} ({mhz:.3f} MHz, code {proc.returncode}): {err}", also_print=True)
            return False
        return True
    except subprocess.TimeoutExpired:
        log(f"TX timeout on {device_serial} at {mhz:.3f} MHz", also_print=True)
        try:
            proc.kill()
        except Exception:
            pass
        return False
    except Exception as e:
        log(f"Transmit error on {device_serial} at {mhz:.3f} MHz: {e}", also_print=True)
        return False


def _device_for_mhz(mhz, num_hackrfs):
    """Return serial for the DEVICE_BAND_SPLITS entry covering mhz, or None."""
    for split in DEVICE_BAND_SPLITS:
        idx = int(split.get("index", 0))
        if idx >= num_hackrfs:
            continue
        if float(split["start_mhz"]) <= mhz <= float(split["stop_mhz"]):
            return HACKRF_SERIALS[idx]
    return None


def option3_real_time_inverse_rx_tx(all_hops, num_hackrfs):
    """Option 3: inverse modulation.

    OPTION3_OVERLAP_RX_TX False:
        Sequential. RX then TX on the same hop, restricted to AUTO_BAND
        (default 400-800). Loops through every eligible frequency.

    OPTION3_OVERLAP_RX_TX True:
        Daisy-chain across DEVICE_BAND_SPLITS. While one radio transmits the
        inverse, the other captures the next hop (preferring the opposite band).
        Restarts the sweep on the next main-loop cycle.
    """
    if num_hackrfs < 2 or not ENABLE_MULTI_HACKRF_TX:
        log("Option 3 skipped: need >=2 HackRFs and ENABLE_MULTI_HACKRF_TX")
        return

    if not OPTION3_OVERLAP_RX_TX:
        # ---- Sequential path: listen, then TX inverse, AUTO_BAND only ----
        rx_serial = HACKRF_SERIALS[OPTION3_INVERSE_RX_DEVICE_INDEX] if OPTION3_INVERSE_RX_DEVICE_INDEX < num_hackrfs else HACKRF_SERIALS[1]
        tx_serial = HACKRF_SERIALS[OPTION3_INVERSE_TX_DEVICE_INDEX] if OPTION3_INVERSE_TX_DEVICE_INDEX < num_hackrfs else HACKRF_SERIALS[0]
        log("Option 3 sequential: listen then TX inverse")
        log(f"  RX {rx_serial}  TX {tx_serial}  band {AUTO_BAND_MIN_MHZ:.0f}-{AUTO_BAND_MAX_MHZ:.0f} MHz")
        live_ok = fallback_ok = 0
        for hop in all_hops:
            if not running:
                break
            mhz = hop["hz"] / 1e6
            if not (AUTO_BAND_MIN_MHZ <= mhz <= AUTO_BAND_MAX_MHZ):
                continue
            if not _should_invert(hop.get("modulation")):
                continue
            mhz_key = round(mhz, 3)
            log(f"  [Option3] RX {mhz:.3f} MHz on {rx_serial}")
            iq = capture_iq(hop["hz"], OPTION3_RX_DURATION_SEC, device_serial=rx_serial)
            silent = True
            pwr = 0.0
            if iq is not None and len(iq) > 100 and HAS_NUMPY:
                pwr = float(np.mean(np.abs(iq) ** 2))
                silent = pwr < 1e-4
            if not silent:
                inverse_iq = create_inverse_waveform(iq, hop)
                if inverse_iq is not None:
                    _last_inverse_iq[mhz_key] = inverse_iq
                    time.sleep(OPTION3_INVERSE_TX_DELAY_SEC)
                    log(f"  [Option3] TX inverse {mhz:.3f} on {tx_serial} (pwr={pwr:.2e})")
                    transmit_inverse_iq(hop["hz"], inverse_iq, device_serial=tx_serial)
                    live_ok += 1
                    time.sleep(OPTION3_INVERSE_TX_WAIT_SEC)
                    continue
            log(f"  [Option3] silent at {mhz:.3f} — last captured inverse")
            if mhz_key in _last_inverse_iq:
                transmit_inverse_iq(hop["hz"], _last_inverse_iq[mhz_key], device_serial=tx_serial)
            else:
                wave = hop.get("wave_type") or _tx_wave_for(hop.get("modulation"))
                amp = _inverse_amplitude(hop.get("mod_waveform_level") or 0.0)
                cap = hop.get("capture_file")
                if cap and Path(cap).exists():
                    wave = "replay"
                transmit_on_frequency(hop["hz"], wave_type=wave, amplitude=amp,
                                      capture_file=cap, device_serial=tx_serial,
                                      dwell_sec=max(DWELL_SEC, OPTION3_RX_DURATION_SEC))
            fallback_ok += 1
            time.sleep(OPTION3_INVERSE_TX_WAIT_SEC)
        log(f"Option 3 sequential complete: live={live_ok} fallback={fallback_ok}")
        return

    # ---- Overlapped daisy-chain across configured band splits ----
    log("Option 3 overlap: daisy-chain RX/TX across band splits")
    for split in DEVICE_BAND_SPLITS:
        idx = int(split.get("index", 0))
        if idx < num_hackrfs:
            log(f"  Device {idx} ({HACKRF_SERIALS[idx]}): "
                f"{split['start_mhz']:.0f}-{split['stop_mhz']:.0f} MHz")
    log(f"  Min TX/RX separation: {OPTION3_MIN_TX_RX_SEPARATION_MHZ:.0f} MHz")

    work = []
    for hop in all_hops:
        mhz = hop["hz"] / 1e6
        if not _should_invert(hop.get("modulation")):
            continue
        owner = _device_for_mhz(mhz, num_hackrfs)
        if owner is None:
            continue
        work.append((hop, owner))
    if not work:
        log("Option 3 overlap: no invertible hops in configured splits")
        return

    live_ok = fallback_ok = 0
    # Prefetch first
    first_hop, first_owner = work[0]
    log(f"  [Option3] prefetch RX {first_hop['hz']/1e6:.3f} on {first_owner}")
    pending_iq = capture_iq(first_hop["hz"], OPTION3_RX_DURATION_SEC, device_serial=first_owner)

    for idx, (hop, owner) in enumerate(work):
        if not running:
            break
        mhz = hop["hz"] / 1e6
        mhz_key = round(mhz, 3)
        next_iq_holder = [None]
        rx_thread = None
        if idx + 1 < len(work):
            next_hop, next_owner = work[idx + 1]
            next_mhz = next_hop["hz"] / 1e6
            sep = abs(next_mhz - mhz)
            # Use the other radio when possible; require min separation
            other = next((HACKRF_SERIALS[i] for i in range(num_hackrfs) if HACKRF_SERIALS[i] != owner), owner)
            rx_dev = next_owner if next_owner != owner else other
            if sep >= OPTION3_MIN_TX_RX_SEPARATION_MHZ and rx_dev != owner:
                def _do_rx(freq, dev, holder):
                    holder[0] = capture_iq(freq, OPTION3_RX_DURATION_SEC, device_serial=dev)
                rx_thread = threading.Thread(target=_do_rx, args=(next_hop["hz"], rx_dev, next_iq_holder))
                log(f"  [Option3] overlap TX {mhz:.3f} on {owner}  ||  RX {next_mhz:.3f} on {rx_dev}  (sep={sep:.0f} MHz)")
                rx_thread.start()
            else:
                log(f"  [Option3] sequential fallback (sep={sep:.0f} MHz < {OPTION3_MIN_TX_RX_SEPARATION_MHZ:.0f} or same device)")

        iq = pending_iq
        silent = True
        pwr = 0.0
        if iq is not None and len(iq) > 100 and HAS_NUMPY:
            pwr = float(np.mean(np.abs(iq) ** 2))
            silent = pwr < 1e-4
        if not silent:
            inverse_iq = create_inverse_waveform(iq, hop)
            if inverse_iq is not None:
                _last_inverse_iq[mhz_key] = inverse_iq
                log(f"  [Option3] LIVE inverse TX {mhz:.3f} on {owner} (pwr={pwr:.2e})")
                transmit_inverse_iq(hop["hz"], inverse_iq, device_serial=owner)
                live_ok += 1
            else:
                silent = True
        if silent:
            log(f"  [Option3] silent at {mhz:.3f} — last captured inverse")
            if mhz_key in _last_inverse_iq:
                transmit_inverse_iq(hop["hz"], _last_inverse_iq[mhz_key], device_serial=owner)
            else:
                wave = hop.get("wave_type") or _tx_wave_for(hop.get("modulation"))
                amp = _inverse_amplitude(hop.get("mod_waveform_level") or 0.0)
                cap = hop.get("capture_file")
                if cap and Path(cap).exists():
                    wave = "replay"
                transmit_on_frequency(hop["hz"], wave_type=wave, amplitude=amp,
                                      capture_file=cap, device_serial=owner,
                                      dwell_sec=max(DWELL_SEC, OPTION3_RX_DURATION_SEC))
            fallback_ok += 1

        if rx_thread is not None:
            rx_thread.join()
            pending_iq = next_iq_holder[0]
        else:
            # Sequential capture of next if we could not overlap
            if idx + 1 < len(work):
                nh, no = work[idx + 1]
                pending_iq = capture_iq(nh["hz"], OPTION3_RX_DURATION_SEC, device_serial=no)
            else:
                pending_iq = None

    log(f"Option 3 overlap cycle complete: live={live_ok} fallback={fallback_ok}")


def create_inverse_waveform(iq, hop):
    """Create an inverse waveform from captured IQ in real-time.
    
    The inverse is created by:
    1. Time-reversing the signal (reverses frequency sweep direction)
    2. Negating the phase (180 degree phase shift)
    3. Scaling amplitude based on the inverse level mode
    """
    if not HAS_NUMPY or iq is None:
        return None
    
    # Get the modulation level for amplitude scaling
    mod_level = hop.get("amplitude", TX_AMPLITUDE)
    
    # Create inverse waveform
    inverse = iq[::-1].copy()
    inverse = -inverse
    
    # Normalize and scale
    peak = np.max(np.abs(inverse)) + 1e-9
    if peak > 0:
        inverse = (inverse / peak * mod_level).astype(np.complex64)
    
    return inverse


def transmit_inverse_iq(freq_hz, iq_data, device_serial=None):
    """Transmit inverse IQ data immediately on the specified device."""
    if iq_data is None or len(iq_data) == 0:
        return False
    
    mhz = freq_hz / 1e6
    
    # Convert complex IQ to int8 interleaved format
    i = np.clip(np.real(iq_data) * 127, -127, 127).astype(np.int8)
    q = np.clip(np.imag(iq_data) * 127, -127, 127).astype(np.int8)
    iq_bytes = bytearray(np.column_stack((i, q)).ravel().tobytes())
    
    # Persistent mode
    if _use_persistent and device_serial in _hackrf_devs:
        dev = _hackrf_devs[device_serial]
        try:
            dev.center_freq = freq_hz
            dev.txvga_gain = TX_GAIN
            dev.amplifier_on = bool(AMP_ENABLE)
            dev.bias_tee_on = bool(ANTENNA_ENABLE)
            
            dev.buffer = iq_bytes
            
            if not _hackrf_tx_active.get(device_serial, False):
                dev.start_tx()
                _hackrf_tx_active[device_serial] = True
            
            duration = len(iq_data) / SAMPLE_RATE
            time.sleep(duration)
            return True
        except Exception as e:
            log(f"Persistent TX error on {device_serial} ({mhz:.3f} MHz): {e}")
            _hackrf_tx_active[device_serial] = False
    
    # Fallback to hackrf_transfer
    if not hackrf_bin:
        return False
    
    cmd = [
        hackrf_bin,
        "-t", "-",
        "-f", str(freq_hz),
        "-s", str(SAMPLE_RATE),
        "-x", str(TX_GAIN),
        "-a", str(AMP_ENABLE),
        "-p", str(ANTENNA_ENABLE),
    ]
    if device_serial:
        cmd.extend(["-d", device_serial])
    
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0
        )
        proc.communicate(input=iq_bytes, timeout=10)
        return proc.returncode == 0
    except Exception as e:
        log(f"TX error on {device_serial} ({mhz:.3f} MHz): {e}")
        return False


def assign_devices_to_hops(tx_list, num_devices, assigned_serials):
    """Assigns device serials to hops based on configuration.
    Uses even/odd indexing for multi-HackRF setups."""
    if not tx_list:
        return []

    final_tx_list = []
    num_hackrfs = len(assigned_serials)
    device_idx_counter = 0

    # --- Option 3: Real-time RX/TX Mode ---
    if (OPTION3_REALTIME_INVERSE_RX_TX and num_hackrfs >= 2 and
            ENABLE_MULTI_HACKRF_TX):
        rx_serial = assigned_serials[OPTION3_INVERSE_RX_DEVICE_INDEX] if OPTION3_INVERSE_RX_DEVICE_INDEX < num_hackrfs else assigned_serials[1]
        tx_serial = assigned_serials[OPTION3_INVERSE_TX_DEVICE_INDEX] if OPTION3_INVERSE_TX_DEVICE_INDEX < num_hackrfs else assigned_serials[0]

        if rx_serial == tx_serial:
            log("Option 3 Error: RX and TX devices are the same. Disabling Option 3.")
            return assign_devices_to_hops(tx_list, num_devices, assigned_serials)

        log(f"Option 3 active: RX on {rx_serial}, TX on {tx_serial} (400-800MHz)")
        for hop in tx_list:
            mhz = hop["hz"] / 1e6
            if (AUTO_BAND_MIN_MHZ <= mhz <= AUTO_BAND_MAX_MHZ and
                    _should_invert(hop.get("modulation"))):
                modified_hop = dict(hop)
                modified_hop["device_serial"] = tx_serial
                modified_hop["wave_type"] = INVERT_WAVE_TYPE
                final_tx_list.append(modified_hop)
            else:
                pass
        return final_tx_list

    # --- Option 2: Phase Shift Assignment ---
    if (OPTION2_PHASE_SHIFT_ASSIGNMENT and num_hackrfs >= 2 and
            ENABLE_MULTI_HACKRF_TX and APPLY_180_PHASE_SHIFT):
        log("Option 2 active: Phase Shift Assignment (both devices transmit simultaneously)")
        log(f"  Phase inversion band: {PHASE_SHIFT_MIN_MHZ:.0f}-{PHASE_SHIFT_MAX_MHZ:.0f} MHz")
        log(f"  Device 0 (normal): {assigned_serials[0]}")
        log(f"  Device 1 (180° phase): {assigned_serials[1] if len(assigned_serials) > 1 else assigned_serials[0]}")
        normal_serial = assigned_serials[0]
        shifted_serial = assigned_serials[1] if num_hackrfs > 1 else normal_serial

        for hop in tx_list:
            mhz = hop["hz"] / 1e6
            freq_in_shift_band = (PHASE_SHIFT_MIN_MHZ <= mhz <= PHASE_SHIFT_MAX_MHZ)

            if freq_in_shift_band:
                # Create two hops: one normal, one phase-shifted
                normal_hop = dict(hop)
                normal_hop["device_serial"] = normal_serial
                normal_hop["apply_phase_shift"] = False
                final_tx_list.append(normal_hop)

                shifted_hop = dict(hop)
                shifted_hop["device_serial"] = shifted_serial
                shifted_hop["apply_phase_shift"] = True
                final_tx_list.append(shifted_hop)
            else:
                # Outside phase shift band, use even/odd distribution
                hop["device_serial"] = assigned_serials[device_idx_counter % num_hackrfs]
                device_idx_counter += 1
                final_tx_list.append(hop)
        return final_tx_list

    # --- Option 1: Multi-HackRF TX Distribution ---
    if num_hackrfs >= 2 and ENABLE_MULTI_HACKRF_TX:
        if RANGE_DEVICE_MAP:
            log("Assigning devices based on RANGE_DEVICE_MAP...")
            for hop in tx_list:
                mhz = hop["hz"] / 1e6
                assigned_serial = None
                for r_name, r_dev_idx in RANGE_DEVICE_MAP.items():
                    try:
                        r_start, r_stop = map(float, r_name.split('-'))
                        if r_start <= mhz <= r_stop:
                            if 0 <= r_dev_idx < num_hackrfs:
                                assigned_serial = HACKRF_SERIALS[r_dev_idx]
                                break
                    except (ValueError, AttributeError):
                        continue
                if not assigned_serial:
                    assigned_serial = HACKRF_SERIALS[device_idx_counter % num_hackrfs]
                    device_idx_counter += 1
                hop["device_serial"] = assigned_serial
                final_tx_list.append(hop)
        elif OPTION1_FIXED_RANGE_SPLIT and num_hackrfs >= 2:
            log("Assigning devices using OPTION1_FIXED_RANGE_SPLIT (simultaneous)...")
            log("  Device index 0 (first detected): 400-2000 MHz")
            log("  Device index 1 (second detected): 2000-6000 MHz")
            log("  Use --devices to see index → serial mapping. No serial needed in config.")
            hf1_serial = assigned_serials[0]
            hf2_serial = assigned_serials[1] if num_hackrfs > 1 else hf1_serial
            for hop in tx_list:
                mhz = hop["hz"] / 1e6
                if 400 <= mhz < 2000:
                    hop["device_serial"] = hf1_serial
                elif 2000 <= mhz <= 6000:
                    hop["device_serial"] = hf2_serial
                else:
                    hop["device_serial"] = hf1_serial
                final_tx_list.append(hop)
        else:
            log(f"Assigning devices using even/odd distribution across {num_hackrfs} HackRF(s)...")
            for hop in tx_list:
                # Even/odd distribution based on hop index
                hop["device_serial"] = assigned_serials[device_idx_counter % num_hackrfs]
                device_idx_counter += 1
                final_tx_list.append(hop)
    else:
        log("Using single HackRF for transmission.")
        for hop in tx_list:
            hop["device_serial"] = assigned_serials[0] if assigned_serials else None
            final_tx_list.append(hop)

    return final_tx_list


def load_frequencies():
    """Load frequencies according to the USE_MANUAL / USE_SCANNED / USE_SMART switches."""
    global frequencies

    frequencies = []
    if USE_MANUAL:
        _add_manual_frequencies()
        frequencies = [f for f in frequencies if is_tx_enabled(f.get("range"))]
        log(f"Manual list: {len(frequencies)} frequency(ies)")
    else:
        log("USE_MANUAL is False — no manual frequencies")
    return


def _parse_manual_entries():
    """Normalize MANUAL_FREQUENCIES into a list of dicts."""
    entries = []
    for item in MANUAL_FREQUENCIES:
        if isinstance(item, dict):
            try:
                mhz = float(item.get("mhz") or item.get("freq") or item.get("frequency"))
            except (TypeError, ValueError):
                continue
            bw = item.get("bandwidth", item.get("bw", MANUAL_BANDWIDTH_MHZ))
            step = item.get("step", MANUAL_STEP_MHZ)
            try:
                bw = float(bw)
            except (TypeError, ValueError):
                bw = MANUAL_BANDWIDTH_MHZ
            try:
                step = float(step)
            except (TypeError, ValueError):
                step = MANUAL_STEP_MHZ
            entries.append({"mhz": mhz, "bandwidth": bw, "step": step})
        else:
            try:
                mhz = float(item)
            except (TypeError, ValueError):
                continue
            entries.append({
                "mhz": mhz,
                "bandwidth": MANUAL_BANDWIDTH_MHZ,
                "step": MANUAL_STEP_MHZ,
            })
    return entries


def _add_manual_frequencies():
    """Append manually configured frequencies."""
    global frequencies
    entries = _parse_manual_entries()
    if not entries:
        return
    existing = {f["hz"] for f in frequencies}
    added = 0

    def _append(mhz):
        nonlocal added
        hz = int(round(mhz * 1_000_000))
        if hz not in existing and hz > 0:
            frequencies.append({
                "mhz": mhz,
                "hz": hz,
                "power_db": None,
                "range": "manual",
                "type": "manual",
                "tone_hz": None,
            })
            existing.add(hz)
            added += 1

    for ent in entries:
        center = ent["mhz"]
        bw = ent["bandwidth"]
        step = ent["step"]
        _append(center)
        if bw > 0 and step > 0:
            half = bw / 2.0
            n_steps = int(round(bw / step)) + 1
            for i in range(n_steps):
                mhz = round(center - half + i * step, 6)
                if abs(mhz - center) <= half + 1e-6:
                    _append(mhz)

    if added:
        frequencies.sort(key=lambda x: x["hz"])
        log(f"Added {added} manual frequency(ies)")


def is_whole_mhz(mhz):
    """True if mhz is a whole number within WHOLE_MHZ_TOLERANCE."""
    return abs(mhz - round(mhz)) <= WHOLE_MHZ_TOLERANCE


def load_scanned_frequencies():
    """Load frequencies from the latest scan JSON."""
    path = JSON_PATH if JSON_PATH.exists() else None
    if path is None:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    out = []
    seen = set()
    ranges = data.get("ranges", []) if isinstance(data, dict) else []
    for rng in ranges:
        rname = rng.get("name", "")
        for sig in rng.get("signals", []):
            mhz = sig.get("frequency_mhz") or sig.get("mhz")
            if mhz is None:
                continue
            try:
                mhz = float(mhz)
            except (TypeError, ValueError):
                continue
            key = round(mhz, 3)
            if key in seen or mhz <= 0:
                continue
            seen.add(key)
            out.append({
                "mhz": mhz,
                "whole": is_whole_mhz(mhz),
                "range": rname,
                "priority": sig.get("priority", 0),
                "hopping": sig.get("hopping", False),
                "modulation": (sig.get("modulation") or sig.get("signal_type") or "unknown").lower(),
                "mod_waveform_level": float(sig.get("mod_waveform_level") or 0.0),
                "power_db": sig.get("power_db"),
                "noise_floor_db": sig.get("noise_floor_db"),
                "snr_db": sig.get("snr_db"),
                "capture_file": sig.get("capture_file"),
            })
    return out


FM_CAPTURE_MODS = {"fm", "nbfm", "fm_capture"}


def _amp_from_target_db(target_db):
    """Map an absolute dB target onto a 0-1 amplitude via the reference window."""
    try:
        t = float(target_db)
    except (TypeError, ValueError):
        return TX_AMPLITUDE
    lo = NOISE_FLOOR_REF_QUIET_DB
    hi = NOISE_FLOOR_REF_LOUD_DB
    if hi <= lo:
        return TX_AMPLITUDE
    frac = (t - lo) / (hi - lo)
    frac = max(0.0, min(1.0, frac))
    amp = NOISE_FLOOR_MIN_AMP + frac * (1.0 - NOISE_FLOOR_MIN_AMP)
    return round(max(0.05, min(1.0, amp)), 3)


def _amp_from_noise_floor(noise_floor_db):
    """Noise floor (dB) -> amplitude."""
    try:
        nf = float(noise_floor_db)
    except (TypeError, ValueError):
        return TX_AMPLITUDE
    return _amp_from_target_db(nf + NOISE_FLOOR_MARGIN_DB)


def _member_target_db(power_db, floor_db, modulation):
    """Highest dB target for one detected frequency."""
    targets = []
    if floor_db is not None:
        try:
            targets.append(float(floor_db) + NOISE_FLOOR_MARGIN_DB)
        except (TypeError, ValueError):
            pass
    if power_db is not None and DESENSE_POWER_DB is not None:
        try:
            targets.append(float(power_db) + float(DESENSE_POWER_DB))
        except (TypeError, ValueError):
            pass
    if (FM_CAPTURE_CHECK and power_db is not None
            and str(modulation or "").lower() in FM_CAPTURE_MODS):
        try:
            targets.append(float(power_db) + float(FM_CAPTURE_RATIO_DB))
        except (TypeError, ValueError):
            pass
    return max(targets) if targets else None


def load_noise_floors():
    """Return [{start_mhz, stop_mhz, noise_floor_db}] from the latest scan JSON."""
    out = []
    if not JSON_PATH.exists():
        return out
    try:
        data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return out
    for rng in data.get("ranges", []) if isinstance(data, dict) else []:
        nf = rng.get("noise_floor_db")
        if nf is None:
            continue
        try:
            out.append({
                "start_mhz": float(rng.get("start_mhz")),
                "stop_mhz": float(rng.get("stop_mhz")),
                "noise_floor_db": float(nf),
            })
        except (TypeError, ValueError):
            continue
    return out


def noise_floor_for_mhz(mhz, floors):
    """Noise floor (dB) for the narrowest scanned range covering mhz."""
    best = None
    for fl in floors:
        if fl["start_mhz"] <= mhz <= fl["stop_mhz"]:
            if best is None or (fl["stop_mhz"] - fl["start_mhz"]) < (best["stop_mhz"] - best["start_mhz"]):
                best = fl
    return best["noise_floor_db"] if best else None


def apply_auto_levels(blocks, floors):
    """Per-frequency auto level for every in-band block.

    Noise floor is taken per member frequency (local floor from the scan).
    Amplitude for the hop steps *between* members is linearly interpolated
    by _amp_at() when the hop list is built — not a single average for the block.
    Inverse-leveled blocks are included: the noise-floor/desense/FM-capture
    target is combined with the inverse amplitude (higher of the two wins)
    so both mechanisms stay active.
    """
    if not NOISE_FLOOR_COMPENSATE or not blocks:
        return 0
    adjusted = 0
    for blk in blocks:
        center = blk.get("center_mhz")
        if center is None or not in_auto_band(center):
            continue
        range_floor = noise_floor_for_mhz(center, floors)
        meta = blk.get("member_meta") or []
        amps = []
        floors_used = []
        for m in meta:
            floor_db = m.get("floor_db")
            if floor_db is None:
                floor_db = range_floor
            if floor_db is not None:
                floors_used.append(float(floor_db))
            tdb = _member_target_db(m.get("power_db"), floor_db, m.get("modulation"))
            if tdb is None:
                continue
            amp = _amp_from_target_db(tdb)
            # Combine with inverse amplitude if this block was inverse-leveled
            if blk.get("inverse_leveled"):
                inv = blk.get("tx_amplitude", TX_AMPLITUDE)
                amp = max(amp, float(inv))
            amps.append([round(float(m["mhz"]), 3), round(amp, 3)])
        if not amps and range_floor is not None:
            for mz in blk.get("members", []):
                amps.append([round(float(mz), 3), _amp_from_noise_floor(range_floor)])
            floors_used.append(float(range_floor))
        if not amps:
            continue
        amps.sort(key=lambda x: x[0])
        blk["member_amps"] = amps
        # Block-level amplitude is the mean of the per-frequency targets;
        # actual hops use interpolated member_amps via _amp_at().
        blk["tx_amplitude"] = round(sum(a for _, a in amps) / len(amps), 3)
        if floors_used:
            blk["noise_floor_db"] = round(sum(floors_used) / len(floors_used), 1)
        elif range_floor is not None:
            blk["noise_floor_db"] = round(range_floor, 1)
        blk["auto_leveled"] = True
        adjusted += 1
    if adjusted:
        extra = ""
        if DESENSE_POWER_DB is not None:
            extra += f", desense+{DESENSE_POWER_DB:.0f}dB"
        if FM_CAPTURE_CHECK:
            extra += f", FMcap+{FM_CAPTURE_RATIO_DB:.0f}dB"
        log(f"Auto-level: {adjusted} in-band block(s) "
            f"[{AUTO_BAND_MIN_MHZ:.0f}-{AUTO_BAND_MAX_MHZ:.0f} MHz]")
        log(f"  Offsets (applied per frequency, not averaged): "
            f"floor+{NOISE_FLOOR_MARGIN_DB:.0f}dB{extra}")
        log(f"  Each member frequency uses its own noise floor; hops between members are interpolated")
    return adjusted


def build_square_block(center, bandwidth, modulation="unknown", source=""):
    """Grid block centered on the frequency."""
    if FORCE_WHOLE_MHZ:
        center = round(center)
    bw = bandwidth if bandwidth and bandwidth > 0 else BLOCK_MIN_WIDTH_MHZ
    bw = max(BLOCK_MIN_WIDTH_MHZ, min(BLOCK_MAX_WIDTH_MHZ, bw))
    half = bw / 2.0
    return {
        "center_mhz": float(center),
        "width_mhz": round(bw, 3),
        "start_mhz": round(center - half, 3),
        "stop_mhz": round(center + half, 3),
        "members": [float(center)],
        "exact": False,
        "modulation": modulation,
        "source": source,
        "assigned_device_serial": None,
    }


def build_exact_hop(mhz, tag="exact", device_serial=None):
    """Single hop on the exact frequency."""
    mhz = round(mhz, 3)
    return {
        "mhz": mhz,
        "hz": int(round(mhz * 1_000_000)),
        "block_center": mhz,
        "range": f"{tag}@{mhz:.3f}",
        "exact": True,
        "device_serial": device_serial,
    }


def load_smart_frequencies():
    """Load top-ranked frequencies from ranked_signals.json."""
    candidates = []
    if not SMART_LIST_PATH.exists():
        return candidates
    try:
        data = json.loads(SMART_LIST_PATH.read_text(encoding="utf-8"))
        ranked = data.get("ranked", data if isinstance(data, list) else [])
        by_mhz = {}
        for item in ranked:
            if isinstance(item, dict) and item.get("mhz") is not None:
                by_mhz[round(float(item["mhz"]), 3)] = item
        included = set()

        def _add(item):
            key = round(float(item["mhz"]), 3)
            if key in included:
                return
            included.add(key)
            candidates.append({
                "mhz": float(item["mhz"]),
                "modulation": (item.get("modulation") or "unknown").lower(),
                "score": float(item.get("score", 0)),
                "power_db": item.get("avg_power_db"),
                "pair_with": item.get("pair_with", []),
            })

        for item in ranked:
            if not isinstance(item, dict):
                continue
            mhz = item.get("mhz")
            score = item.get("score", 0)
            if mhz is None or score < SMART_MIN_SCORE:
                continue
            _add(item)
            # Always include both sides of a hop pair, even if the partner
            # scored below SMART_MIN_SCORE or would otherwise be truncated.
            for pm in item.get("pair_with", []):
                partner = by_mhz.get(round(float(pm), 3))
                if partner:
                    _add(partner)
                else:
                    # Partner not in ranked list — still add a stub so TX covers it
                    try:
                        pmf = float(pm)
                    except (TypeError, ValueError):
                        continue
                    key = round(pmf, 3)
                    if key not in included:
                        included.add(key)
                        candidates.append({
                            "mhz": pmf,
                            "modulation": "hopping",
                            "score": float(item.get("score", 0)),
                            "power_db": item.get("avg_power_db"),
                            "pair_with": [float(item["mhz"])],
                        })
            if len(included) >= SMART_TOP_N:
                break
    except Exception as e:
        log(f"Could not read {SMART_LIST_PATH.name}: {e}")
    return candidates


def group_by_modulation(items):
    """Group a list of {mhz, modulation, ...} by modulation type."""
    groups = {}
    for it in items:
        mod = it.get("modulation", "unknown")
        groups.setdefault(mod, []).append(it)
    return groups


def write_active_blocks(blocks, exact_hops=None):
    """Write the current transmit blocks so they can be inspected at any time."""
    try:
        out = {
            "timestamp": datetime.now().isoformat(),
            "block_match_modulation": BLOCK_MATCH_MODULATION,
            "invert_modulations": list(INVERT_MODULATIONS) if INVERT_MODULATIONS else [],
            "invert_wave_type": INVERT_WAVE_TYPE,
            "blocks": [
                {
                    "center_mhz": b.get("center_mhz"),
                    "start_mhz": b.get("start_mhz"),
                    "stop_mhz": b.get("stop_mhz"),
                    "width_mhz": b.get("width_mhz"),
                    "modulation": b.get("modulation", "unknown"),
                    "source": b.get("source", ""),
                    "members": b.get("members", []),
                    "tx_wave": b.get("tx_wave", TRANSMIT_TYPE),
                    "tx_amplitude": b.get("tx_amplitude", TX_AMPLITUDE),
                    "avg_waveform_level": b.get("avg_waveform_level"),
                    "inverted": b.get("inverted", _should_invert(b.get("modulation"))),
                    "noise_floor_db": b.get("noise_floor_db"),
                    "member_floors": [
                        {"mhz": m.get("mhz"), "floor_db": m.get("floor_db"), "power_db": m.get("power_db")}
                        for m in (b.get("member_meta") or [])
                    ],
                    "auto_leveled": b.get("auto_leveled", False),
                    "in_band": b.get("in_band"),
                    "capture_file": b.get("capture_file"),
                    "member_amps": b.get("member_amps"),
                    "device_serial": b.get("assigned_device_serial"),
                }
                for b in blocks
            ],
            "exact_hops": [
                {
                    "mhz": h.get("mhz"),
                    "range": h.get("range", ""),
                    "wave_type": h.get("wave_type", TRANSMIT_TYPE),
                    "device_serial": h.get("device_serial"),
                }
                for h in (exact_hops or [])
            ],
        }
        BLOCKS_FILE.write_text(json.dumps(out, indent=2), encoding="utf-8")
        log(f"Active blocks written to {BLOCKS_FILE.name} ({len(blocks)} blocks)")
    except Exception as e:
        log(f"Failed to write active blocks: {e}")


# =============================================================================
# SCANNER FUNCTIONS
# =============================================================================

def run_sweep(start_mhz, stop_mhz, bin_hz, num_sweeps=1, device_serial=None):
    """Sweep using rtl_power (RTL-SDR) or hackrf_sweep."""
    sweeps = defaultdict(list)
    if USE_RTLSDR:
        for i in range(num_sweeps):
            cmd = [
                "rtl_power",
                "-f", f"{start_mhz}M:{stop_mhz}M:{bin_hz}",
                "-i", "1",
                "-1",
                "-g", str(RTL_GAIN),
                "-d", str(RTL_DEVICE),
                "-"
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except Exception:
                continue
            if result.returncode != 0:
                continue
            ts = f"sweep{i}"
            for line in result.stdout.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 7:
                    continue
                try:
                    low = float(parts[2])
                    high = float(parts[3])
                    step = float(parts[4])
                    powers = [float(p) for p in parts[6:] if p]
                    for idx, pwr in enumerate(powers):
                        center = low + (idx + 0.5) * step
                        sweeps[ts].append((center, pwr))
                except (ValueError, IndexError):
                    continue
        return sweeps

    # HackRF path
    device_args = []
    if device_serial:
        device_args = ["-d", device_serial]
    elif HACKRF_DEVICES:
        device_args = ["-d", HACKRF_DEVICES[0]]
    cmd = [
        "hackrf_sweep",
        *device_args,
        "-f", f"{int(start_mhz)}:{int(stop_mhz)}",
        "-w", str(int(bin_hz)),
        "-N", str(num_sweeps)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return sweeps
    for line in result.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            ts = parts[1]
            center = (float(parts[2]) + float(parts[3])) / 2.0
            power = float(parts[6])
            sweeps[ts].append((center, power))
        except ValueError:
            continue
    return sweeps


def find_hop_pairs(sweeps, threshold_db):
    """Look for pairs of frequencies with anti-correlated power across sweeps."""
    if len(sweeps) < 4:
        return []
    freq_powers = defaultdict(list)
    for ts in sorted(sweeps.keys()):
        for center, power in sweeps[ts]:
            if power >= threshold_db - 10:
                freq_powers[center].append(power)
    candidates = {f: ps for f, ps in freq_powers.items() if len(ps) >= len(sweeps) * 0.6}
    if len(candidates) < 2:
        return []
    freqs = list(candidates.keys())
    pairs = []
    for i in range(len(freqs)):
        for j in range(i + 1, len(freqs)):
            p1 = np.array(candidates[freqs[i]])
            p2 = np.array(candidates[freqs[j]])
            n = min(len(p1), len(p2))
            if n < 4:
                continue
            corr = np.corrcoef(p1[:n], p2[:n])[0, 1]
            if corr <= PAIR_CORRELATION_THRESHOLD:
                pairs.append((
                    round_freq_mhz(freqs[i]),
                    round_freq_mhz(freqs[j]),
                    round(float(corr), 3)
                ))
    pairs.sort(key=lambda x: x[2])
    return pairs[:5]


def capture_iq(freq_hz, duration, device_serial=None):
    """Capture IQ data from a specific device."""
    tmp = str(Path(tempfile.gettempdir()) / f"scan_cap_{device_serial or 'default'}.iq")
    n = int(SAMPLE_RATE * duration)
    if USE_RTLSDR:
        cmd = [
            "rtl_sdr",
            "-f", str(int(freq_hz)),
            "-s", str(SAMPLE_RATE),
            "-n", str(n),
            "-g", str(RTL_GAIN),
            "-d", str(RTL_DEVICE),
            tmp
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=duration + 5)
        except Exception:
            return None
        raw = np.fromfile(tmp, dtype=np.uint8)
        if len(raw) < 1000:
            return None
        i = (raw[0::2].astype(np.float32) - 127.5) / 127.5
        q = (raw[1::2].astype(np.float32) - 127.5) / 127.5
        return i + 1j * q

    # HackRF path
    cmd = [
        "hackrf_transfer", "-r", tmp, "-f", str(int(freq_hz)),
        "-s", str(SAMPLE_RATE), "-n", str(n * 2),
        "-a", "0", "-l", str(LNA_GAIN), "-g", str(VGA_GAIN)
    ]
    if device_serial:
        cmd.extend(["-d", device_serial])
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=duration + 5)
    except Exception:
        return None
    raw = np.fromfile(tmp, dtype=np.int8)
    if len(raw) < 1000:
        return None
    # Cap analysis window so a long capture cannot exhaust memory (esp. Windows)
    max_complex = int(SAMPLE_RATE * 0.5)          # 0.5 s max
    if len(raw) > max_complex * 2:
        raw = raw[:max_complex * 2]
    return (raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)) / 128.0


def detect_hopping(iq):
    power = np.abs(iq) ** 2
    block_size = int(SAMPLE_RATE * HOP_BLOCK_MS / 1000)
    if block_size < 1 or len(power) < block_size * 4:
        return False, 0, 0.0
    n_blocks = len(power) // block_size
    blocks = power[:n_blocks * block_size].reshape(n_blocks, block_size).mean(axis=1)
    median_p = np.median(blocks)
    if median_p <= 0:
        return False, 0, 0.0
    on_mask = blocks > (median_p * HOP_ON_THRESHOLD_RATIO)
    transitions = np.sum(np.diff(on_mask.astype(int)) != 0)
    hop_count = int(transitions // 2)
    duty = float(np.mean(on_mask))
    is_hopping = hop_count >= MIN_HOPS_FOR_FLAG and 0.05 < duty < 0.95
    return is_hopping, hop_count, round(duty, 3)


def am_demod_and_tone(iq):
    envelope = np.abs(iq)
    envelope -= np.mean(envelope)
    audio = scipy_signal.resample(envelope, int(len(envelope) * 48000 / SAMPLE_RATE))
    rms = float(np.sqrt(np.mean(audio ** 2)))
    n = min(len(audio), 48000 * 2)
    if n < 1024:
        return rms, None
    windowed = np.real(audio[:n]).astype(np.float64) * np.hanning(n)
    spec = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(n, 1/48000)
    mask = (freqs >= TONE_MIN_HZ) & (freqs <= TONE_MAX_HZ)
    if not np.any(mask):
        return rms, None
    band_spec = spec[mask]
    band_freqs = freqs[mask]
    peak_idx = np.argmax(band_spec)
    peak_db = 20 * np.log10(band_spec[peak_idx] + 1e-12)
    median_db = 20 * np.log10(np.median(band_spec) + 1e-12)
    if peak_db - median_db >= TONE_PROMINENCE_DB:
        return rms, float(band_freqs[peak_idx])
    return rms, None


def classify_modulation(iq):
    """Detect the dominant modulation type from a short IQ capture."""
    if iq is None or len(iq) < 2048:
        return "unknown", False, 0.0, 0.0

    env = np.abs(iq)
    env_mean = float(np.mean(env)) + 1e-12
    env_std = float(np.std(env))
    am_depth = env_std / env_mean

    phase = np.unwrap(np.angle(iq))
    inst_freq = np.diff(phase) * SAMPLE_RATE / (2 * np.pi)
    fm_std = float(np.std(inst_freq))

    n = min(8192, len(iq))
    windowed = iq[:n] * np.hanning(n)
    spec = np.abs(np.fft.fft(windowed))
    spec = spec[1:n // 2] + 1e-12
    geo = np.exp(np.mean(np.log(spec)))
    arith = np.mean(spec)
    flatness = float(geo / arith)

    peak_ratio = float(np.max(spec) / (np.mean(spec) + 1e-12))

    env_n = env - np.mean(env)
    ac_peak = 0.0
    if len(env_n) > 1000:
        ac = np.correlate(env_n[:2000], env_n[:2000], mode="full")
        ac = ac[len(ac)//2:]
        ac_peak = float(np.max(ac[10:200]) / (ac[0] + 1e-12))

    has_waves = flatness < 0.65 or am_depth > 0.12 or fm_std > 400

    fm_level = min(1.0, fm_std / 8000.0)
    waveform_level = float(min(1.0, max(am_depth, fm_level)))

    if flatness > 0.78 and am_depth < 0.08 and fm_std < 250:
        return "noise", False, 0.6, waveform_level
    if am_depth < 0.08 and fm_std < 400 and peak_ratio > 20:
        return "cw", True, 0.85, waveform_level
    if am_depth > 0.28 and fm_std < 2500:
        conf = min(1.0, am_depth * 1.5)
        return "am", True, conf, waveform_level
    if fm_std > 800 and am_depth < 0.35:
        if fm_std < 4000:
            return "nbfm", True, min(1.0, fm_std / 5000), waveform_level
        else:
            return "fm", True, min(1.0, fm_std / 15000), waveform_level
    if fm_std > 1500 and am_depth < 0.25 and ac_peak > 0.2:
        return "fsk", True, min(1.0, 0.5 + ac_peak), waveform_level
    if ac_peak > 0.35 or (am_depth > 0.18 and fm_std > 800):
        return "digital", True, min(1.0, ac_peak + 0.25), waveform_level
    if has_waves:
        return "cw", True, 0.4, waveform_level
    return "noise", False, 0.3, waveform_level


def _save_replay_snippet(freq_hz, iq):
    """Save a short int8 IQ snippet for an unrecognised signal."""
    if not HAS_NUMPY or iq is None:
        return None
    try:
        REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        n = int(SAMPLE_RATE * REPLAY_SNIPPET_SEC)
        snip = iq[:n] if len(iq) > n else iq
        i = np.clip(np.real(snip) * 127, -127, 127).astype(np.int8)
        q = np.clip(np.imag(snip) * 127, -127, 127).astype(np.int8)
        data = np.column_stack((i, q)).ravel().astype(np.int8)
        mhz = freq_hz / 1e6
        path = REPLAY_DIR / f"cap_{mhz:.3f}MHz.iq"
        data.tofile(str(path))
        return str(path)
    except Exception:
        return None


def analyze_frequency(freq_hz, power_db, mode, device_serial=None):
    print(f"    Analyzing {round_freq_mhz(freq_hz):.1f} MHz ({power_db:.1f} dB)..." + (f" on {device_serial}" if device_serial else ""))
    try:
        iq = capture_iq(freq_hz, CAPTURE_SECONDS, device_serial=device_serial)
        if iq is None:
            return None
        rms, tone = am_demod_and_tone(iq)
        is_hopping, hop_count, duty = detect_hopping(iq)
        modulation, has_waves, conf, waveform_level = classify_modulation(iq)
        power_trace = np.abs(iq) ** 2
        block = max(1, len(power_trace) // 50)
        block_power = power_trace[:block*50].reshape(50, block).mean(axis=1)
        variance = float(np.var(block_power))
        signal_type = modulation
        if is_hopping:
            signal_type = "hopping"
        elif variance > np.mean(block_power) * 0.5 and modulation == "cw":
            signal_type = "pulsed"
        priority = 0
        if is_hopping:
            priority += 3
        if has_waves:
            priority += 2
        if power_db >= HIGH_POWER_DB:
            priority += 1
        capture_file = None
        mhz_now = freq_hz / 1e6
        # Save a replay snippet for every analyzed signal inside the auto band
        # (and for unknown signals anywhere). This is what makes capture_file
        # non-null and lets TX replay the captured modulation.
        if (REPLAY_UNKNOWN and modulation == "unknown") or in_auto_band(mhz_now):
            capture_file = _save_replay_snippet(freq_hz, iq)
        return {
            "frequency_hz": int(freq_hz),
            "frequency_mhz": round_freq_mhz(freq_hz),
            "power_db": round(power_db, 1),
            "am_rms": round(rms, 6),
            "tone_hz": round(tone, 1) if tone else None,
            "signal_type": signal_type,
            "modulation": modulation,
            "has_waves": has_waves,
            "mod_confidence": round(conf, 2),
            "mod_waveform_level": round(float(waveform_level), 4),
            "hopping": is_hopping,
            "hop_count": hop_count,
            "duty_cycle": duty,
            "priority": priority,
            "capture_file": capture_file,
        }
    except Exception as e:
        print(f"      Error: {e}")
        return None


def _local_noise_floor(power_map, mhz, fallback_db):
    """Noise floor from sweep bins within LOCAL_FLOOR_SPAN_MHZ of mhz."""
    try:
        lo = (mhz - LOCAL_FLOOR_SPAN_MHZ) * 1e6
        hi = (mhz + LOCAL_FLOOR_SPAN_MHZ) * 1e6
        vals = []
        for center, powers in power_map.items():
            if lo <= center <= hi:
                vals.extend(powers)
        if len(vals) >= 4 and HAS_NUMPY:
            return float(np.percentile(np.array(vals), 25))
    except Exception:
        pass
    return fallback_db


def _prune_old(directory, pattern, keep):
    """Keep the newest `keep` files matching pattern; delete the rest."""
    try:
        files = sorted(Path(directory).glob(pattern),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[keep:]:
            try:
                p.unlink()
            except Exception:
                pass
    except Exception:
        pass


def run_sdr_scan():
    """Run the full multi-range scanner and write frequencies.json."""
    if not HAS_NUMPY or not HAS_SCIPY:
        log("ERROR: numpy and scipy are required for the scanner. Install them with: pip install numpy scipy")
        return False

    print("HackRF multi-range AM scanner (with hop-pair search)")
    print(f"Default threshold: {POWER_THRESHOLD_DB} dB | Top {TOP_N_PER_RANGE} per range\n")

    all_results = {
        "timestamp": datetime.now().isoformat(),
        "settings": {
            "power_threshold_db": POWER_THRESHOLD_DB,
            "top_n_per_range": TOP_N_PER_RANGE,
            "capture_seconds": CAPTURE_SECONDS,
            "lna_gain": LNA_GAIN,
            "vga_gain": VGA_GAIN,
        },
        "ranges": []
    }

    for rng in RANGES:
        if not rng["enabled"]:
            print(f"Skipping {rng['name']} (disabled)")
            continue
        thresh = rng.get("threshold_db", POWER_THRESHOLD_DB)
        do_capture = rng.get("capture", True) and TOP_N_PER_RANGE > 0
        print(f"\n=== {rng['name']} ({rng['start_mhz']}-{rng['stop_mhz']} MHz)  thresh={thresh} dB ===")

        # Select HackRF device for this range if multi-device mode is configured
        device_serial = None
        if HACKRF_DEVICES and RANGE_DEVICE_MAP:
            idx = RANGE_DEVICE_MAP.get(rng["name"])
            if idx is not None and 0 <= idx < len(HACKRF_DEVICES):
                device_serial = HACKRF_DEVICES[idx]
            elif HACKRF_DEVICES:
                device_serial = HACKRF_DEVICES[0]
        elif HACKRF_DEVICES:
            device_serial = HACKRF_DEVICES[0]

        sweeps = run_sweep(rng["start_mhz"], rng["stop_mhz"], rng["bin_hz"], PAIR_SWEEPS, device_serial)
        power_map = defaultdict(list)
        for ts_data in sweeps.values():
            for center, power in ts_data:
                power_map[center].append(power)

        _all_powers = [p for powers in power_map.values() for p in powers]
        if _all_powers:
            noise_floor_db = float(np.percentile(np.array(_all_powers), 25))
        else:
            noise_floor_db = thresh
        print(f"  Noise floor: {noise_floor_db:.1f} dB")

        strong = []
        for center, powers in power_map.items():
            max_p = max(powers)
            if max_p >= thresh:
                strong.append((center, max_p))
        strong.sort(key=lambda x: x[1], reverse=True)
        top = strong[:TOP_N_PER_RANGE] if do_capture else []

        print(f"  Candidates above threshold: {len(strong)}")
        if strong:
            print("  Strongest frequencies:")
            for freq, power in strong[:10]:
                print(f"    {round_freq_mhz(freq):8.1f} MHz   {power:6.1f} dB")

        pairs = find_hop_pairs(sweeps, thresh)
        if pairs:
            print("  Possible hop pairs (anti-correlated):")
            for f1, f2, corr in pairs:
                print(f"    {f1:.1f} MHz  <->  {f2:.1f} MHz   (corr={corr})")
        else:
            print("  No clear hop pairs detected in this range.")

        range_result = {
            "name": rng["name"],
            "start_mhz": rng["start_mhz"],
            "stop_mhz": rng["stop_mhz"],
            "threshold_db": thresh,
            "noise_floor_db": round(noise_floor_db, 1),
            "mode": rng["mode"],
            "capture_enabled": do_capture,
            "candidates_found": len(strong),
            "hop_pairs": [{"freq1_mhz": f1, "freq2_mhz": f2, "correlation": corr} for f1, f2, corr in pairs],
            "signals": []
        }

        hop_list = []
        if top:
            print(f"  Detailed analysis of top {len(top)}:")
            capture_device = device_serial if not USE_RTLSDR else None
            for freq, power in top:
                info = analyze_frequency(freq, power, rng["mode"], device_serial=capture_device)
                if info:
                    local_floor = _local_noise_floor(power_map, info["frequency_mhz"], noise_floor_db)
                    info["range_noise_floor_db"] = round(noise_floor_db, 1)
                    info["noise_floor_db"] = round(local_floor, 1)
                    info["snr_db"] = round(info["power_db"] - local_floor, 1)
                    range_result["signals"].append(info)
                    tone_str = f"{info['tone_hz']} Hz" if info["tone_hz"] else "none"
                    hop_str = f"  hops={info['hop_count']} duty={info['duty_cycle']}" if info["hopping"] else ""
                    print(f"      {info['frequency_mhz']:.1f} MHz  {info['power_db']} dB  "
                          f"mod={info.get('modulation','?')} waves={info.get('has_waves')} "
                          f"lvl={info.get('mod_waveform_level',0):.2f} "
                          f"type={info['signal_type']}{hop_str}  tone={tone_str}  "
                          f"pri={info.get('priority',0)}")
                    if info["hopping"]:
                        hop_list.append(info["frequency_mhz"])

        if hop_list:
            print(f"  Single-channel hopping flags: {', '.join(f'{f:.1f}' for f in hop_list)} MHz")

        all_results["ranges"].append(range_result)
        time.sleep(0.2)

    # Explicitly analyze manual frequencies that fall inside the auto band so
    # they receive a modulation classification, noise floor, and capture file.
    if USE_MANUAL and CAPTURE_RANGE_MANUAL:
        print("\n=== Manual frequencies inside auto band ===")
        for ent in _parse_manual_entries():
            mhz = float(ent["mhz"])
            if not in_auto_band(mhz):
                continue
            print(f"  Capturing manual {mhz:.3f} MHz")
            info = analyze_frequency(mhz * 1e6, -60.0, "both")
            if not info:
                continue
            # Attach to the manual range result (create if missing)
            manual_rng = next((r for r in all_results["ranges"] if r.get("name") == "manual"), None)
            if manual_rng is None:
                manual_rng = {
                    "name": "manual",
                    "start_mhz": AUTO_BAND_MIN_MHZ,
                    "stop_mhz": AUTO_BAND_MAX_MHZ,
                    "noise_floor_db": None,
                    "signals": [],
                    "hop_pairs": [],
                }
                all_results["ranges"].append(manual_rng)
            # Local floor from the manual range sweep if present
            info["noise_floor_db"] = info.get("noise_floor_db") or manual_rng.get("noise_floor_db")
            info["source"] = "manual"
            manual_rng["signals"].append(info)
            print(f"      manual {info['frequency_mhz']:.3f} MHz  mod={info.get('modulation')}  "
                  f"lvl={info.get('mod_waveform_level',0):.2f}  cap={bool(info.get('capture_file'))}")

    if OUTPUT_FILE.exists():
        backup_name = BACKUP_DIR / f"frequencies_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        shutil.copy2(OUTPUT_FILE, backup_name)
        print(f"\nPrevious full results backed up to {backup_name}")
        _prune_old(BACKUP_DIR, "frequencies_*.json", BACKUP_KEEP)
        _prune_old(REPLAY_DIR, "cap_*.iq", CAPTURE_KEEP)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Full results:          {OUTPUT_FILE.resolve()}")

    # Append to long-term history
    try:
        history_entry = {
            "timestamp": all_results["timestamp"],
            "signals": [
                {
                    "mhz": s.get("frequency_mhz"),
                    "power_db": s.get("power_db"),
                    "range": s.get("range", ""),
                    "type": s.get("signal_type"),
                    "modulation": s.get("modulation"),
                    "has_waves": s.get("has_waves"),
                    "hopping": s.get("hopping"),
                    "priority": s.get("priority"),
                    "tone_hz": s.get("tone_hz"),
                    "mod_waveform_level": s.get("mod_waveform_level"),
                    "noise_floor_db": s.get("noise_floor_db"),
                    "snr_db": s.get("snr_db"),
                }
                for rng in all_results.get("ranges", [])
                for s in rng.get("signals", [])
            ]
        }
        _rotate_file(HISTORY_FILE, HISTORY_MAX_BYTES, HISTORY_KEEP)
        with open(HISTORY_FILE, "a", encoding="utf-8") as hf:
            hf.write(json.dumps(history_entry) + "\n")
        log(f"Appended {len(history_entry['signals'])} signals to history")
    except Exception as e:
        log(f"Failed to write history: {e}")

    return True


def run_intelligence():
    """Score history + latest scan, compare with the previous ranking."""
    from collections import Counter, defaultdict

    scans = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            scans.append(json.loads(line))
                        except Exception:
                            continue
        except Exception:
            pass

    latest = []
    if JSON_PATH.exists():
        try:
            data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
            for rng in data.get("ranges", []):
                for s in rng.get("signals", []):
                    s = dict(s)
                    s["range"] = rng.get("name", "")
                    latest.append(s)
        except Exception:
            pass

    if not scans and not latest:
        log("Intelligence: no scan data yet")
        return []

    total = max(1, len(scans))
    counts = Counter()
    hop_counts = Counter()
    wave_counts = Counter()
    mod_counts = defaultdict(Counter)
    power_sum = defaultdict(float)
    power_n = Counter()
    tone_counts = Counter()
    pair_counts = Counter()

    for entry in scans:
        seen = set()
        for s in entry.get("signals", []):
            mhz = s.get("mhz")
            if mhz is None:
                continue
            mhz = round(float(mhz), 1)
            seen.add(mhz)
            counts[mhz] += 1
            if s.get("hopping"):
                hop_counts[mhz] += 1
            if s.get("has_waves"):
                wave_counts[mhz] += 1
            mod = (s.get("modulation") or s.get("type") or "").lower()
            if mod:
                mod_counts[mhz][mod] += 1
            if s.get("power_db") is not None:
                power_sum[mhz] += float(s["power_db"])
                power_n[mhz] += 1
            if s.get("tone_hz"):
                tone_counts[mhz] += 1
        freqs = sorted(seen)
        for i in range(len(freqs)):
            for j in range(i + 1, len(freqs)):
                pair_counts[(freqs[i], freqs[j])] += 1

    for s in latest:
        mhz = s.get("frequency_mhz") or s.get("mhz")
        if mhz is None:
            continue
        mhz = round(float(mhz), 1)
        if mhz not in counts:
            counts[mhz] += 1
        if s.get("hopping"):
            hop_counts[mhz] += 1
        if s.get("has_waves"):
            wave_counts[mhz] += 1
        mod = (s.get("modulation") or s.get("signal_type") or "").lower()
        if mod:
            mod_counts[mhz][mod] += 1

    pair_bonus = Counter()
    for (a, b), c in pair_counts.items():
        if c >= max(2, total * 0.2):
            pair_bonus[a] += 1
            pair_bonus[b] += 1

    hop_partner = defaultdict(set)
    if JSON_PATH.exists():
        try:
            _d = json.loads(JSON_PATH.read_text(encoding="utf-8"))
            for rng in _d.get("ranges", []):
                for hp in rng.get("hop_pairs", []):
                    f1 = hp.get("freq1_mhz")
                    f2 = hp.get("freq2_mhz")
                    if f1 is None or f2 is None:
                        continue
                    f1 = round(float(f1), 1)
                    f2 = round(float(f2), 1)
                    hop_partner[f1].add(f2)
                    hop_partner[f2].add(f1)
                    for f in (f1, f2):
                        if f not in counts:
                            counts[f] += 1
                    pair_bonus[f1] += 1
                    pair_bonus[f2] += 1
        except Exception:
            pass

    prev_scores = {}
    if SMART_PREV_PATH.exists():
        try:
            prev = json.loads(SMART_PREV_PATH.read_text(encoding="utf-8"))
            for item in prev.get("ranked", []):
                prev_scores[item.get("mhz")] = item.get("score", 0)
        except Exception:
            pass

    ranked = []
    for mhz, n in counts.items():
        persistence = n / total
        avg_pwr = (power_sum[mhz] / power_n[mhz]) if power_n[mhz] else -80.0
        top_mod = mod_counts[mhz].most_common(1)
        top_mod = top_mod[0][0] if top_mod else "unknown"
        sc = 0.0
        sc += W_PERSISTENCE * persistence
        sc += W_HOPPING * (hop_counts[mhz] / max(1, n))
        sc += W_WAVES * (wave_counts[mhz] / max(1, n))
        sc += W_MOD.get(top_mod, 0.0)
        sc += W_POWER * max(0.0, avg_pwr + 80.0)
        sc += W_PAIR * min(pair_bonus[mhz], 3)
        sc += W_TONE * (tone_counts[mhz] / max(1, n))
        prev = prev_scores.get(mhz)
        if prev is None:
            trend = "new"
        elif sc > prev + 0.3:
            trend = "rising"
        elif sc < prev - 0.3:
            trend = "falling"
        else:
            trend = "steady"
        ranked.append({
            "mhz": mhz,
            "score": round(sc, 2),
            "scans": n,
            "persistence": round(persistence, 2),
            "avg_power_db": round(avg_pwr, 1),
            "modulation": top_mod,
            "hop_frac": round(hop_counts[mhz] / max(1, n), 2),
            "wave_frac": round(wave_counts[mhz] / max(1, n), 2),
            "pair_links": pair_bonus[mhz],
            "pair_with": sorted(hop_partner.get(mhz, [])),
            "trend": trend,
            "prev_score": prev,
        })
    ranked.sort(key=lambda x: -x["score"])

    if SMART_LIST_PATH.exists():
        try:
            shutil.copy2(SMART_LIST_PATH, SMART_PREV_PATH)
        except Exception:
            pass

    out = {
        "timestamp": datetime.now().isoformat(),
        "scans_compared": len(scans),
        "ranked": ranked,
    }
    SMART_LIST_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")

    new_n = sum(1 for r in ranked if r["trend"] == "new")
    rising_n = sum(1 for r in ranked if r["trend"] == "rising")
    log(f"Intelligence: {len(ranked)} scored, {new_n} new, {rising_n} rising "
        f"→ {SMART_LIST_PATH.name}")
    for r in ranked[:5]:
        log(f"  {r['mhz']:.1f} MHz  score={r['score']:.2f}  "
            f"{r['modulation']}  {r['trend']}")
    return ranked


def run_scan_safely():
    """Run the scanner in a separate process, then run intelligence."""
    needs_hackrf_for_scan = not USE_RTLSDR
    was_persistent = _use_persistent and needs_hackrf_for_scan
    if was_persistent:
        log("Closing HackRF TX session for scan...")
        close_persistent_hackrf()
        time.sleep(1.0)

    script = str(Path(__file__).resolve())
    log(f"Starting scan subprocess: {script} --scan")
    success = False
    try:
        proc = subprocess.run(
            [sys.executable, script, "--scan"],
            timeout=SCAN_TIMEOUT_SEC,
        )
        success = proc.returncode == 0
        if success:
            log("Scan subprocess finished OK")
        else:
            log(f"Scan subprocess exited with code {proc.returncode} — continuing TX")
    except subprocess.TimeoutExpired:
        log(f"Scan subprocess timed out after {SCAN_TIMEOUT_SEC}s — continuing TX")
    except Exception as e:
        log(f"Scan subprocess failed: {e} — continuing TX")

    try:
        run_intelligence()
    except Exception as e:
        log(f"Intelligence step failed: {e}")

    time.sleep(1.0)
    if was_persistent or needs_hackrf_for_scan:
        log("Re-opening HackRF for TX...")
        init_persistent_hackrf()
    return success


def analyze_history(cmd):
    """Analyze the long-term frequency history file."""
    if not HISTORY_FILE.exists():
        print(f"No history file found at {HISTORY_FILE}")
        return
    from collections import Counter, defaultdict
    scans = []
    freq_counts = Counter()
    pair_counts = Counter()
    total_scans = 0
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    total_scans += 1
                    freqs = []
                    for s in entry.get("signals", []):
                        mhz = s.get("mhz")
                        if mhz is not None:
                            mhz = round(float(mhz), 1)
                            freq_counts[mhz] += 1
                            freqs.append(mhz)
                    freqs = sorted(set(freqs))
                    for i in range(len(freqs)):
                        for j in range(i + 1, len(freqs)):
                            pair_counts[(freqs[i], freqs[j])] += 1
                    scans.append(entry)
                except Exception:
                    continue
    except Exception as e:
        print(f"Error reading history: {e}")
        return

    print(f"History file: {HISTORY_FILE}")
    print(f"Total scans recorded: {total_scans}")
    if total_scans == 0:
        return

    if cmd in ("--history", "--stats", "--top"):
        print("\n=== Top frequencies (by number of scans they appeared in) ===")
        for mhz, count in freq_counts.most_common(30):
            pct = 100.0 * count / total_scans
            print(f"  {mhz:8.1f} MHz   {count:5d} scans  ({pct:5.1f}%)")

    if cmd in ("--history", "--stats", "--persistent"):
        print("\n=== Frequencies present in most scans (>= 50%) ===")
        persistent = [(mhz, c) for mhz, c in freq_counts.items() if c >= total_scans * 0.5]
        persistent.sort(key=lambda x: -x[1])
        for mhz, count in persistent[:40]:
            pct = 100.0 * count / total_scans
            print(f"  {mhz:8.1f} MHz   {count:5d} / {total_scans}  ({pct:5.1f}%)")
        if not persistent:
            print("  (none)")

    if cmd in ("--history", "--stats", "--pairs"):
        print("\n=== Top co-occurring frequency pairs ===")
        for (f1, f2), count in pair_counts.most_common(20):
            pct = 100.0 * count / total_scans
            print(f"  {f1:7.1f} <-> {f2:7.1f} MHz   {count:5d} scans  ({pct:5.1f}%)")


def main():
    global last_update_time, running, frequencies
    global AMP_ENABLE, TX_AMPLITUDE, TX_GAIN, TX_TYPE, TX_SPEED_HZ
    global TX_HOP_SEC, TX_DURATION_SEC, TX_SLEEP_SEC, TX_JITTER_SEC, DWELL_SEC
    global _use_persistent, _hackrf_devs, _hackrf_tx_active
    running = True

    # Apply PortaPack-style transmit options when enabled
    if USE_TRANSMIT_OPTIONS:
        TX_TYPE = TRANSMIT_TYPE
        TX_SPEED_HZ = TRANSMIT_SPEED_HZ
        TX_HOP_SEC = TRANSMIT_HOP_SEC
        TX_DURATION_SEC = TRANSMIT_TX_SEC
        TX_SLEEP_SEC = TRANSMIT_SLEEP_SEC
        TX_JITTER_SEC = TRANSMIT_JITTER_SEC
        TX_GAIN = TRANSMIT_GAIN
        AMP_ENABLE = TRANSMIT_AMP
        TX_AMPLITUDE = TRANSMIT_AMPLITUDE
        DWELL_SEC = TRANSMIT_HOP_SEC if TRANSMIT_HOP_SEC > 0 else 0.05
        log(f"Transmit options active: type={TX_TYPE}, speed={TX_SPEED_HZ} Hz, "
            f"hop={TX_HOP_SEC}s, gain={TX_GAIN}, amp={'ON' if AMP_ENABLE else 'OFF'}, "
            f"amp={TX_AMPLITUDE}")

    # Standalone intelligence (no radio needed)
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("--smart", "--rank", "--intelligence"):
        run_intelligence()
        sys.exit(0)

    # List detected HackRFs by index (no serial needed for Option 1 / RANGE_DEVICE_MAP)
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("--devices", "--hackrfs"):
        if find_hackrf():
            print("Detected HackRF devices (index → serial):")
            for i, s in enumerate(HACKRF_SERIALS):
                print(f"  index {i}: {s}")
            print("Option 1 fixed split uses index 0 = 400-2000 MHz, index 1 = 2000-6000 MHz.")
            print("RANGE_DEVICE_MAP can use these indexes, e.g. {'410-430': 0, '420-800': 1}")
        else:
            print("No HackRF devices detected.")
        sys.exit(0)

    # Show current active blocks (and optionally the frequencies inside each)
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("--blocks", "--show-blocks", "--block-freqs", "--block-members"):
        show_members = sys.argv[1].lower() in ("--block-freqs", "--block-members", "--blocks", "--show-blocks")
        if BLOCKS_FILE.exists():
            try:
                data = json.loads(BLOCKS_FILE.read_text(encoding="utf-8"))
                print(f"Active blocks (as of {data.get('timestamp', '?')}):")
                for b in data.get("blocks", []):
                    inv = "INV" if b.get("inverted") else "   "
                    print(f"  {b.get('center_mhz'):>7.1f} MHz  "
                          f"{b.get('start_mhz'):.1f}–{b.get('stop_mhz'):.1f}  "
                          f"mod={b.get('modulation','?'):<8}  "
                          f"wave={b.get('tx_wave','?'):<10}  "
                          f"amp={b.get('tx_amplitude', 0):.2f}  {inv}  "
                          f"src={b.get('source','')}")
                    if show_members:
                        members = b.get("members") or []
                        if members:
                            freqs = ", ".join(f"{m:.3f}" for m in members)
                            print(f"           members ({len(members)}): {freqs}")
                        else:
                            print("           members: (none)")
                exact = data.get("exact_hops", [])
                if exact:
                    print(f"Exact hops: {len(exact)}")
                    for h in exact[:20]:
                        print(f"  {h.get('mhz'):.3f} MHz  ({h.get('range','')})")
            except Exception as e:
                print(f"Could not read {BLOCKS_FILE}: {e}")
        else:
            print(f"No active blocks file yet ({BLOCKS_FILE})")
        sys.exit(0)

    # History analysis commands
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("--history", "--stats", "--top", "--pairs", "--persistent"):
        analyze_history(sys.argv[1].lower())
        sys.exit(0)

    # Test / scan-only mode
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("--scan", "-s", "scan", "--test", "test", "--test-scan"):
        print("=" * 60)
        print("  SDR Scan Test Mode")
        print("=" * 60)
        success = run_sdr_scan()
        if success:
            print("\n[+] Scan completed successfully.")
            print(f"    Results written to: {OUTPUT_FILE}")
        else:
            print("\n[!] Scan failed or dependencies missing.")
            sys.exit(1)
        sys.exit(0)

    print("=" * 60)
    print("  Continuous Frequency Sweep (RTL-SDR / HackRF)")
    print("=" * 60)
    print(f"Script directory : {SCRIPT_DIR}")
    print(f"JSON             : {JSON_PATH}")
    print(f"Update every     : {UPDATE_INTERVAL_SEC} s")
    print(f"Scanner          : {'RTL-SDR' if USE_RTLSDR else 'HackRF'}")
    if USE_RTLSDR:
        print(f"RTL gain         : {RTL_GAIN} dB")
    if HACKRF_DEVICES:
        print(f"HackRF devices   : {len(HACKRF_DEVICES)} configured")
        if RANGE_DEVICE_MAP:
            print(f"Range→device map : {RANGE_DEVICE_MAP}")
    print(f"Dwell per freq   : {DWELL_SEC} s  ({NUM_SAMPLES} samples)")
    print(f"Waveform type    : {TX_TYPE}")
    print(f"Speed            : {TX_SPEED_HZ} Hz")
    print(f"TX duration      : {TX_DURATION_SEC} s")
    print(f"Sleep            : {TX_SLEEP_SEC} s")
    print(f"Jitter           : ±{TX_JITTER_SEC} s")
    print(f"TX Gain          : {TRANSMIT_GAIN if USE_TRANSMIT_OPTIONS else TX_GAIN}")
    print(f"RF Amp           : {'ON' if (TRANSMIT_AMP if USE_TRANSMIT_OPTIONS else AMP_ENABLE) else 'OFF'}")
    print(f"Default tone     : {TX_TONE_OFFSET_HZ} Hz (JSON tone_hz overrides when present)")
    print(f"Amplitude        : {TX_AMPLITUDE}")
    if APPLY_180_PHASE_SHIFT:
        print(f"180° phase shift : {PHASE_SHIFT_MIN_MHZ:.0f}–{PHASE_SHIFT_MAX_MHZ:.0f} MHz "
              f"(applied on carrier even with 1 HackRF)")
    print(f"TX mode          : block hop (HackRF transmit style)")
    if USE_TRANSMIT_OPTIONS:
        print(f"Transmit options   : ON")
        print(f"  Type           : {TRANSMIT_TYPE}")
        print(f"  Speed          : {TRANSMIT_SPEED_HZ} Hz")
        print(f"  Hop            : {TRANSMIT_HOP_SEC}s per {TRANSMIT_SEGMENT_HZ/1e6:.1f} MHz segment")
        print(f"  TX / Sleep     : {TRANSMIT_TX_SEC}s / {TRANSMIT_SLEEP_SEC}s  (jitter ±{TRANSMIT_JITTER_SEC}s)")
        print(f"  Gain / Amp     : {TRANSMIT_GAIN} / {'ON' if TRANSMIT_AMP else 'OFF'}  (amplitude={TRANSMIT_AMPLITUDE})")
        print(f"  Block gap      : {BLOCK_GAP_MHZ} MHz  (max width {BLOCK_MAX_WIDTH_MHZ} MHz)")
        if CLICK_SMOOTH:
            print(f"  Click smooth   : ON (±{CLICK_JITTER_FRAC:.0%} hop variation)")
        else:
            print(f"  Click smooth   : OFF")
        if FILL_NOISE:
            print(f"  Fill noise     : ON (level {FILL_NOISE_LEVEL})")
        else:
            print(f"  Fill noise     : OFF")
        print(f"  Lock on targets: {'ON' if LOCK_ON_TARGETS else 'OFF'}")
        print(f"  Block by nearby+mod: {'ON' if BLOCK_MATCH_MODULATION else 'OFF'}")
        if INVERT_MODULATIONS:
            mode = "scanned-match" if INVERT_USE_SCANNED_WAVEFORM else INVERT_WAVE_TYPE
            print(f"  Invert mods      : {', '.join(INVERT_MODULATIONS)} → {mode} (only {AUTO_BAND_MIN_MHZ:.0f}-{AUTO_BAND_MAX_MHZ:.0f} MHz)")
        else:
            print(f"  Invert mods      : none (all use {TRANSMIT_TYPE})")
        if NOISE_FLOOR_COMPENSATE:
            print(f"  Auto band        : {AUTO_BAND_MIN_MHZ:.0f}-{AUTO_BAND_MAX_MHZ:.0f} MHz")
            print(f"    noise floor    : +{NOISE_FLOOR_MARGIN_DB:.0f} dB (per-freq, amp {NOISE_FLOOR_MIN_AMP:.2f}-1.00)")
            if DESENSE_POWER_DB is not None:
                print(f"    desense        : signal +{DESENSE_POWER_DB:.0f} dB")
            if FM_CAPTURE_CHECK:
                print(f"    FM capture     : FM signal +{FM_CAPTURE_RATIO_DB:.0f} dB")
            if REPLAY_UNKNOWN:
                print(f"    replay unknown : {'ON' if REPLAY_INVERT else 'AS-IS'}")
        else:
            print(f"  Auto band        : OFF")
    if BASEBAND_FILTER_HZ:
        print(f"Baseband filter  : {BASEBAND_FILTER_HZ/1e6:.2f} MHz")
    else:
        print(f"Baseband filter  : auto (from {SAMPLE_RATE/1e6:.1f} Msps)")

    # Print Multi-HackRF options
    if ENABLE_MULTI_HACKRF_TX:
        print("\n--- Multi-HackRF TX Options ---")
        if OPTION3_REALTIME_INVERSE_RX_TX:
            print("  Option 3 Active: Real-time Inverse RX/TX")
            print(f"    Overlap RX/TX  : {'ON' if OPTION3_OVERLAP_RX_TX else 'OFF'}")
            if OPTION3_OVERLAP_RX_TX:
                print("    Mode           : daisy-chain across band splits")
                for split in DEVICE_BAND_SPLITS:
                    print(f"      Device {split['index']}: {split['start_mhz']:.0f}-{split['stop_mhz']:.0f} MHz")
                print(f"    Min TX/RX sep  : {OPTION3_MIN_TX_RX_SEPARATION_MHZ:.0f} MHz")
            else:
                print(f"    Mode           : sequential listen-then-TX")
                print(f"    Range          : {AUTO_BAND_MIN_MHZ:.0f}-{AUTO_BAND_MAX_MHZ:.0f} MHz")
            print(f"    RX Duration    : {OPTION3_RX_DURATION_SEC}s")
        elif OPTION2_PHASE_SHIFT_ASSIGNMENT:
            print("  Option 2 Active: 180° Phase Shift Assignment")
            print(f"    Device 0: Normal TX")
            print(f"    Device 1: Phase-Shifted TX (in {PHASE_SHIFT_MIN_MHZ:.0f}-{PHASE_SHIFT_MAX_MHZ:.0f} MHz band)")
            print("    Waveform: last captured modulation at inverse amplitude")
        elif OPTION1_FIXED_RANGE_SPLIT:
            print("  Option 1 Active: Fixed Range Split")
            print("    Device 0: 400-2000 MHz")
            print("    Device 1: 2000-6000 MHz")
        elif RANGE_DEVICE_MAP:
            print("  Option 1 Active: Range-to-Device Mapping")
            for r_name, r_dev_idx in RANGE_DEVICE_MAP.items():
                print(f"    Range {r_name}: Device {r_dev_idx}")
        else:
            print("  Option 1 Active: Round-Robin Block Distribution")
            if not OPTION3_REALTIME_INVERSE_RX_TX:
                print("    Waveform: last captured modulation at inverse amplitude")

    if MANUAL_FREQUENCIES:
        print(f"Manual freqs     :")
        for ent in _parse_manual_entries():
            print(f"    {ent['mhz']:.3f} MHz  bw={ent['bandwidth']} MHz  step={ent['step']} MHz")
        print(f"Sources          : manual={USE_MANUAL}  scanned={USE_SCANNED}  smart={USE_SMART}")
    disabled_tx = [name for name, enabled in TX_ENABLED_BY_RANGE.items() if not enabled]
    if disabled_tx:
        print(f"TX disabled      : {', '.join(disabled_tx)}")
    else:
        print("TX disabled      : none")
    print("=" * 60)

    # --- Initial Setup ---
    if not find_hackrf():
        if USE_RTLSDR:
            log("No HackRF found — running in RTL-SDR scan-only mode (TX disabled).")
            log("Install rtl-sdr tools (rtl_power, rtl_sdr) if not already present.")
        else:
            log("ERROR: hackrf_transfer binary not found. Install HackRF tools and ensure it is in PATH.")
            sys.exit(1)

    num_hackrfs = len(HACKRF_SERIALS)
    log(f"Using hackrf_transfer. Found {num_hackrfs} HackRF device(s): {HACKRF_SERIALS}")

    # Validate multi-HackRF options
    if ENABLE_MULTI_HACKRF_TX and num_hackrfs >= 2:
        if OPTION3_REALTIME_INVERSE_RX_TX:
            log("Option 3 active: Real-time Inverse RX/TX mode")
        elif OPTION2_PHASE_SHIFT_ASSIGNMENT:
            log("Option 2 active: Phase Shift Assignment mode")
        elif OPTION1_FIXED_RANGE_SPLIT:
            log("Option 1 active: Fixed Range Split mode")
        else:
            log("Option 1 active: Round-robin distribution mode")

    # Initialize persistent mode if possible
    init_persistent_hackrf()

    # Initial scan if needed
    if (USE_SCANNED or USE_SMART) and not JSON_PATH.exists() and not SMART_LIST_PATH.exists():
        log("Running initial scan to populate frequencies / smart list...")
        run_scan_safely()

    load_frequencies()

    if not frequencies and not (USE_SCANNED or USE_SMART):
        log("ERROR: No frequencies loaded. Manual list empty and other sources disabled.")
        sys.exit(1)

    # --- Main Loop ---
    last_update_time = time.time()
    sweep_count = 0
    fail_streak = 0

    while running:
        # Scheduled scan
        now = time.time()
        if RESTART_AFTER_HOURS and (now - _process_start) >= RESTART_AFTER_HOURS * 3600:
            restart_process(f"scheduled {RESTART_AFTER_HOURS}h refresh")
        if SCAN_ALWAYS and (now - last_update_time) >= UPDATE_INTERVAL_SEC:
            log("--- Hourly update triggered ---")
            run_scan_safely()
            load_frequencies()
            last_update_time = time.time()
            if RESTART_AFTER_EACH_SCAN:
                restart_process("post-scan refresh")
        elif SCAN_ALWAYS and sweep_count == 0:
            nxt = UPDATE_INTERVAL_SEC - (now - last_update_time)
            log(f"Next scan in {max(0, int(nxt))}s (every {UPDATE_INTERVAL_SEC}s)")

        if not frequencies:
            time.sleep(5)
            continue

        # --- Build TX Plan ---
        all_items = []
        if USE_MANUAL:
            for ent in _parse_manual_entries():
                all_items.append({
                    "mhz": ent["mhz"],
                    "modulation": "manual",
                    "source": "manual",
                    "range": "manual",
                    "bandwidth": ent.get("bandwidth", MANUAL_BANDWIDTH_MHZ),
                    "whole": True,
                })
        if USE_SCANNED:
            scanned = load_scanned_frequencies()
            for s in scanned:
                mod = (s.get("modulation") or "unknown").lower()
                if s.get("hopping"):
                    mod = "hopping"
                all_items.append({
                    "mhz": s["mhz"],
                    "modulation": mod,
                    "source": "scanned",
                    "range": s.get("range", ""),
                    "whole": s.get("whole", False),
                    "bandwidth": MANUAL_BANDWIDTH_MHZ if (FORCE_WHOLE_MHZ and s.get("whole")) else None,
                    "mod_waveform_level": s.get("mod_waveform_level", 0.0),
                    "power_db": s.get("power_db"),
                    "noise_floor_db": s.get("noise_floor_db"),
                    "capture_file": s.get("capture_file"),
                })
        if USE_SMART:
            smart = load_smart_frequencies()
            if smart:
                log(f"Smart list: {len(smart)} ranked frequency(ies)")
                pairs = [(s["mhz"], s.get("pair_with")) for s in smart if s.get("pair_with")]
                if pairs:
                    for mhz, partners in pairs:
                        log(f"  [HopPair] {mhz:.1f} MHz paired with {partners}")
                else:
                    log("  [HopPair] none in smart list this cycle")
                for s in smart:
                    mod = (s.get("modulation") or "unknown").lower()
                    all_items.append({
                        "mhz": s["mhz"],
                        "modulation": mod,
                        "source": "smart",
                        "range": s.get("range", ""),
                        "whole": is_whole_mhz(s["mhz"]) if FORCE_WHOLE_MHZ else False,
                        "bandwidth": SMART_BLOCK_BANDWIDTH_MHZ if (FORCE_WHOLE_MHZ and is_whole_mhz(s["mhz"])) else None,
                        "mod_waveform_level": s.get("mod_waveform_level", 0.0),
                        "power_db": s.get("power_db"),
                        "noise_floor_db": s.get("noise_floor_db"),
                        "capture_file": s.get("capture_file"),
                        "pair_with": s.get("pair_with", []),
                    })
            else:
                log("USE_SMART is True but no ranked list found yet")

        # Filter by TX enable. Specific range switches override source.
        filtered_items = []
        for item in all_items:
            rng = item.get("range") or ""
            if not rng or rng in ("scanned", "smart", "manual"):
                inferred = range_name_for_mhz(item.get("mhz"))
                if inferred:
                    rng = inferred
            if is_tx_enabled(rng, item.get("mhz")):
                item["range"] = rng or item.get("range")
                filtered_items.append(item)
            else:
                log(f"  TX disabled for {item.get('mhz')} MHz (range={rng})")
        # Share capture_file / noise floor from scanned onto smart/manual at same MHz
        by_mhz = {}
        for it in filtered_items:
            key = round(float(it["mhz"]), 3)
            by_mhz.setdefault(key, []).append(it)
        for group in by_mhz.values():
            cap = next((g.get("capture_file") for g in group if g.get("capture_file")), None)
            nf = next((g.get("noise_floor_db") for g in group if g.get("noise_floor_db") is not None), None)
            lvl = next((g.get("mod_waveform_level") for g in group if g.get("mod_waveform_level")), None)
            for g in group:
                if cap and not g.get("capture_file"):
                    g["capture_file"] = cap
                if nf is not None and g.get("noise_floor_db") is None:
                    g["noise_floor_db"] = nf
                if lvl and not g.get("mod_waveform_level"):
                    g["mod_waveform_level"] = lvl
        all_items = filtered_items

        if not all_items:
            log("No frequencies to transmit (all sources disabled or empty).")
            time.sleep(5)
            continue

        # Build blocks
        blocks = []
        if BLOCK_MATCH_MODULATION:
            clusters = cluster_nearby_matching_mod(all_items)
            blocks = build_blocks_from_clusters(clusters)
            inv_n = sum(1 for b in blocks if b.get("inverse_leveled"))
            log(f"Clustered into {len(blocks)} block(s) by nearby+matching modulation")
            if inv_n and not OPTION3_REALTIME_INVERSE_RX_TX:
                log(f"  {inv_n} block(s) using last-captured modulation at inverse amplitude")
        else:
            # Legacy per-source square blocks
            seen_centers = set()
            for item in all_items:
                c = round(item["mhz"])
                if c not in seen_centers:
                    blocks.append(build_square_block(c, item.get("bandwidth", MANUAL_BANDWIDTH_MHZ),
                                                     item.get("modulation", "unknown"),
                                                     item.get("source", "")))
                    seen_centers.add(c)

        # Apply auto-levels to in-band blocks
        floors = load_noise_floors()
        apply_auto_levels(blocks, floors)

        # Build hop list
        hop_list = build_block_hop_list(blocks)

        # Add exact hops for manual frequencies
        exact_hops = []
        if LOCK_ON_TARGETS and USE_MANUAL:
            for ent in _parse_manual_entries():
                dev_serial = HACKRF_SERIALS[0] if HACKRF_SERIALS else None
                if num_hackrfs >= 2 and ENABLE_MULTI_HACKRF_TX:
                    if 400 <= ent["mhz"] < 2000:
                        dev_serial = HACKRF_SERIALS[0]
                    elif 2000 <= ent["mhz"] <= 6000 and num_hackrfs >= 2:
                        dev_serial = HACKRF_SERIALS[1]
                exact_hops.append(build_exact_hop(ent["mhz"], "manual", dev_serial))

        # Assign devices to hops based on selected option
        all_hops = hop_list + exact_hops
        if num_hackrfs > 0:
            all_hops = assign_devices_to_hops(all_hops, num_hackrfs, HACKRF_SERIALS)

        # Write active blocks for inspection
        write_active_blocks(blocks, exact_hops)

        # --- Transmit Loop ---
        if all_hops:
            sweep_count += 1
            log(f"Sweep #{sweep_count}: {len(all_hops)} hops across {len(blocks)} blocks")
            if ENABLE_MULTI_HACKRF_TX and num_hackrfs >= 2:
                if OPTION3_REALTIME_INVERSE_RX_TX:
                    log(f"  Multi-HackRF: Option 3 real-time inverse (silent-RX → last captured inverse)")
                elif OPTION2_PHASE_SHIFT_ASSIGNMENT and APPLY_180_PHASE_SHIFT:
                    log(f"  Multi-HackRF: Option 2 180° phase inversion ({PHASE_SHIFT_MIN_MHZ:.0f}-{PHASE_SHIFT_MAX_MHZ:.0f} MHz)")
                elif OPTION1_FIXED_RANGE_SPLIT:
                    log(f"  Multi-HackRF: Option 1 fixed range split")
                else:
                    log(f"  Multi-HackRF: round-robin across {num_hackrfs} devices")
                inv_blocks = sum(1 for b in blocks if b.get("inverted") or b.get("inverse_leveled"))
                if inv_blocks:
                    log(f"  Inverse modulation: {inv_blocks} block(s) at inverse amplitude of last capture")

            # Check for Option 3 real-time inverse RX/TX mode
            if OPTION3_REALTIME_INVERSE_RX_TX and num_hackrfs >= 2 and ENABLE_MULTI_HACKRF_TX:
                # Real-time inverse modulation
                option3_real_time_inverse_rx_tx(all_hops, num_hackrfs)
            else:
                # Normal TX mode (parallel for Option 2 phase-shift pairs and Option 1 split)
                use_parallel = (OPTION2_PHASE_SHIFT_ASSIGNMENT and num_hackrfs >= 2
                                and ENABLE_MULTI_HACKRF_TX and APPLY_180_PHASE_SHIFT)
                use_opt1_parallel = (OPTION1_FIXED_RANGE_SPLIT and num_hackrfs >= 2
                                     and ENABLE_MULTI_HACKRF_TX)

                i = 0
                while i < len(all_hops) and running:
                    hop = all_hops[i]

                    # Apply jitter
                    dwell = DWELL_SEC
                    if TX_JITTER_SEC > 0:
                        dwell += random.uniform(-TX_JITTER_SEC, TX_JITTER_SEC)
                        dwell = max(0.001, dwell)

                    # Option 2: phase-shift pair at the same frequency
                    is_pair = (use_parallel and i + 1 < len(all_hops)
                               and hop.get("apply_phase_shift") is False
                               and all_hops[i + 1].get("apply_phase_shift") is True
                               and hop["hz"] == all_hops[i + 1]["hz"])
                    # Option 1: next hop is on the other device — transmit together
                    if (not is_pair and use_opt1_parallel and i + 1 < len(all_hops)
                            and hop.get("device_serial") and all_hops[i + 1].get("device_serial")
                            and hop.get("device_serial") != all_hops[i + 1].get("device_serial")):
                        is_pair = True
                        if i == 0 or (i % 20 == 0):
                            log(f"  [Option1] simultaneous TX  "
                                f"{hop.get('mhz')} MHz on {hop.get('device_serial')}  +  "
                                f"{all_hops[i+1].get('mhz')} MHz on {all_hops[i+1].get('device_serial')}")

                    if is_pair:
                        hop2 = all_hops[i + 1]
                        if i == 0 or (i % 20 == 0):
                            log(f"  [Option2] phase-inversion pair @ {hop['mhz']:.3f} MHz  "
                                f"normal={hop.get('device_serial','?')}  "
                                f"shifted={hop2.get('device_serial','?')}")
                        results = [None, None]

                        def _tx(hop_dict, idx, dwell_val):
                            results[idx] = transmit_on_frequency(
                                hop_dict["hz"],
                                tone_hz=hop_dict.get("tone_hz"),
                                dwell_sec=dwell_val,
                                wave_type=hop_dict.get("wave_type"),
                                amplitude=hop_dict.get("amplitude", TX_AMPLITUDE),
                                capture_file=hop_dict.get("capture_file"),
                                device_serial=hop_dict.get("device_serial")
                            )

                        t1 = threading.Thread(target=_tx, args=(hop, 0, dwell))
                        t2 = threading.Thread(target=_tx, args=(hop2, 1, dwell))
                        t1.start()
                        t2.start()
                        t1.join()
                        t2.join()

                        # Both must succeed for a clean result; otherwise count as failure
                        success = results[0] and results[1]
                        i += 2
                    else:
                        success = transmit_on_frequency(
                            hop["hz"],
                            tone_hz=hop.get("tone_hz"),
                            dwell_sec=dwell,
                            wave_type=hop.get("wave_type"),
                            amplitude=hop.get("amplitude", TX_AMPLITUDE),
                            capture_file=hop.get("capture_file"),
                            device_serial=hop.get("device_serial")
                        )
                        i += 1

                    if not success:
                        fail_streak += 1
                        if fail_streak >= FAIL_BURSTS_BEFORE_RESTART:
                            log(f"Too many consecutive failures ({fail_streak}). Restarting process...")
                            restart_process("too many device failures")
                        elif fail_streak >= FAIL_BURSTS_BEFORE_RESET:
                            log(f"Device failing ({fail_streak} failures). Resetting HackRF...")
                            close_persistent_hackrf()
                            time.sleep(1.0)
                            init_persistent_hackrf()
                            # Do not reset fail_streak so it can accumulate to restart threshold
                    else:
                        fail_streak = 0

                    if TX_SLEEP_SEC > 0:
                        time.sleep(TX_SLEEP_SEC)

                    # Progress every 50 hops so a long sweep is visible
                    if (i % 50) == 0 and i > 0:
                        log(f"  hop {i}/{len(all_hops)}  last={hop.get('mhz', 0):.3f} MHz")

            # Print sweep summary
            if PRINT_SWEEP_SUMMARY and sweep_count % 10 == 0:
                print(f"\n--- Sweep Summary #{sweep_count} ---")
                print(f"Blocks: {len(blocks)} | Hops: {len(all_hops)}")
                print(f"TX devices: {num_hackrfs}")
                print(f"Last update: {datetime.fromtimestamp(last_update_time).strftime('%H:%M:%S')}")
                print("---")
        else:
            log("No hops to transmit. Waiting for scan data...")
            time.sleep(5)

    # Cleanup
    log("Shutting down...")
    close_persistent_hackrf()
    log("Goodbye.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[+] Interrupted by user.")
    except Exception as e:
        log(f"Fatal error: {e}", also_print=True)
        import traceback
        traceback.print_exc()
        try:
            close_persistent_hackrf()
        except Exception:
            pass
        sys.exit(1)
