# RF Script Studio

A desktop control centre for HackRF Python scripts, for **Linux and Windows**.

Drop a Python script into `scripts/`. The app reads its variables and builds a settings panel from them (toggles for `True`/`False`, number fields, dropdowns). It runs the script with your values, keeps it alive in the background, and restarts it if it stops or hangs.

![Control page](docs/screenshots/control.png)

## Features

- **Options built from your script.** Top-level variables such as `ENABLE_TX = False` or `CENTER_FREQ = 433.92e6` become controls, grouped by the comment headers in your file. Scripts without headers are grouped by meaning:
  - Groups: Radio, Transmit, Receive, Detection, Timing, Files & storage, Logging & display, Network, Advanced.
  - On/off switches sit together in a grid at the top of each group, and settings that make the HackRF transmit are marked **TX** in red.
  - Frequencies display as `433.92 MHz`, and gains get the HackRF's real limits.
  - Values are saved per script.
- **Command-line flags become buttons.** Flags your script defines with `argparse` or checks in `sys.argv` appear on the **Commands** page:
  - Report flags (`--history`, `--top N`, `--pairs`, ...) run once, and their output is shown as sortable tables you can copy or save as CSV.
  - Long-running flags (`--scan`) become the **Start mode**, supervised and restarted like any run.
- **Touch screens:** drag anywhere on a page to scroll it, with a finger or the mouse (Settings → Appearance).
- **Both HackRF interfaces:**
  - **pyhackrf2** is bundled. On Windows a small shim loads `hackrf.dll`, because pyhackrf2 only looks for `libhackrf.so.0`.
  - **hackrf_transfer** has its own page for recording and transmitting IQ files, and the HackRF tools are on `PATH` for your script.
- **Keeps the script running:**
  - It can restart always, only on failure, or never.
  - The retry delay grows after each quick failure, and it gives up if the script keeps crashing.
  - Hang detection restarts a script that prints nothing for a set time.
  - Stopping sends Ctrl+C first, so scripts can close the radio cleanly.
  - If the app is killed, the script is stopped too and won't keep holding the HackRF.
- **Runs in the background:**
  - It can live in the system tray, start at login, and send tray notifications when the script crashes.
  - An optional **background service** (systemd user unit on Linux, Task Scheduler on Windows) runs the script with no window, and the OS restarts that service if it ever exits.
- **Installs drivers and dependencies:**
  - **Linux:** the `.deb` pulls in `hackrf` and `libhackrf0`. An *Install HackRF drivers* button in the app runs the same setup (apt/dnf/pacman/zypper, udev rules, `plugdev` group).
  - **Windows:** the HackRF DLL and tools are bundled, along with Zadig for the WinUSB driver.
- **Live console** with colouring, search, timestamps and export. Output also goes to rotating log files.
- **Result files:** when a script says it wrote a `.json` or `.csv` file (for example `Results written to: frequencies.json`), the app can show the file as tables, with buttons to open it or its folder.
- **Dark and light themes.** Your script's changes are picked up when the file changes on disk.

| Commands & reports | Options grouped automatically |
|---|---|
| ![Commands](docs/screenshots/commands.png) | ![Scanner options](docs/screenshots/control-scanner.png) |

| Device & drivers | hackrf_transfer |
|---|---|
| ![Device](docs/screenshots/device.png) | ![Transfer](docs/screenshots/transfer.png) |

## Adding your script

1. Put your script in **`scripts/`** (e.g. `scripts/my_receiver.py`). Every `.py` file there is bundled into the app and listed in its script picker.
2. **numpy, scipy and pyhackrf2 are included.** If the script needs any other package, list it in **`scripts/requirements.txt`**, one per line, and the next build bundles it.
3. Commit and push. GitHub Actions builds new Linux and Windows apps with your script inside.

Variables are discovered automatically. Comments are optional but improve the panel:

```python
# ============ Radio ============        <- section header (decorated, [Name], or ALL CAPS)
CENTER_FREQ = 433.92e6   # @unit Hz Centre frequency     <- inline comment = description
ENABLE_AMP = False       # 14 dB RF amp                   <- True/False -> toggle switch
LNA_GAIN = 16            # @range 0..40 @step 8           <- slider
MODE = "fm"              # @options fm|am|usb             <- dropdown
API_TOKEN = ""           # @hide                          <- not shown in the panel

# [Logging]
VERBOSE = True
```

| Hint | Effect |
|---|---|
| `@options a\|b\|c` | dropdown with these values |
| `@range 0..40` `@step 8` | limits and step (slider when both are set) |
| `@unit Hz` | unit label; frequencies display as kHz/MHz/GHz |
| `@label "Nice name"` | custom label |
| `@hide` / `@ignore` | keep it out of the panel |
| `@section Name` | put it in a specific section |
| `# @gui-begin` … `# @gui-end` | only read variables between these lines |

