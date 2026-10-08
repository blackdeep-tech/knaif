# First signed and notarized macOS build — step 9 of the Mac's list

Run 2026-10-06 22:32–23:03 on the M1 Pro, macOS 27.2 Beta 2, by the Mac contributor with the owner's
Developer ID certificates (F1). `just release-macos` (`installers/macos/release.sh`), with no
entitlements (F4). `notary/` holds the outputs `notarize.sh` wrote to `dist/notary/`, unchanged:
both submission receipts, both notary logs, and the CDHashes of the signed tree.

## What ran and what Apple said

| Step | Result |
|---|---|
| 1. Sign every Mach-O, libraries first (F3) | 10 signed by team `8YJ4KKV9SJ`, hardened runtime, secure timestamp, no entitlements |
| 2. Rebuild the `.zip` from the signed tree | `knaif-1.2.1-macos-arm64.zip`, sha256 `733fe061…` |
| 3. Notarize the `.zip` (F6) | **Accepted**, `issues: null`, submission `94193dab-…`; the ticket holds all 10 CDHashes |
| 4. Build, sign (Developer ID Installer) and notarize the `.pkg` | **Accepted**, `issues: null`, submission `04f75396-…`; ticket holds the 10 CDHashes and the package; **stapled** and validated |
| 5. Verify as Gatekeeper does (F7) | `codesign` valid and Designated Requirement satisfied; `spctl` on the `.pkg`: accepted, `source=Notarized Developer ID` |

`.pkg` sha256 `ffcd7093…`. The notary logs were read in full (F3b): status `Accepted`,
`statusSummary` "Ready for distribution", no issues, and the ticket lists every Mach-O that ships.

## F4 — entitlements

None were needed on this Mac. The signed `knaif`, under the hardened runtime with no entitlements:

- passes `codesign --verify --strict --deep`
- loads its team-signed backends (`libggml-metal.so`, `libggml-cpu-apple_m1.so`) and PDFium, so
  `disable-library-validation` is not needed
- compiles Metal's embedded shaders at run time and offloads 37/37 layers to the M1 Pro, so
  `allow-jit` is not needed (plan §12, question 4)
- runs a real documents request
- runs on the CPU with the Metal backend removed

This was a local launch, not quarantined. F4 asks for the same test in the clean-room VM, and the
quarantined first launch is E4's, so F4 stays open until step 10.

## Not for release: these files carry a home-directory path

The staged tree was built in a checkout inside the builder's home directory, on the
`documents_105` fix branch (not yet merged). `check_no_local_paths.py` refuses it:

- `knaif` and `libggml` each carry one string, llama.cpp's backend folder under `target/`.
- That path names the builder.
- The `.zip` and `.pkg` contain those binaries.

`package.sh`'s guard stopped the packaging, but only after staging, and `release.sh` signs whatever
is in `dist/staging` without running the guard again. So the signed, notarized files must not be
uploaded.

**The release build has to be made again** from a checkout outside `~` (RELEASE.md), on the tree
chosen for release, and signed and notarized again. The proposed tool fix is for `release.sh` to run
`check_no_local_paths.py` on the staged tree before signing (AGENTS.md: fix the tool, not its
output).

The notary files here contain no local paths or personal names.
