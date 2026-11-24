"""Spectrum power monitor (pyhackrf2 example).

Reads IQ blocks from a HackRF with pyhackrf2 and prints the signal power and
the strongest FFT bin. With SIMULATE = True it runs without hardware, which is
handy for trying out RF Script Studio.

Every top-level variable below appears as an option in RF Script Studio.
"""

import sys
import time

import numpy as np

# ============== Radio ==============
SIMULATE = True            # Generate fake samples instead of using a HackRF
CENTER_FREQ = 433.92e6     # @unit Hz  Centre frequency
SAMPLE_RATE = 8e6          # @unit Hz @options 2e6|4e6|8e6|10e6|16e6|20e6
LNA_GAIN = 16              # @range 0..40 @step 8  IF (LNA) gain in dB
VGA_GAIN = 20              # @range 0..62 @step 2  Baseband (VGA) gain in dB
ENABLE_AMP = False         # 14 dB RF amplifier - careful with strong signals
BIAS_TEE = False           # Power an active antenna through the antenna port

# ============== Processing ==============
BLOCK_SIZE = 262144        # Samples per measurement
FFT_SIZE = 1024
AVERAGE_BLOCKS = 4         # Number of blocks averaged per report
ALERT_THRESHOLD_DB = -20.0  # Print an alert above this power level
REPORT_INTERVAL = 1.0      # Seconds between reports

# ============== Output ==============
VERBOSE = True             # Print every measurement, not just alerts
SHOW_PEAK = True           # Report the strongest frequency offset
LOG_TO_CSV = False
CSV_PATH = "power_log.csv"
RUN_FOREVER = True         # Keep measuring until stopped
MAX_REPORTS = 60           # Used when RUN_FOREVER is False


def open_radio():
    from pyhackrf2 import HackRF

    sdr = HackRF()
    sdr.sample_rate = SAMPLE_RATE
    sdr.center_freq = CENTER_FREQ
    sdr.lna_gain = LNA_GAIN
    sdr.vga_gain = VGA_GAIN
    sdr.amplifier_on = ENABLE_AMP
    sdr.bias_tee_on = BIAS_TEE
    print(f"HackRF opened: serial {sdr.get_serial_no()}")
    return sdr


def fake_samples(n):
    t = np.arange(n) / SAMPLE_RATE
    tone = 0.05 * np.exp(2j * np.pi * 250e3 * t)
    noise = (np.random.randn(n) + 1j * np.random.randn(n)) * 0.01
    return (tone + noise).astype(np.complex64)


def measure(samples):
    power_db = 10 * np.log10(np.mean(np.abs(samples) ** 2) + 1e-12)
    usable = samples[: len(samples) // FFT_SIZE * FFT_SIZE].reshape(-1, FFT_SIZE)
    spectrum = np.mean(np.abs(np.fft.fftshift(np.fft.fft(usable, axis=1), axes=1)) ** 2, axis=0)
    peak_bin = int(np.argmax(spectrum))
    peak_offset = (peak_bin - FFT_SIZE // 2) * SAMPLE_RATE / FFT_SIZE
    return power_db, peak_offset


def main():
    sdr = None if SIMULATE else open_radio()
    mode = "simulation" if SIMULATE else "hardware"
    print(f"Monitoring {CENTER_FREQ / 1e6:.3f} MHz @ {SAMPLE_RATE / 1e6:.1f} Msps ({mode})")
    csv = open(CSV_PATH, "a", encoding="utf-8") if LOG_TO_CSV else None
    reports = 0
    try:
        while RUN_FOREVER or reports < MAX_REPORTS:
            powers, peaks = [], []
            for _ in range(AVERAGE_BLOCKS):
                block = fake_samples(BLOCK_SIZE) if SIMULATE else sdr.read_samples(BLOCK_SIZE)
                p, f = measure(block)
                powers.append(p)
                peaks.append(f)
            power = float(np.mean(powers))
            peak = float(np.median(peaks))
            reports += 1
            line = f"power {power:6.1f} dB"
            if SHOW_PEAK:
                line += f"   peak {(CENTER_FREQ + peak) / 1e6:.4f} MHz"
            if power > ALERT_THRESHOLD_DB:
                print(f"ALERT  {line}", file=sys.stderr)
            elif VERBOSE:
                print(line)
            if csv:
                csv.write(f"{time.time():.1f},{power:.2f},{CENTER_FREQ + peak:.0f}\n")
                csv.flush()
            time.sleep(REPORT_INTERVAL)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        if csv:
            csv.close()
        if sdr is not None:
            sdr.close()


if __name__ == "__main__":
    main()
