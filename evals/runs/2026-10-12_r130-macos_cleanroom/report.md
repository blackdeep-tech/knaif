# knaif 1.3.0 macOS round 2, step 10: the clean room on the frozen CI files

Plan §0 *Round 2, at the freeze*, step 6 (step 10 of the Mac's list), approved by the owner with a
4 h budget (relayed by Deyan, 2026-10-12). Started 2026-10-12 04:18; the automatic part took 35 min.

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

## Manual, not done yet

- **The `.pkg` in Installer.app** with a user logged in: the options page as it looks (title not
  cut off, sizes, Space Required counting them; greyed tools without Homebrew); the one Terminal
  window opening last, the model first, then the tools in options-page order; a brew `[y/n]` or a
  refused tool holding up only the tools, the summary naming the right commands.
- **The daemon with a console user** (run 3's case, logged in at the VM window): the reinstall's
  preinstall stops it.
- **Metal from a fresh user account on the physical Mac** (D3's cold and warm times).
- Optional: on a macOS 13+ VM, does `brew install ffmpeg` pour a bottle?
