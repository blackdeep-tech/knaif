# Windows Installer Polish — task-page correctness, upgrade detection, identity, signing

**Status:** Planning · **Created:** 2026-07-25 · **Completed:** —
**Owner:** packaging · **Ref:** [`installers/windows/knaif.iss`](../../installers/windows/knaif.iss) · [native-branch-finalization](2026-07-15-native-branch-finalization.md) (C5b / packaging)

> **Status note:** Not started, except W0 (license-accept default), which shipped 2026-07-25.
> **F1–F6** came out of a **live install session on the packaged v1.0.1 artifact** on the Windows dev
> box (2026-07-25) — not from reading the script. **F7–F11** came out of a follow-up cross-read of
> `knaif.iss` against `installers/package.sh` and the native runtime (2026-07-25) and are *script
> reads, not observations* — the *Verified* column says which is which for every row, because two of
> them (F1, F2) are silent misbehaviour that reading `knaif.iss` does not reveal.
> **F2 is the one to fix first**: it installs AGPL software and a ~350 MB office suite that the
> script explicitly marks `unchecked`.
>
> This plan is Windows-only **except for two findings that are not**: **F8** (`README.txt` says
> nothing about what knaif is or who maintains it) and **F11** (`NOTICE` is never distributed — an
> Apache-2.0 §4(d) obligation). Both live in `package.sh`, so they affect the Linux and macOS
> artifacts identically and fixing them once fixes all three. Everything else here is Inno-specific
> and the Linux AppImage is unaffected. `installers/macos/` is a README with no installer behind it,
> so notarization has nothing to attach to yet.

**Goal:** Make the Windows installer behave like a shipped product — correct task defaults, real
upgrade/uninstall detection, an installed tree that says what knaif is and who maintains it, its own
icon and version metadata, a lint that stops the task-page class of bug recurring, and a signing path
that can be switched on the day a certificate exists.

## Release framing

**This plan targets 1.0.2.** `v1.0.1` is tagged, pushed, and protected by the `release-tags` ruleset,
so the tag cannot be moved — nothing here can be retrofitted into it, exactly as recorded for the
spinner-text bug in [`docs/TODO.md`](../TODO.md). Every workstream below ships in the next cut.

**Open — F11 and the already-published artifacts.** F11 is not only a future defect: **every
published v1.0.1 artifact, on all three OSes, is already downloadable without `NOTICE`**, and that is
an Apache-2.0 §4(d) obligation rather than a nice-to-have. Fixing it forward leaves the existing
downloads non-compliant for as long as they are the current release. Three options:

| Option | Cost | Notes |
|---|---|---|
| **Fix forward in 1.0.2** *(recommended)* | none | Shortest path if 1.0.2 is near. The exposure is bounded by the gap between now and that release |
| **Re-cut the 1.0.1 assets** in place | Regenerates `dist/SHA256SUMS` | **Read the *Verification protocol* first** — the published checksums are the thing at risk, and anyone who already verified a download would see a changed hash for an unchanged version. Rarely worth it |
| **Cut 1.0.2 sooner, carrying F11 alone** | one small release | The middle path if 1.0.2 is otherwise far off |

- [ ] **Owner call: pick one, and if it is "fix forward", say roughly when 1.0.2 lands** — the
      recommendation is only sound while that date is close.

## Findings

