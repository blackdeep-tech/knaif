# knaif 1.3.0 macOS round 2, step 10: the clean room on the frozen CI files

Plan §0 *Round 2, at the freeze*, step 6 (step 10 of the Mac's list), approved by the owner with a
4 h budget (relayed by Deyan, 2026-10-12). Started 2026-10-12 04:18:33 (`LOG`); the automatic part
ran until 04:35 (17 min; the last check, `host-signature/`, was written at 04:35:25). The message of
commit `c4e1df0` and the first version of this report said 35 min; that was the documents re-run's
time, copied here by mistake.

**What was measured:** only the CI release files of run `38075857537` on the freeze commit
`8c94cbe`, nothing built or signed on this Mac:

| File | sha256 |
|---|---|
| `knaif-1.3.0-macos-arm64.zip` | `bb042cc45c094503d7ef2b89df0dce9efb3bdc87e67b74b94a201fe882f8590e` |
| `knaif-1.3.0-macos-arm64.pkg` | `e9a317684a69822aec6f39830cd698abcb727317a9bd5760198196ec82aa6470` |
| upgrade source: round 1's signed, notarized 1.2.1 `.pkg` (2026-10-09) | `5aa3a67feaeb634d8721bb509fe76ea919e4e212e269114146a4b9543480ce27` |

`clean-room.sh`, `smoke.sh` and `Cargo.toml` are the freeze tree's. The VM is round 1's base, built
from Apple's `UniversalMac_12.6_21G115_Restore.ipsw` (macOS 12.6, no Command Line Tools, no Xcode,
no Homebrew); every run is a fresh `tart clone` of it. Its automatic updates are off
(`vm-info.txt`: `AutomaticCheckEnabled`, `AutomaticDownload`, `AutomaticallyInstallMacOSUpdates`,
`CriticalUpdateInstall` all 0).

## The three runs

| Run | Command | Result |
|---|---|---|
| 1 | `--zip`, network up, over ssh | **CLEAN ROOM PASS** (8 checks) |
| 2 | `--pkg --offline`, the Ethernet service off for the whole run | **CLEAN ROOM PASS** (13 checks), `room_offline` included; the stapled `.pkg` accepted with no network (`source=Notarized Developer ID`) |
| 3 | `--pkg --upgrade-from knaif-1.2.1-macos-arm64.pkg`, over ssh | **CLEAN ROOM PASS** (13 checks); a real version upgrade: `install-old.log` "Package name is knaif 1.2.1 … The install was successful.", `install.log` "Package name is knaif 1.3.0 … The upgrade was successful."; `upgrade_receipt` passes |

In every run: the quarantine reached every extracted file (run 1), the quarantined `knaif` launched
with no Gatekeeper block and no dylib failure, `smoke.sh` passed, and the CPU tree wrote
`sample-rotated.pdf`. **Metal inside the VM (INFO, never gated, D16):** 37/37 layers offloaded to
the paravirtualized GPU, then exit 1, as in round 1.

**Run 2's method differs from the README.** Instead of typing in the VM's own Terminal, a detached
job in the VM (`run2-pkg-offline/offline.sh`) turned the Ethernet service off, ran `clean-room.sh
--offline`, and turned it back on; `offline-net.txt` shows `*Ethernet` (disabled) and no default
route during the run. Nobody was logged in, so the model choice logged "skipping the model download:
nobody is logged in" rather than a failed download.

## The daemon, headless (run 3's VM)

`run3-pkg-upgrade/daemon-check-headless.log`, nobody logged in (`console=root`), `knaif` called
through the PATH link: install, `daemon start` (running, pid 776), reinstall: the daemon is left
running and `install.log` says `./preinstall: knaif: not stopping a model daemon: nobody is logged
in to ask; one left running exits on its own when idle`, as designed. `sudo uninstall.sh`: `knaif
daemon stopped.`, then **DAEMON-GONE**; `~/.knaif` kept. The console-user half (the preinstall
stopping it) is in *Manual* below.

## The options page, as Installer evaluates it (`pkg-choices/`)

`installer -showChoicesXML` on the CI `.pkg`, in a fresh base clone (no Homebrew) and in a clone of
the round 1 Homebrew VM (Homebrew 7.0.9):