Number fields accept `433.92M`, `2.4G`, `8e6` and `2_000_000`. Your script itself is never modified. A patched copy with your values is run each time, and line numbers in tracebacks still match your file.

### Command-line flags

If your script has flags, they're picked up automatically, whether they're defined with argparse or checked by hand:

```python
parser.add_argument("--scan", action="store_true", help="Run the scanner continuously")  # -> run mode
parser.add_argument("--top", type=int, nargs="?", const=10, metavar="N")                 # -> report with a box for N
if "--history" in sys.argv: ...                                                          # -> report
```

Flags whose name or help text sounds long-running (scan, monitor, listen, transmit, continuous, ...) become **run modes**, and the rest become **reports**. Report output is shown as tables when it looks like one:
- columns aligned with spaces, `|` tables or CSV, with the header row detected;
- `Name: value` summaries;
- titles like `=== Top 10 ===`.

`examples/frequency_scanner.py` is a complete example.

To see what the app will detect:

```bash
python -m rfstudio --list-options scripts/my_receiver.py
```

## Download & install

Builds are produced by GitHub Actions. You can download them from the **Actions** tab (artifacts of the latest run) or from **Releases** (when you push a `v*` tag).

### Linux

**Debian / Ubuntu / Mint (recommended):** apt installs HackRF, libhackrf, libusb and the Qt libraries automatically:

```bash
sudo apt install ./rf-script-studio_1.0.0_amd64.deb
```

**Any distro (tarball):**

```bash
tar xzf RFScriptStudio-1.0.0-linux-x86_64.tar.gz
cd RFScriptStudio-1.0.0-linux-x86_64
./install.sh              # asks for sudo once to install hackrf + udev rules
```

Both set up the udev rules and add you to the `plugdev` group. **Log out and back in once**, then re-plug the HackRF. `./install.sh --with-python` also pip-installs `pyhackrf2` for your system Python. That's only needed if you choose a custom interpreter in Settings, because the app already bundles it.

### Windows

Run **`RFScriptStudio-1.0.0-windows-x64-setup.exe`**. A portable `.zip` is also built.

- `hackrf.dll`, `hackrf_transfer.exe`, `hackrf_info.exe` and their DLLs are bundled, and pyhackrf2 is built in. There is nothing to pip-install.
- **USB driver:** HackRF One with stock firmware is normally picked up by Windows' built-in WinUSB driver automatically. If Device Manager shows the board with an error (common with older or PortaPack/Mayhem firmware), use the bundled **Zadig**. You can tick *Install the HackRF USB driver* in the installer, or press **Device → Install / repair USB driver** in the app. Zadig opens with WinUSB preselected: pick the HackRF and click *Install/Replace Driver*. Windows requires this one confirmation click, so it can't be done fully silently.
- The app isn't code-signed, so SmartScreen may ask you to confirm the first run (*More info → Run anyway*).

## Troubleshooting

When a script stops with an error, a banner explains it in plain English with a button for the fix. The full output is in the console and in the log file.

| Message | What to do |
|---|---|
| **No permission to open the HackRF** | Linux: Device → *Install HackRF drivers*, then unplug and re-plug the board (log out/in once if you were just added to `plugdev`). Windows: Device → *Install / repair USB driver* (Zadig → WinUSB). |
| **The HackRF is in use by another program** | Close other SDR software (GQRX, SDR++, `hackrf_transfer`, the background service) and press Start. |
| **No HackRF found** | Plug it in directly (not through a hub) and press Device → *Detect devices*. |
| **The script needs Python package(s) …** | numpy, scipy and pyhackrf2 are already included. Add any other package to `scripts/requirements.txt` and push, so the next build includes it. The app stops instead of restarting over and over, because a restart can't install a package. |
| **The script runs but prints nothing** | After 30 s the app shows where the script is waiting (which function and line), and the 🔍 button in the console shows it again at any time. Send that output when asking for help. |
| **The script isn't allowed to write a file** | Relative paths go to the working folder (below). |

Errors a restart can't fix, such as a syntax error or a missing package, stop the script instead of restarting it over and over.

**Working folder:** a script you open from your own folders runs in its own folder. Scripts bundled with the app run from a copy in `~/RF Script Studio/<script name>/` (`%USERPROFILE%\RF Script Studio\...` on Windows), because the install folder is read-only. Files saved with relative paths or next to the script (`Path(__file__).parent / "backups"`) end up there, and they're kept across updates. The folder button next to the script name opens it.