| # | Symptom | Root cause | Verified | Severity |
|---|---|---|---|---|
| **F1** | No "knaif" row in Add/Remove Programs; installing 1.0.1 over 1.0.0 warns *"folder already exists"* and does not upgrade | Inno detects a prior install **only** via its `…\Uninstall\{AppId}_is1` key. On the dev box that key is absent, so setup sees a bare directory | Registry read (key missing under HKCU **and** HKLM); `unins000.dat` string dump shows the key path *was* recorded at install; Windows CloudStore app-metadata cache still holds a stale `{7e9f3c2a-…}_is1` entry ⇒ it existed once and was later removed. **Fresh 1.0.1 install into a scratch dir writes the key correctly** — the script is not the cause, but nothing detects or repairs the broken state | P0 (user-visible) |
| **F2** | Ghostscript, LibreOffice and Tesseract are **pre-checked** on the Tasks page, and the *"Install supporting tools (via winget):"* heading never renders — the four winget tasks appear indented under *"Add knaif to my PATH"* in the **Integration:** group | The `deps\*` tasks declare a parent task named `deps` that **is never defined** in `[Tasks]`. They render as children of the preceding (checked) task, which defeats their `Flags: unchecked` and discards their `GroupDescription` | Owner screenshot of the real 1.0.1 wizard: all four checked, one heading. Cross-read against [`knaif.iss:78-88`](../../installers/windows/knaif.iss#L78-L88) — no `Name: "deps"` entry exists. *(Not reproducible under `/VERYSILENT`: the task tree is a UI control and silent installs never build it — see* Verification protocol *)* | **P0 — installs AGPL + ~350 MB against stated intent** |
| **F3** | The *"Download the knaif AI model now (~2.5 GB)"* task is offered and pre-checked even when the GGUF is already on disk | `Check: NeedsModel` sits only on the `[Run]` entry ([`knaif.iss:126`](../../installers/windows/knaif.iss#L126)), not on the `[Tasks]` entry. The download is correctly skipped — but the user is never told that, so the wizard promises a 2.5 GB download it will not perform | `NeedsModel`'s path and filename confirmed exact against the on-disk store (`~/.knaif/models/knaif-qwen3-4b-v1-q4_k_m.gguf`, 2 497 280 960 B) and [`contracts/models/model-manifest.yaml:49-50`](../../contracts/models/model-manifest.yaml#L49-L50) — the predicate is right, its placement is wrong | P1 |
| **F4** | Generic icons everywhere: setup.exe, the Add/Remove row, and the wizard's stock CD-ROM artwork | No `.ico` exists in the repo (only [`media/logo.png`](../../media/logo.png) + an SVG); `apps/cli` has no `build.rs`, so `knaif.exe` carries no icon and no VERSIONINFO; the `.iss` sets no `SetupIconFile`, `UninstallDisplayIcon` or `WizardSmallImageFile` | `Get-ChildItem`/`Cargo.toml` read; built setup.exe reports **blank `FileVersion`** (ProductVersion 1.0.1 comes from Inno's `AppVersion`) | P1 — compounds F5 |
| **F5** | SmartScreen *"Windows protected your PC"* | Ships unsigned by design for v1 ([`docs/RELEASE.md:283`](../RELEASE.md#L283)) | Documented decision | P1 |
| **F6** | License page defaults to *"I do not accept"* | Inno's built-in default; no directive exists to change it | Owner screenshot | **Fixed 2026-07-25 (W0)** |
| **F7** | Add/Remove Programs shows a dead *Publisher website* link | `AppPublisherURL=https://github.com/` is a placeholder ([`knaif.iss:53`](../../installers/windows/knaif.iss#L53)) | Script read | P2 |
| **F8** | Nothing in the installed tree says **what knaif is or who maintains it** — no project description, no license name, no homepage, no bug address. `{app}\README.txt` opens on `bin\knaif.exe skills list` | The shipped `README.txt` is generated by a heredoc in [`installers/package.sh`](../../installers/package.sh) that is pure quick-start; the only other prose is `LICENSE` (bare Apache-2.0 text) and `licenses/`. `AppPublisher=knaif` names no entity and F7's URL goes nowhere, so Add/Remove Programs adds nothing either | Owner observation (2026-07-25) + read of the `README.txt` heredoc and [`knaif.iss:48-66`](../../installers/windows/knaif.iss#L48-L66) | P1 — **the only finding that also affects the Linux/macOS artifacts** |
| **F9** | Reinstalling with a skill **deselected** leaves it installed and still listed by `skills list`; kind/version changes leave orphaned DLLs in `bin\` | `[Files]` only ever *copies*. There is no `[InstallDelete]`, so a component that stops being selected keeps its payload from the previous install ([`knaif.iss:98-99`](../../installers/windows/knaif.iss#L98-L99)) and the component tree stops describing what is on disk | Script read (not reproduced — needs the F1 upgrade path working first) | P1 |
| **F10** | Typing `knaif` in an already-open terminal right after install fails with *"not recognized"* | `ChangesEnvironment=yes` broadcasts `WM_SETTINGCHANGE`, which only reaches processes started **after** it. No finish-page text says to open a new terminal; there is no `InfoAfterFile` and `[Icons]` is deliberately empty, so the wizard ends with no next step at all | Script read + documented Windows behaviour | P2 — first impression |
| **F11** | **`NOTICE` is never distributed.** It is not in the installed tree, not in the zip, not in `licenses/` | [`installers/package.sh:320`](../../installers/package.sh#L320) copies `LICENSE` and the third-party files into `licenses/`, but there is no `cp NOTICE`. Apache-2.0 **§4(d)** requires the NOTICE file to travel with redistributions, and [`AGENTS.md`](../../AGENTS.md) designates `NOTICE` as *the* legal-attribution surface — it carries the Qwen3 derivation attribution for the shipped models | Read of the `package.sh` staging block + `ls` of the staged tree: `LICENSE README.txt bin contracts licenses skills`, no `NOTICE` | **P0 — license compliance, and cross-OS like F8.** One `cp` |

## Decision — ARM64 Windows *(settled 2026-07-25: warn and allow)*

`ArchitecturesAllowed=x64compatible` ([`knaif.iss:57`](../../installers/windows/knaif.iss#L57))
matches **ARM64 Windows as well as x64** — that is what `x64compatible` means, as opposed to
`x64os`. So an ARM64 box installs the x64 build and runs llama.cpp inference under Prism emulation,
with no warning anywhere and the `ggml-cpu-*` variant dispatch selecting against an emulated CPUID.

- [ ] **Warn and allow.** Keep `x64compatible`, and add an `IsArm64` check in `InitializeSetup`
  showing a one-time *"this is an x64 build; it will run under emulation and inference will be
  slow"* message with a continue/cancel choice. Keeps the install working and stops it lying.

Rejected: **`x64os`**, which refuses to install at all — a slow knaif beats no knaif, and the CLI's
non-inference surface (`skills list`, `skills deps`, `models pull`) is unaffected by emulation.
Shipping a native ARM64 artifact is the real fix and is out of scope (see *Out of scope*).
The one thing not to do is leave it as-is: today's behaviour is the single shape that is both slow
**and** silent.

## Workstreams

### - [x] W0 — License page defaults to accept *(shipped 2026-07-25)*

`CurPageChanged` forces `WizardForm.LicenseAcceptedRadio.Checked` on `wpLicense`
([`knaif.iss:166-176`](../../installers/windows/knaif.iss#L166-L176)). Re-applies on Back.
Apache-2.0 is a permissive grant requiring no click-through assent, so the page is informational
and defaulting to refusal only adds a step every user undoes.

### - [ ] W1 — Task-page correctness *(F2, F3 — do this first)*

- [ ] **Flatten the `deps\*` task names.** Rename to `depsffmpeg` / `depsgs` / `depssoffice` /
  `depstesseract`, keeping each task's `GroupDescription` and `Components:` filter. Update the four
  matching `Tasks:` references in `[Run]`.
  **Do not fix this by adding a parent `deps` task** — Inno's task tree force-checks children when
  the parent is checked, so a parent would re-break the `unchecked` flags the moment anyone ticks it.
  Flat names are the only shape where per-task defaults survive.
- [ ] **Gate the `getmodel` task itself.** Add `Check: NeedsModel` to the `[Tasks]` entry; Inno hides
  a task whose `Check` returns False, so the whole **AI model:** group disappears once the GGUF is
  present. Keep the `Check` on the `[Run]` entry too — it is the guard against the store being
  emptied between the wizard page and the install step.
- [ ] **Make `ShouldInstall` actually mirror the runtime probe.** [`knaif.iss:132`](../../installers/windows/knaif.iss#L132)
  claims it "mirrors the runtime `knaif skills deps` probe". It does not:
  [`deps.rs:175-205`](../../native/crates/knaif-core/src/deps.rs#L175-L205) resolves
  `$KNAIF_<CMD>_BIN` **first**, then scans PATH honouring `PATHEXT` and **command aliases** — for
  Ghostscript, *any one* of `gs` / `gswin64c` / `gswin32c` satisfies it
  ([`deps.rs:243`](../../native/crates/knaif-core/src/deps.rs#L243)). The installer probes one bare
  name + `.exe` ([`knaif.iss:145`](../../installers/windows/knaif.iss#L145)), so a box the runtime
  considers satisfied still gets a redundant winget install. Take the alias list and the
  `KNAIF_*_BIN` override; W1 is already editing these lines. **Read the aliases from
  `skills/*/skill.yaml`-declared deps if they are reachable at compile time — otherwise duplicate
  them and let W6 assert the two lists agree.**
- [ ] **Say that setup waits for the 2.5 GB download** *(settled 2026-07-25: keep it in setup)*. The
  `getmodel` `[Run]` entry ([`knaif.iss:124-126`](../../installers/windows/knaif.iss#L124-L126)) has
  no `nowait`, and Inno **disables Cancel during the `[Run]` stage** — so setup sits on
  *"Downloading…"* for the length of a 2.5 GB transfer, uncancellable, with the only progress in a
  detached console window the user did not ask for. **Decision: keep it there** — a CLI whose
  headline verb (`run`) does not work after install is the worse failure — but stop it being a
  surprise: extend the task description with **"setup will wait for this"**, so the wizard discloses
  the wait at the point the user opts in rather than at the point it starts. Independent of F3, which
  removes the task entirely when the GGUF is already present.
- [ ] **Re-verify by eye** (per *Verification protocol* — this page cannot be probed silently):
  ffmpeg checked, the other three unchecked, *"Install supporting tools (via winget):"* renders as
  its own heading, and the model task is **absent** on a box that already has the GGUF.

### - [ ] W2 — Upgrade and uninstall robustness *(F1)*

> **Residual risk — F1 has no root cause, and this workstream does not give it one.** The evidence
> says the `{AppId}_is1` key was written at install and later removed by something nobody has
> identified; a fresh install into a scratch dir writes it correctly, so the script is exonerated but
> the mechanism is still unknown. Everything below is therefore **rescue and detection, not
> prevention** — a machine can land in the broken state again. Accept that knowingly: the rescue
> makes the state recoverable, which is the part that actually hurt. If it recurs on a box that has
> not been hand-edited, that is the signal to investigate the cause for real rather than extend the
> rescue.

- [ ] **Stale-install rescue in `InitializeSetup`.** When `{app}\unins000.exe` exists but the
  `{AppId}_is1` key does not, offer to run the orphaned uninstaller before continuing. This is the
  only item that helps a machine already in the broken state — including the owner's, which has a
  1.0.0 tree with no registry key.
- [ ] **`AppMutex`** + a matching named mutex held by `knaif.exe` in `apps/cli/src/main.rs`. Today an
  upgrade while the CLI is running hits a locked `bin\knaif.exe` and silently defers to reboot.
  Add **`SetupMutex`** in the same edit — two concurrent setups are the same class of problem and
  it is one extra directive. A per-user install needs no `Global\` namespace for either.
- [ ] **Clear the stale payload before installing** *(F9)*. `[InstallDelete] Type: filesandordirs`
  over `{app}\skills`, `{app}\contracts` and the staged `{app}\bin` libs, run before `[Files]`.
  This is safe **only** because `~/.knaif` is deliberately outside `{app}`
  ([`knaif.iss:206-214`](../../installers/windows/knaif.iss#L206-L214)) — all three dirs are pure
  staged payload with no user data in them, so a wipe-and-recopy costs nothing but disk churn and is
  the only shape where deselecting a component, dropping a file, or switching kind leaves a tree that
  matches what was chosen. **State that dependency in the `[InstallDelete]` comment**: if anything
  ever starts writing user state under `{app}`, this section becomes destructive.
- [ ] **`UninstallDisplayIcon={app}\bin\knaif.exe`** (depends on W3 for a non-generic result).
- [ ] *(F7 — `AppPublisherURL` and the rest of the identity block)* **Owned by W3, not here.** Both
  workstreams were editing the same `[Setup]` directives; the single table in *W3 → Propagate the
  publisher identity* is the one place that list lives. Do W3's table in one edit and leave `[Setup]`
  alone in W2.
- [ ] **Confirm the upgrade path end to end**: install 1.0.1 → bump `AppVersion` → install again →
  assert no "folder exists" warning, the dir is reused from `InstallLocation`, and the Add/Remove
  row's `DisplayVersion` advances.

### - [ ] W3 — Identity: icon, version metadata, and saying who made this *(F4, F8, F10, F11)*

> **Ordering:** W1 is first for the *installer*, but the `NOTICE` staging fix (F11, below) is a
> licensing obligation and a one-line change — take it out of order and ship it with whatever
> lands next, rather than holding it behind the icon and build-script work in this workstream.

- [ ] **`media/knaif.ico`** — multi-resolution (16/32/48/64/128/256) from the existing logo.
- [ ] **`apps/cli/build.rs`** using `winresource`, embedding the icon **and** VERSIONINFO
  (`FileVersion` / `ProductVersion` / `CompanyName` / `FileDescription`) into `knaif.exe`. Must be
  `cfg(windows)`-gated and must not perturb the Linux/macOS builds or the `dynamic-backends` staging.
- [ ] **`.iss`**: `SetupIconFile`, `VersionInfoVersion={#AppVersion}`, `WizardSmallImageFile`
  (55×58 + 2x/3x BMPs) to replace Inno's stock CD-ROM artwork. Set the rest of the `VersionInfo*`
  block too — **`VersionInfoCompany`, `VersionInfoDescription`, `VersionInfoProductName`,
  `VersionInfoCopyright`** — F4 only caught the blank `FileVersion`, but `setup.exe`'s whole
  Properties → Details tab is empty, and that tab is the one thing a cautious user checks *because*
  of the SmartScreen prompt in F5.
- [ ] Re-run `installers/smoke.sh` on the rebuilt artifact — the icon work touches the build script,
  which is exactly where a staging regression would hide.

#### Who maintains this *(F8)* — the cross-OS half

The installed tree currently answers "what is this and who wrote it" nowhere: `README.txt` is pure
quick-start, `licenses/` is third-party attribution, and **`NOTICE` — the file that would have
answered the question — is not shipped at all**. `LICENSE` now names the right holder (done, below)
but an Apache-2.0 text is not an introduction to a product. This is not a Windows problem:
everything left in this subsection is `package.sh` staging, so **fixing it once fixes all three OS
artifacts**.

- [ ] **Stage `NOTICE`** *(F11 — do this first; it is a licensing obligation, not polish)*. Add
  `cp NOTICE "$STAGE/"` beside the existing `cp LICENSE` at
  [`package.sh:320`](../../installers/package.sh#L320), and a matching `Source:` line in
  [`[Files]`](../../installers/windows/knaif.iss#L90-L99) so it lands in `{app}`. Apache-2.0 §4(d)
  requires it, and it is the file carrying the Qwen3 derivation attribution for the shipped models.
  **Add it to `installers/smoke.sh`'s expected-tree assertion** — a file that must ship but that
  nothing executes is exactly the kind that silently stops shipping again.

- [ ] **Lead `README.txt` with identity before the quick start**: one line on what knaif *is*
  (natural language → validated, executable action plans — the model proposes, deterministic code
  validates and runs), the maintaining entity and its homepage, the license (**Apache-2.0**, with
  `licenses/` named as third-party attribution so the two are not confused), and where bugs go.
  Keep it to ~6 lines above the existing commands — this is an installed-product README, not
  `docs/`, and its job is to orient someone who has just double-clicked an installer.
- [x] **Copyright holder settled and applied (2026-07-25): `Blackdeep Technologies Ltd.`** knaif was
  written by a single author and assigned to the company. [`LICENSE:189`](../../LICENSE#L189) and
  `python/core/LICENSE:189` had read *Copyright 2026 knaif contributors* — the inbound=outbound
  convention for a **jointly-owned** project, which was never accurate here and which quietly
  contradicted [`NOTICE:2`](../../NOTICE#L2). All four files (root + `python/core/` copies of both
  `LICENSE` and `NOTICE`) now carry the same holder. When outside contributions land the line becomes
  `… Blackdeep Technologies Ltd. and the knaif contributors` — a cheap edit at that time, with **no
  CLA required** (see W4 on why Apache-2.0 §5 is sufficient).
- [ ] **Propagate the publisher identity into the installer — every value already exists in-repo.**
  Nothing here needs inventing; these are placeholders and blanks standing next to known-good values:

  | `.iss` directive | Value | Source |
  |---|---|---|
  | `AppPublisher` ([`:52`](../../installers/windows/knaif.iss#L52), today the bare product name) | `Blackdeep Technologies Ltd.` | `NOTICE` |
  | `AppPublisherURL` *(F7 — today `https://github.com/`)* | `https://blackdeep.tech` | [`README.md:261`](../../README.md#L261) |
  | `AppSupportURL` | `https://github.com/blackdeep-tech/knaif/issues` | `blackdeep-tech/knaif` origin |
  | `AppUpdatesURL` | `https://github.com/blackdeep-tech/knaif/releases` | as above |
  | `AppContact` | `knaif@blackdeep.tech` | [`pyproject.toml:23`](../../python/core/pyproject.toml#L23) |
  | `AppReadmeFile` | `{app}\README.txt` | F8 is what makes it worth pointing at |
  | `VersionInfoCompany` | `Blackdeep Technologies Ltd.` | as above |

  - [ ] **Reconcile `AppPublisher` with the certificate subject when W4 lands — not before.** The
    cert subject is **not self-declared**: the CA issues it from official registry records, so it
    is worth reading the issued cert rather than assuming it matches. Windows shows the
    cert subject as the *verified publisher* in the SmartScreen/UAC prompt while Add/Remove Programs
    shows `AppPublisher`, so **those two are the pair that must agree** — a user comparing them has
    no way to tell a benign mismatch from a malicious one. If the issued cert differs from
    `AppPublisher`, change `AppPublisher` to match it, and **leave the copyright notices alone**:
    `LICENSE`/`NOTICE` are ownership statements with no matching requirement against a certificate.
- [ ] **Give the wizard a last page that says what to do next** *(F10)*: `InfoAfterFile` (or a custom
  finished label) carrying **"open a new terminal, then `knaif skills deps`"** — *there is no
  `knaif doctor` subcommand; "doctor" is only a descriptive term in the source comments
  ([`main.rs:67`](../../apps/cli/src/main.rs#L67)). The real command is `skills deps`.*
  `ChangesEnvironment=yes`
  only reaches processes started after the broadcast, so the PATH task genuinely does not work in
  the shell the user already had open — and with `[Icons]` deliberately empty, the wizard otherwise
  ends offering nothing at all.

**Forward note:** the same `.ico` is what the future UI executable needs. For a CLI this is polish;
for a windowed app a default icon reads as unshipped, so W3 is a prerequisite for that work. The
publisher string is a harder dependency still: W4 signs under it.

### - [ ] W4 — Code signing *(F5 — certificate path decided 2026-07-25; unblocked)*

**Not blocked, do it now:** submit each published artifact to Microsoft's Security Intelligence
portal for analysis (the Defender false-positive/developer submission form). It is free, needs no
certificate, no entity and no owner decision, and it addresses the **antivirus-detection** half of
F5 — which signing would not fix anyway, since a signature does not exempt a binary from
heuristic detection. An unsigned, low-reputation `setup.exe` that also ships loadable `ggml-*.dll`
backends and downloads a 2.5 GB file post-install is exactly the shape that draws a heuristic hit.
Fold the submission into `docs/RELEASE.md` as a release step, not a one-off.

Two layers; signing only the installer leaves every bundled DLL unsigned.

- [ ] **Payload** — sign `knaif.exe` and the shipped `llama.dll` / `ggml-*.dll` after `cargo build`,
  before `package.sh` stages. Hook via an env var (`KNAIF_SIGN_CMD`) applied per staged binary, so
  unsigned local builds keep working unchanged.
- [ ] **Installer** — `SignTool=knaifsign $f` + **`SignedUninstaller=yes`** in `[Setup]`; the tool is
  supplied at compile time (`ISCC /Sknaifsign="…"`). Without `SignedUninstaller`, `unins000.exe` is
  unsigned and carries its own SmartScreen friction.
- [ ] **Always timestamp** (`/tr` + `/td sha256`) — untimestamped binaries stop validating the day
  the certificate expires.
- [ ] Update [`docs/RELEASE.md`](../RELEASE.md) — the §6 "ships unsigned" note and the checksum
  guidance both change.

**Certificate landscape** (context for the decision recorded below). Since June 2023 every
publicly-trusted code-signing key must live on FIPS-140 hardware, so a plain `.pfx` is no longer
purchasable from any CA.

> **No certificate buys a clean first download any more.** Microsoft removed EV's instant-SmartScreen
> privilege in 2024 (all EV code-signing OIDs were pulled from the Trusted Root Program roots in
> August 2024) because malware operators were buying EV certs through shell companies precisely to
> inherit that trust. **EV, OV and Azure Artifact Signing are now identical to SmartScreen** — every
> one of them accrues reputation per file hash through download volume. Any purchasing argument that
> rests on "EV skips the warning" is stale; the honest reason to sign is integrity, enterprise
> allow-listing, and not looking abandoned — not making the first prompt disappear.

| Option | Cost | Availability | Notes |
|---|---|---|---|
| **SignPath Foundation** (OSS program) | **Free** | Qualifying OSS projects | OV-level, key on their HSM, CI-integrated. knaif appears to qualify — see below |
| **Azure Artifact Signing** (ex-Trusted Signing) | ~$9.99/mo | **Orgs:** US, CA, EU, UK · **Individuals:** US/CA only | No token; native GitHub Actions/Azure DevOps integration |
| OV cert on HSM token / cloud HSM | ~$150–300/yr | Worldwide | The fallback when the two above don't fit |
| EV cert on token | $400+/yr | Worldwide | **No longer justified for SmartScreen.** Only for enterprise procurement or kernel-mode drivers |

**Decided 2026-07-25: apply to SignPath Foundation**, with Azure Artifact Signing as the fallback if
the application is refused. knaif meets the published criteria — OSI-approved license (Apache-2.0)
with no commercial dual-licensing, no proprietary components in the signed artifact (the GGUF is
downloaded post-install, not bundled), actively maintained, already released, public repo, documented
download page.

**The disqualifier this plan previously flagged does not apply.** The owner's position (2026-07-25)
is that *knaif itself is and stays OSS*; a future commercial product would **use** Apache-2.0 knaif
rather than ship knaif under other terms. That is not dual-licensing — it is what a permissive
license is for — so Foundation eligibility is unaffected and no relicensing is ever required.

> **The one constraint that does survive, and it is easy to break later.** SignPath Foundation signs
> **OSS artifacts**. If a proprietary UI executable ever ships, it needs its **own certificate and its
> own artifact** — it must not be folded into the knaif installer, because that would put a
> proprietary component inside the Foundation-signed artifact and void the grant. Adding the UI to
> the existing installer is the obvious thing to do at that point, and it is precisely the move that
> breaks this. **Record it wherever the UI packaging gets planned, not only here.**

**Corollary — no CLA is needed, ever, for this purpose.** Apache-2.0 **§5** already licenses inbound
contributions under the same terms, so outside PRs need no agreement for the project to function.
A CLA would only be needed to preserve *unilateral relicensing*, which the decision above rules out.
If and when outside PRs start, adopt a **DCO** sign-off instead: it certifies the right to submit,
changes no ownership, and costs no contributor goodwill. This is not urgent and has no deadline —
unlike a CLA, which can only be adopted before the first outside contribution.

- [ ] Confirm the org's country/entity status against Azure Artifact Signing's geography before
      relying on it as the fallback. Microsoft's April 2026 comparison lists **orgs in US/CA/EU/UK**,
      but an April 2025 service update restricted onboarding to **US/CA orgs with 3+ years of
      verifiable history** — the two have not obviously been reconciled, so verify at signup rather
      than from the docs.
- [ ] Note the renewal cadence change: from **2026-03-01** the CA/Browser Forum caps publicly-trusted
      code-signing certificate validity at **458 days**, so any purchased-cert path is now a
      roughly-annual renewal, not a 2–3 year one.

### - [ ] W5 — Docs

- [ ] [`installers/windows/README.md`](../../installers/windows/README.md) — its *Uninstall* section
  describes upgrade behaviour that F1 shows can silently fail; add the detection dependency. Its
  *What the installer does* section also states the winget tools are optional, which F2 makes untrue
  in the shipped wizard — correct it when W1 lands, not before.
- [ ] [`docs/RELEASE.md`](../RELEASE.md) — signing (W4), the Microsoft submission step, and the new
  verification protocol below.

### - [ ] W6 — Regression guard: lint `knaif.iss` in the test suite *(makes F2 non-recurring)*

W1 ends in *"re-verify by eye"* — a human step that cannot run in CI, on a page the *Verification
protocol* proves cannot be probed silently. F2 shipped in v1.0.1 precisely because nothing checked
it. **The bug class is fully decidable from the text of the script**, so it does not need the wizard:

- [ ] **`python/core/tests/test_installer_iss.py`** — parse `knaif.iss` and assert:
  - every task `Name:` containing `\` has its parent declared in `[Tasks]` *(this is exactly F2)*;
  - every `Tasks:` reference in `[Run]` and `[Registry]` resolves to a declared task;
  - every `Components:` filter resolves to a declared component;
  - every `Check:` names a function that exists in `[Code]`;
  - the `deps\*` → winget probe names agree with the aliases in
    [`deps.rs`](../../native/crates/knaif-core/src/deps.rs) *(the W1 mirror claim, asserted rather
    than commented)*.
- [ ] Cross-check the shape against [`test_version_consistency.py:18-46`](../../python/core/tests/test_version_consistency.py#L18-L46),
  which **already parses `knaif.iss`** for `AppVersion` — the precedent, the path constant and the
  regex idiom exist; this is an extension of an established test, not a new mechanism.

**Scope discipline:** this is a text lint, not an Inno emulator. It catches undeclared references and
name drift — the F2/F9 class. It cannot catch F1 (registry state), F3's task-vs-run placement
semantics, or anything about rendering. Those stay manual, and the *Verification protocol* is what
governs them.

## Verification protocol

Learned the hard way on 2026-07-25; follow it for every change in this plan.

- **Compile to a scratch dir** — `ISCC /O<scratch>`. Compiling to the default output overwrites
  `dist/knaif-<ver>-windows-x64-setup.exe`, which is a **published artifact with a row in
  `dist/SHA256SUMS`**; a stray rebuild silently invalidates the published checksum.
- **Install contained** — `/VERYSILENT /TASKS="" /DIR=<scratch>`. Empty `/TASKS` is what keeps the
  run from touching PATH or pulling 2.5 GB.
- **Never run the uninstaller with `/SUPPRESSMSGBOXES`.** `CurUninstallStepChanged` asks whether to
  delete `~/.knaif`, and `SuppressibleMsgBox` answers **IDYES** — a "cleanup" of a test install
  destroys the real model store and costs a 2.5 GB re-download. Tear down by hand instead: remove
  the scratch dir and delete the `{AppId}_is1` key.
- **The Tasks page cannot be probed silently.** A `/VERYSILENT` run never builds the task tree, so
  the F2 class of bug does not reproduce and the install log records no task selection. Confirmed by
  probe on 2026-07-25. **Task-page defaults must be verified in the GUI.**

## Out of scope

- **macOS signing/notarization** — no macOS installer exists yet (`installers/macos/` is a README).
- **Linux AppImage signing** — unaffected by every finding here. Note that **F8 is not out of scope
  for Linux/macOS**: the `README.txt` it fixes is written once by `package.sh` for all three.
- **A native ARM64 Windows artifact** — a `package.sh` + CI matter. Only the *installer's* behaviour
  on ARM64 is decided here (warn and allow — see *Decision — ARM64 Windows* above).
- **Root-causing F1** — why the uninstall key disappeared. W2 rescues the state rather than
  preventing it; see the residual-risk note there for the trigger to reopen this.
- **A Start Menu entry** (`[Icons]`) — deliberate for a CLI; revisit with the UI executable.
- **CUDA component** — remains the opt-in `~/.knaif/backends` payload, tracked in
  [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md).
