# Pre-freeze `.pkg` tests — the re-signed 1.2.1 package

Run 2026-10-09 on the M1 Pro, macOS 27.2, by the Mac contributor, after round 1
(`../2026-10-09_mac-rehearsal-1.3.0_cleanroom/`). Version 1.2.1, so **not release evidence**.

**What was tested.** `just release-macos` re-signed round 1's staged tree (built from `49b4e95`)
and packaged it with the current installer scripts. Since round 1, the only shipped change is the
two-line log in `core-preinstall.sh`, so these tests cover that and the checks round 1 left open
(E4's unsigned half, and the installer screens as seen by a person). L3/L4 were not re-run: same
binaries, and only the frozen build counts.

| File | sha256 | Notarization |
|---|---|---|
| `knaif-1.2.1-macos-arm64.zip` | `b13eb318e0ea0cf5bc7fb419489eef10fbe54854a61f30791a4f208e674c5cf8` | Accepted, submission `32a44da0-…` |
| `knaif-1.2.1-macos-arm64.pkg` | `5aa3a67feaeb634d8721bb509fe76ea919e4e212e269114146a4b9543480ce27` | Accepted, submission `57264b2c-…`; stapled |

Every test ran in a fresh clone of the macOS 12.6 base VM (`installers/macos/README.md`).

## Results

| # | Test | Result |
|---|---|---|
| 1 | The preinstall's new log line: reinstall over a running daemon with nobody at the console | **Pass.** `./preinstall: knaif: not stopping a model daemon: nobody is logged in to ask; one left running exits on its own when idle`. The daemon kept running, by design; `daemon stop` and `uninstall.sh` then left nothing (`test1-preinstall-log/`) |
| 2 | `clean-room.sh --pkg --upgrade-from` the 2026-10-06 `.pkg` | **CLEAN ROOM PASS** (13 checks); a real upgrade (`Installing`, then `Upgrading at base path /`). The upgrade itself logged test 1's line too (`test2-upgrade/`) |
| 3 | The installer screens, by a person (F5, E6) | **Pass.** Options page shown, with the skills, the PATH link and the model ticked and the Homebrew tool group unticked (no Homebrew in the VM; the group was not expanded, so each tool's disabled state was not looked at); install completed; `knaif --version` and `knaif skills list` through the PATH link; `uninstall.sh` removed the link, the install and the receipts and kept `~/.knaif`. The model download failed (below) and setup carried on, as D13 requires (`test3-installer/`) |
| 4 | E4's two halves: an ad-hoc signed build (what `cargo` produces) vs the signed one, both quarantined as Safari leaves them | **Pass.** See the table below (`test4-gatekeeper/`) |
| 5 | Does Installer stop a package script at `pkgbuild`'s default `timeout="600"`? A throwaway package whose postinstall stays alive 12 minutes, logging every 30 s, installed with `sudo installer` | **Not enforced.** The script logged `alive at 602s` … `692s` and `finished after 722s`; `The install was successful.`, exit 0; nothing in `install.log` about a timeout or a kill (`test5-script-timeout/`). macOS 12.6 through `installer`, which uses the same `installd`/`package_script_service` as Installer.app; a newer macOS could enforce the attribute |

**E4.** The "unsigned" artifacts were made from a copy of the staged tree: every Mach-O re-signed ad
hoc (`codesign --force -s -`; Apple Silicon runs no code without a signature), zipped as
`release.sh` does, and packaged by `build-pkg.sh` without `--sign`.

| | Ad-hoc signed ("unsigned") | Developer ID, notarized |
|---|---|---|
| Quarantined launch, a user logged in | **Blocked**: *"“knaif” cannot be opened because the developer cannot be verified … Safari downloaded this file today"*; after Cancel, `zsh: killed` | runs (round 1, fresh account on the physical Mac) |
| Quarantined launch, nobody logged in | **Killed**, exit 137; `syspolicyd`: *Denying, eval requires a prompt but there is no logged in user* | runs, exit 0; `syspolicyd` scans every Mach-O and accepts it |
| `spctl --assess -t install` on the `.pkg` | **rejected**, `source=no usable signature` | **accepted**, `source=Notarized Developer ID` |

`spctl --assess -t execute` rejects the signed bare binary as "does not seem to be an app"; that is
the app-bundle check and expected for a CLI (`release.sh` says so).

## Findings

1. **The model choice did not say setup would sit silent.** In the options list its title was cut
   off at *"Download the knaif AI model now — k…"*, hiding "~2.5 GB; setup will wait", and Installer
   shows only *"Running package scripts…"* with no progress while the postinstall downloads; a
   package script cannot change that text. The person testing took it for a hang. The conclusion
   page said "if you skipped the model download", though here it was chosen and failed.
   **Addressed in `release/1.3.0` by blackdeep-tech/knaif#85:** the model download and the tool
   installs now run in one Terminal window that shows their progress, and setup does not wait.
2. **A dropped connection fails the whole model download.** The download stopped after ~10
   minutes at ~93%: `read range 2315255808-2332033023 … response body closed before all bytes were
   read`. `knaif-models`' fetcher retried a chunk only on HTTP 429/503 (`fetcher.rs` `get`), so one
   broken connection among ~150 16 MB chunks aborted the pull. Progress was kept (`.part` +
   `.resume`), so `knaif models pull` resumed the missing chunks. **Fixed in `release/1.3.0` by
   #85** (transport failures retried, a cut-off chunk re-fetched, a 60 s stall fails and retries).
   Test 5 shows Installer's 600 s script timeout was not enforced here, so it did not end this
   download: the run lasted 9 min 38 s and failed on the connection.
3. **Time Machine exclusion waits for a finished download.** After the failed download,
   `tmutil isexcluded ~/.knaif/models` read `[Included]`, with 2.3 GB of partial model in it.
   Minor: the exclusion lands when a later `models pull` completes.
