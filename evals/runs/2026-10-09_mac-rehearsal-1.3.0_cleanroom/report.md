# Round 1 rehearsal — signed build, clean room, daemon, Metal from a fresh account

Run 2026-10-09 on the M1 Pro, macOS 27.2, by the Mac contributor: *Next round for the Mac*, round 1,
of the macOS support plan (`docs/plans/2026-08-02-macos-support.md` §0). Version 1.2.1, so **nothing
here is release evidence**; it rehearses round 2 on the frozen 1.3.0.

## The build (round 1, steps 1–2)

From `feat/macos-support` at `49b4e95`, in a checkout under `/Users/Shared` (outside `~`, E5).

| Check | Result |
|---|---|
| `just check-native` | clean |
| `just test-native` | 563 passed, 0 failed (22 suites) |
| installer tests under `/bin/bash` 3.2 (`test_installer_pkg`, `test_macos_release`, `test_macos_signing`, `test_macho_deps`) | 97 passed |
| `just package-native metal` | 9 Mach-Os resolve, arm64-only, floor 12.0; `check_no_local_paths` 65 files clean; self-contained |
| `just release-macos` | home-path check passed; both notarizations **Accepted**, no issues, all 10 CDHashes in each ticket; `.pkg` stapled and validated; `spctl` accepts it (`source=Notarized Developer ID`) |

| File | sha256 | Submission |
|---|---|---|
| `knaif-1.2.1-macos-arm64.zip` | `0e435cc0f54ce3a08f349f04d8853036412e85956b80e6aaebd6d1b6e29864ca` | `d73994cb-d6bd-436a-a918-3fbb6e52b84d` |
| `knaif-1.2.1-macos-arm64.pkg` | `ece592c65f1fad00275a915bdcdb931a76790d9e86a5dbf9c29dc25fa468425c` | `7f51076b-cc1e-438a-bee5-33c79b09e51b` |
| staged `bin/knaif` | `ee058d0a…` | |
| `old-1.2.1.pkg` (the 2026-10-06 signed build, run 3's starting point) | `ffcd7093…` | |

`notary/` is `dist/notary/` unchanged. No entitlements were passed (F4).

## The clean room (step 3)

**The VM is not Cirrus Labs' `macos-monterey-vanilla`.** That image ships the Command Line Tools
(`/Library/Developer/CommandLineTools`, 113 tools, six `CLTools_*` receipts), so `room_no_clt`
fails on it. The base was built from Apple's own restore image instead:
`UniversalMac_12.6_21G115_Restore.ipsw` from `updates.cdn-apple.com` (sha256 `5113f8d3…0658e5`,
matching api.ipsw.me), `tart create --from-ipsw`, tart 2.32.1. Setup Assistant by hand: account
`admin`, nothing installed, Remote Login on, display and system sleep off, passwordless `sudo` (the
room's `installer` needs it). Checked before use: macOS 12.6 (21G115), `xcode-select -p` fails, no
CLT folder, no Xcode, no Homebrew. Every run is a fresh `tart clone` of that base.
`installers/macos/README.md` now says so.

| Run | Command | Result |
|---|---|---|
| 1 | `--zip`, network up, over ssh | **CLEAN ROOM PASS** (8 checks) |
| 2 | `--pkg --offline`, in the VM's own Terminal, Ethernet service inactive, `admin` logged in | **13 PASS, 0 FAIL** (`room_offline` and the stapled `pkg_gatekeeper` included) |
| 3 | `--pkg --upgrade-from old-1.2.1.pkg`, over ssh | **CLEAN ROOM PASS** (13 checks); a real upgrade: `install-old.log` "The install was successful.", then "The upgrade was successful." |

In every run: the quarantine propagated to every extracted file (run 1), the quarantined `knaif`
launched with no Gatekeeper block and no dylib failure under the hardened runtime with **no
entitlements** (F4), `smoke.sh` passed, and the CPU tree wrote `sample-rotated.pdf`.
Run 1 first failed `smoke` — a fault in the room, not the artifact (Findings 2); the table is the
re-run on a fresh clone with the fix.

**Metal inside the VM (INFO, never gated, D16):** all three runs offload 37/37 layers to the
paravirtualized GPU and then exit 1, `Error: Unknown Token Type` — the guest GPU produces output the
plan parser cannot read. Says nothing about a real Mac (step 5 is that proof).

**The model choice (D13):** with nobody logged in (runs 1, 3) it is skipped and logged; offline
(run 2) `could not download knaif-qwen3-4b-v2; knaif is installed anyway`, install successful; online
with `admin` logged in (step 4) it downloaded the model in about 5 minutes, and `tmutil isexcluded
~/.knaif/models` reads `[Excluded]` on a VM where nothing else could have set it (F5b, D18).

## The daemon (step 4)

`daemon-check-console.log`, in run 3's VM with `admin` logged in at the console: the plan's
sequence (install → `daemon start` → reinstall → start → `uninstall.sh`).

- reinstall over a running daemon: **DAEMON-GONE**; `install.log`: `./preinstall: knaif daemon
  stopped.`, no "could not stop"
- uninstall over a running daemon: **DAEMON-GONE**; `~/.knaif` kept

`daemon-check-headless.log` is the first attempt, with the VM headless and nobody at the console:
the reinstall left the daemon running (pid 642), because the preinstall asks the console user and
there was none. That is the designed behaviour — the daemon exits after 10 idle minutes — but the
preinstall logged nothing about it (Findings 3).

## Metal on the physical Mac, from a fresh user account (step 5)

A new standard user; the signed `.zip` given Safari's quarantine and extracted by Finder (the
extracted `bin/knaif` carries `0083;…;Safari;…`); two runs of `knaif run documents --yes --verbose
--model <4B v2> "rotate sample.pdf 90 degrees"`. `summary.txt` is the log's summary lines.

| Run | Wall time | Metal |
|---|---|---|
| cold (first ever in this account) | **18.93 s** | `MTL0 (Apple M1 Pro)`, **offloaded 37/37 layers**, exit 0 |
| warm | **3.40 s** | the same, exit 0 |

No Gatekeeper block. The cold run's ~15.5 s extra is D3's first-run tax, measured the way D3 asks (a
fresh user account). It bundles Metal compiling the embedded shader source for this account with
the first quarantined launch's online notarization check; the 2.4 GB model was most likely already
in the file cache, so disk reads are not in it.

## Findings

1. **Cirrus' macOS 12 vanilla image carries the Command Line Tools** and cannot be the clean room.
   Fixed in the README (the IPSW base above).
2. **`clean-room.sh` could never pass `smoke`.** `smoke.sh` reads the release version from
   `../Cargo.toml`, its place in a checkout; in the room it is beside the script, so `grep` failed.
   The tests fake `smoke.sh` (`exit 0`), so they never saw it. Fixed in macOS files only:
   `clean-room.sh` gives `smoke.sh` a checkout layout from a `Cargo.toml` copied in beside it, and
   the README's `scp` copies it. The real `smoke.sh` on run 1's quarantined tree in that layout: all
   seven checks ok.
3. **The preinstall's comment says "nobody logged in is logged"; the code logged nothing.** Fixed:
   `core-preinstall.sh` now logs that it is not stopping a daemon because nobody is logged in.
   Not in the signed `.pkg` above, which predates it.

Not done this round: E4's *before signing, expect a block* half — only the signed files were run.