**Scripts that install their own packages:** inside the app, `sys.executable` behaves like `python`:
- `[sys.executable, "-m", "pip", "install", "numpy", "scipy"]` answers "already satisfied" for bundled packages. Anything not included gets a clear error, because nothing can be installed into a packaged app.
- A script that restarts itself with `os.execv(sys.executable, …)` comes back with the app's option values.

**Child processes (Windows parity):** scripts that start helper programs (`subprocess`, `hackrf_transfer`, other scripts) or worker processes (`multiprocessing`) work the same on Windows as on Linux:
- their output appears in the console;
- workers get the app's option values and can use pyhackrf2;
- Stop reaches them.

**Stopping cleanly:** Stop sends Ctrl+C on Linux and an equivalent stop request on Windows. Your script gets a `KeyboardInterrupt`, so its `finally:` / `except KeyboardInterrupt:` code runs (for example, switching the transmitter off) before it's force-closed.

**pyhackrf2 fixes:** the app patches two bugs in pyhackrf2 1.0.x when your script imports it, without changing the installed package:
- `read_samples()` returned samples with a constant offset and busy-waited at 100% CPU. It now returns IQ scaled to ±1, like `hackrf_transfer` data.
- Reading `lna_gain` crashed with a RecursionError.

**Text size:** Settings → Appearance → *Text size* (Small / Normal / Large / Extra large).

## Running in the background

| Want | How |
|---|---|
| Keep running when the window closes | Settings → *Close to system tray* (on by default) |
| Start with your session | Settings → *Launch at login* (starts minimised to the tray) |
| Start the script automatically | Settings → *Start script when the app opens* |
| Run without any desktop app | Settings → *Background service → Install* |
| Restart a hung script | Settings → *Hang detection* (seconds without output) |
| A one-off task (e.g. a single `--scan`) | Use **Run once** on the Commands page. A script that ends normally is shown as *Finished* and isn't restarted; it's only restarted when it crashes. |

Command line:

```bash
rfscriptstudio --minimized --autorun               # tray only, start the script
rfstudio-cli --headless --script scripts/my.py     # no GUI (what the service runs)
rfstudio-cli --probe                               # check pyhackrf2/libhackrf/tools + list devices
```

On Linux the service is `~/.config/systemd/user/rfscriptstudio.service`. Run `loginctl enable-linger $USER` to keep it running while you're logged out. Logs are in `~/.local/state/rfscriptstudio/logs/` (Linux) or `%LOCALAPPDATA%\RFScriptStudio\logs\` (Windows).

## Building

GitHub Actions (`.github/workflows/build.yml`) runs on every push and pull request:

1. **Tests:** lint, unit tests, a check that your scripts parse, and an offscreen GUI run that produces screenshots.
2. **Linux build** (Ubuntu 22.04 for wide glibc compatibility): PyInstaller → smoke test with real libhackrf → `.tar.gz` and `.deb` → test-install of the `.deb`.
3. **Windows build:** HackRF DLLs and tools from conda-forge, Zadig from the libwdi releases → PyInstaller → smoke test (pyhackrf2 loads `hackrf.dll`) → `.zip` and an Inno Setup installer.
4. **Release:** pushing a tag such as `v1.0.1` sets the app version and attaches all files to a GitHub Release.

```bash
git tag v1.0.0 && git push origin v1.0.0
```

### Locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m rfstudio                                     # run from source
pytest                                                 # tests
pyinstaller packaging/rfstudio.spec --noconfirm        # build dist/RFScriptStudio
bash tools/package_linux.sh 1.0.0                      # Linux: tarball + .deb
```

## Project layout

```
scripts/                your HackRF scripts (bundled into the app)
examples/               example scripts (pyhackrf2 + hackrf_transfer)
src/rfstudio/
  script_model.py       variable/section discovery + patching
  supervisor.py         auto-restart process supervisor
  runner.py             executes scripts; pyhackrf2 cross-platform shim
  hackrf_backend.py     device probe, hackrf_transfer command builder
  drivers.py            driver/dependency checks and installers
  autostart.py          launch at login, systemd / Task Scheduler service
  headless.py           windowless mode
  gui/                  PySide6 desktop app
packaging/              PyInstaller spec, Linux installers, Windows installer + Zadig config
tools/                  build helpers, smoke test, screenshot generator
.github/workflows/      CI builds and releases
```

## A note on transmitting

The app and `hackrf_transfer` can transmit. Only transmit where you're authorised to (for example with an amateur licence or within ISM-band rules), or into a dummy load.

## License

MIT. See [LICENSE](LICENSE). Bundled third-party components keep their own licences: Qt/PySide6 (LGPLv3), libhackrf/HackRF tools (GPLv2), Zadig/libwdi (GPLv3/LGPLv3), numpy (BSD) and pyhackrf2.