| Check (the pre-freeze fixes) | Without Homebrew | With Homebrew |
|---|---|---|
| the model's line above Supporting tools | yes | yes |
| its title in full: "AI model — in a Terminal window" | yes | yes |
| sizes: model 2,438,751 KB (the manifest's `size_bytes` 2,497,280,960 rounded up), FFmpeg 61,440, Ghostscript 133,120, LibreOffice 819,200, Tesseract 40,960 | yes | yes |
| the Supporting tools group | **not enabled**, not selected | enabled |
| each tool | **not enabled**, not selected | enabled; FFmpeg selected, the rest not |

The hidden `finish` package is last in the choices-outline (`Distribution`), so it installs last.

## Signatures on the host (`host-signature/`)

`.pkg`: Developer ID Installer (8YJ4KKV9SJ), notarized, `stapler validate` worked, `spctl -t
install` accepted. `.zip`: every Mach-O (`knaif`, 9 libraries) passes `codesign --verify --strict`;
`knaif` is Developer ID Application with the hardened runtime and a secure timestamp.
(`spctl -t exec` on a bare command-line tool says "not an app"; the gate is the quarantined launch
in the room, which passed.)

## Manual, with a person at the screen (2026-10-12/13)

Done by Deyan on the same CI files, every VM a fresh clone. All four pass; no failure and no finding.
Screenshots were judged by a person and are kept outside the repo (they show this Mac's desktop);
the logs below were collected over ssh from the VM while the person was logged in.

**A. The `.pkg` in Installer.app, no Homebrew** (`step-a-pkg-no-brew/check.log`). A fresh clone of
the base, `admin` logged in. Customize: the model's line above Supporting tools, its title in full
("AI model — in a Terminal window"), sizes real (core 37.5 MB, model 2.5 GB), Space Required
2.53 GB; the Supporting tools group greyed out and unticked (its 1.08 GB not counted). Install: one
Terminal window opened while Installer already showed "The installation was completed
successfully", downloaded only the model with progress, and ended "Done. Open a new Terminal
window and try: knaif skills list". The conclusion page reads correctly. Afterwards: the model's
sha256 `a9c26005…` matches the manifest, `tmutil` reads `[Excluded]`, `install.log` queued only the
model step and then "opening a Terminal window to finish setup for admin", and the step queue was
cleared. *Observation, not a finding:* the window printed "Downloading knaif-qwen3-4b-v2" two or
three times before "Installed"; whether that was a retry or the progress line redrawn, the
download completed with the right sha256.

**B. The daemon with a console user** (`step-b-daemon-console/daemon-check-console.log`), in A's VM
with `admin` logged in (`console=admin`): `daemon start` (pid 866), reinstall: **DAEMON-GONE**, and
`install.log` reads `./preinstall: knaif daemon stopped.`; `daemon start` again (pid 908),
`sudo uninstall.sh`: `knaif daemon stopped.`, **DAEMON-GONE**, `~/.knaif` kept.

**C. The `.pkg` with Homebrew** (`step-c-pkg-brew/`). A fresh clone of round 1's Homebrew VM
(Homebrew 7.0.9). That base still carried the pre-freeze pass's knaif 1.2.1, its model and the
LibreOffice cask, so the clone was first reset to a clean Homebrew Mac (`prep.log`: `uninstall.sh
--purge`, `brew uninstall --cask libreoffice`); the base itself is unchanged. Customize: the tools
enabled, FFmpeg ticked by default; ticking LibreOffice took the group from 62.9 MB to 901.8 MB and
Space Required from 2.6 GB to 3.44 GB. Install: one Terminal window, opened while Installer showed
success; **the model first**, then FFmpeg, then LibreOffice (the options-page order; `install.log`
queued them in that order). FFmpeg asked `[y/n]` (a Tier 3 source build on macOS 12) and was
answered `n`: only the tools waited on it, the model had already finished, and LibreOffice then
installed. The window ended "Not everything finished. Run these again any time: brew install
ffmpeg", the only failed step. Afterwards (`check.log`): the model's sha256 matches, `[Excluded]`;
FFmpeg not installed, LibreOffice installed, and `knaif skills deps` names `brew install ffmpeg`.
*Observations, not findings:* the tools' titles are cut off in the list ("FFmpeg — required for
the ffmpeg…", "Supporting tools (installed with Home…"), as the model's was before its fix (the
full text shows when a row is selected); and the LibreOffice cask installs on macOS 12 despite
Homebrew's Tier 3 warning, while FFmpeg needs a source build (pre-freeze finding 4).

**D. Metal from a fresh account on the physical Mac** (`step-d-metal-fresh-account/summary.txt`). A
new standard account (not round 1's, whose shader cache was already warm), the CI `.zip` given
Safari's quarantine and extracted by Finder; the extracted `knaif` carries the quarantine. Two runs
of `knaif run documents --yes --verbose --model <4B v2> "rotate sample.pdf 90 degrees"`:

| Run | Wall time | Metal | Exit |
|---|---|---|---|
| cold (first launch in this account) | **18.89 s** | `MTL0 (Apple M1 Pro)`, **offloaded 37/37 layers** | 0 |
| warm | **3.46 s** | the same | 0 |

No Gatekeeper block: the quarantined binary ran unattended, from a script, both times. Round 1
measured 18.93 s and 3.40 s on its build; D3's first-run cost is unchanged on the frozen files.

**Not done:** the optional macOS 13+ question (does `brew install ffmpeg` pour a bottle?), which needs
a 13+ VM; the owner's list no longer asks for it.
