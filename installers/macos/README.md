# `installers/macos/` — macOS packaging, signing and the clean room

The macOS release: an arm64 `.zip` (portable tree) and a `.pkg` (installer with an options page),
both signed with Developer ID and notarized, the `.pkg` stapled, plus a Homebrew formula. The
plan and its decisions are in `docs/plans/2026-08-02-macos-support.md` (D1–D19); this file is how
to run it.

> **Status (2026-09-30).** Everything here was written on the Windows box and tested there and on
> Linux against faked Apple tools (`python/core/tests/test_installer_pkg.py`,
> `test_macos_release.py`, `test_macos_signing.py`). **None of it has run on a Mac yet.** The
> first run of each step is the Mac contributor's, and its result goes under the plan's task.

## What is where

| File | Does |
|---|---|
| `build-pkg.sh` | staged tree → `.pkg` (a Distribution package, D13); unsigned unless `--sign` |
| `pkg/Distribution.xml.in` | the options page: core, skills, PATH link, Homebrew tools, model |
| `pkg/scripts/*.sh` | the packages' install scripts; tool and model scripts never fail the install |
| `pkg/uninstall.sh` | shipped as `/usr/local/knaif/uninstall.sh` |
| `sign.sh` | sign every Mach-O inside-out, hardened runtime, timestamp; verify each (F3) |
| `notarize.sh` | submit, read the log even on success, staple a `.pkg` (F3b, F6) |
| `release.sh` | the whole order: sign → `.zip` → notarize → `.pkg` → notarize → staple → F7 checks |
| `clean-room.sh` | run inside the macOS 12 VM: E3, E4 and E6's install half |
| `homebrew/` | the formula template and its renderer (D19) |

## Prerequisites (on the Mac)

- Apple Silicon, `just bootstrap` (mise provides Rust, Python, uv, just, cmake).
- Xcode or the Command Line Tools — whether the CLT alone suffice is §12 Q3 of the plan.
- For signing: the two Developer ID identities in a keychain and the notarization credentials —
  obtained by the owner (`docs/plans/2026-09-30-macos-signing-certificates.md`), handed over
  through a password manager.

## Build and inspect (no signing)

```bash
just package-native metal     # build, stage, check_macho_deps.py, the .zip, a self-containment smoke
just package-pkg              # the .pkg, UNSIGNED — inspect it, try the options page
pkgutil --expand dist/knaif-<ver>-macos-arm64.pkg /tmp/knaif-pkg && ls /tmp/knaif-pkg
```

## Sign, notarize, staple

```bash
xcrun notarytool store-credentials knaif-notary   # once: API key (preferred) or Apple ID
export KNAIF_SIGN_IDENTITY="Developer ID Application: <name> (<TEAM>)"
export KNAIF_INSTALLER_IDENTITY="Developer ID Installer: <name> (<TEAM>)"
export KNAIF_TEAM_ID=<TEAM>
export KNAIF_NOTARY_PROFILE=knaif-notary
just release-macos
```

`security find-identity -v` lists the exact identity names. The notarization logs and the
per-binary CDHashes land in `dist/notary/` (evidence, never a release asset). No entitlements are
passed: F4 starts with none, and one is added only for a reproduced failure, recorded in the plan.

CI does the same on a tag (`release.yml`, job `macos`) once the owner sets the repository variable
`MACOS_SIGNING=enabled` — only after this hand-run build has passed the clean room.

## The clean room (tart)

A disposable macOS 12 VM with no Xcode, Command Line Tools or Homebrew (D8, D15). Cirrus Labs'
*vanilla* images are exactly that; their login is `admin` / `admin`.

```bash
brew install cirruslabs/cli/tart
tart clone ghcr.io/cirruslabs/macos-monterey-vanilla:latest knaif-room   # fresh for every run
tart run knaif-room &                                                    # opens the VM window
ip="$(tart ip knaif-room)"
ssh admin@"$ip" mkdir room
scp dist/knaif-<ver>-macos-arm64.{zip,pkg} installers/macos/clean-room.sh installers/smoke.sh \
    sandbox/fixtures/documents/sample.pdf models/knaif-qwen3-4b-v2-q4_k_m.gguf admin@"$ip":room/
ssh admin@"$ip" 'cd room && bash clean-room.sh --zip knaif-<ver>-macos-arm64.zip --fixtures . \
    --model knaif-qwen3-4b-v2-q4_k_m.gguf'
```

Three runs, each on a fresh clone (`tart delete knaif-room`, then clone again):

1. `--zip`, network up — the portable tree, quarantined, extracted the way Finder does it.
2. `--pkg --offline` — the stapled package with no network. SSH needs the network, so run this one
   in the VM's own Terminal after turning networking off in System Settings.
3. `--pkg --upgrade-from <previous .pkg>` — the upgrade path, then the uninstaller.

The gate is a real request on the **CPU** (the tree copied without its Metal backend): a VM's GPU
is Apple's paravirtualized one, so what Metal does there is recorded as `INFO`, never gated (D16).
Metal selection and layer offload are proven on the physical Mac, from a fresh user account, with
the same quarantined files. `sample.pdf` comes from `just eval-fixtures documents`.

## Homebrew

After the release is published (RELEASE.md §5 step 9):

```bash
installers/macos/homebrew/render-formula.sh dist/knaif-<ver>-macos-arm64.zip > knaif.rb
brew install --build-from-source ./knaif.rb && brew test knaif
```

Commit it to `blackdeep-tech/homebrew-knaif` as `Formula/knaif.rb`. Watch the first install for
Homebrew rewriting the dylibs' install names (its relocation step): if it does, it re-signs them
ad hoc and the Developer ID signature is gone from the installed copy. That would be a finding for
the plan, not something to patch around in the formula.
