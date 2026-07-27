# Windows Installer Polish — task-page correctness, upgrade detection, identity, signing

**Status:** Done · **Created:** 2026-07-25 · **Completed:** 2026-07-27
**Owner:** packaging · **Ref:** [`installers/windows/knaif.iss`](../../installers/windows/knaif.iss) · [native-branch-finalization](2026-07-15-native-branch-finalization.md) (C5b / packaging)

> **Status note:** W0 shipped 2026-07-25. W1, W2 and W3 shipped 2026-07-26/27 and were verified
> in a GUI install session on 2026-07-27 — task page, identity, NOTICE in the installed tree,
> `[InstallDelete]`, `AppMutex`, the stale-install rescue, and both confirmation prompts
> defaulting to No. The two items that outlived that session are now **closed rather than left
> hanging**: the end-to-end upgrade assertion **moved to [`docs/RELEASE.md`](../RELEASE.md) §4**
> (it is a recurring release check needing a second, higher-versioned build, which a release cut
> produces and this plan never does), and `WizardSmallImageFile` is **won't-do** (Inno 6 ships no
> modern small image at all, and `WizardStyle=modern` renders correctly without one).
> W5 and W6 shipped 2026-07-27 — W6's lint was verified by injecting all 14 of the
> mutations it claims to catch, so it is known to fail, not merely known to pass.
> **W4 moved out 2026-07-27** to
> [code-signing](2026-07-27-code-signing.md) — it was the only workstream gated on an external
> party, so this plan can now close without it.
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

**Closed — F11 has no published exposure** *(corrected 2026-07-27)*. This section originally claimed
that **"every published v1.0.1 artifact, on all three OSes, is already downloadable without
`NOTICE`"** and weighed three remediation options against it. **That premise was false.** Checked
against the live repository and registries:

| Channel | Actual state at v1.0.1 |
|---|---|
| GitHub Releases | **none** — `gh api repos/blackdeep-tech/knaif/releases` returns `[]` |
| Native artifacts | **not downloadable anywhere.** The Windows zip and `setup.exe` were built and never uploaded |
| Git tags | `v1.0.1` only (there is no `v1.0.0` tag on the remote) |
| PyPI `knaif` | 1.0.0 and 1.0.1 live — and **already compliant**: the published wheel carries `LICENSE` and `NOTICE` under `dist-info/licenses/` |

So no recipient has ever received a knaif artifact without `NOTICE`, and there is nothing to
remediate. **Fix forward, at zero cost.** Re-cutting the 1.0.1 assets is moot — there are no
published assets and no published checksums to invalidate. This also means **1.0.2 will be the
project's first native release**, not a follow-up to one, which raises the bar on the release
verification above rather than lowering it.

*(A PyPI-only 1.0.1 is internally coherent: its changelog is entirely Python-runtime fixes and states
outright that the native runtime is unchanged, so nothing was withheld by not publishing binaries.)*

## Findings

| # | Symptom | Root cause | Verified | Severity |
|---|---|---|---|---|
| **F1** | No "knaif" row in Add/Remove Programs; installing 1.0.1 over 1.0.0 warns *"folder already exists"* and does not upgrade | Inno detects a prior install **only** via its `…\Uninstall\{AppId}_is1` key. On the dev box that key is absent, so setup sees a bare directory | Registry read (key missing under HKCU **and** HKLM); `unins000.dat` string dump shows the key path *was* recorded at install; Windows CloudStore app-metadata cache still holds a stale `{7e9f3c2a-…}_is1` entry ⇒ it existed once and was later removed. **Fresh 1.0.1 install into a scratch dir writes the key correctly** — the script is not the cause, but nothing detects or repairs the broken state | P0 (user-visible) |
| **F2** | Ghostscript, LibreOffice and Tesseract are **pre-checked** on the Tasks page, and the *"Install supporting tools (via winget):"* heading never renders — the four winget tasks appear indented under *"Add knaif to my PATH"* in the **Integration:** group | The `deps\*` tasks declare a parent task named `deps` that **is never defined** in `[Tasks]`. They render as children of the preceding (checked) task, which defeats their `Flags: unchecked` and discards their `GroupDescription` | Owner screenshot of the real 1.0.1 wizard: all four checked, one heading. Cross-read against [`knaif.iss:95-110`](../../installers/windows/knaif.iss#L95-L110) — no `Name: "deps"` entry exists. *(Not reproducible under `/VERYSILENT`: the task tree is a UI control and silent installs never build it — see* Verification protocol *)* | **P0 — installs AGPL + ~350 MB against stated intent** |
| **F3** | The *"Download the knaif AI model now (~2.5 GB)"* task is offered and pre-checked even when the GGUF is already on disk | `Check: NeedsModel` sits only on the `[Run]` entry ([`knaif.iss:161`](../../installers/windows/knaif.iss#L161)), not on the `[Tasks]` entry. The download is correctly skipped — but the user is never told that, so the wizard promises a 2.5 GB download it will not perform | `NeedsModel`'s path and filename confirmed exact against the on-disk store (`~/.knaif/models/knaif-qwen3-4b-v1-q4_k_m.gguf`, 2 497 280 960 B) and [`contracts/models/model-manifest.yaml:49-50`](../../contracts/models/model-manifest.yaml#L49-L50) — the predicate is right, its placement is wrong | P1 |
| **F4** | Generic icons everywhere: setup.exe, the Add/Remove row, and the wizard's stock CD-ROM artwork | No `.ico` exists in the repo (only [`media/logo.png`](../../media/logo.png) + an SVG); `apps/cli` has no `build.rs`, so `knaif.exe` carries no icon and no VERSIONINFO; the `.iss` sets no `SetupIconFile`, `UninstallDisplayIcon` or `WizardSmallImageFile` | `Get-ChildItem`/`Cargo.toml` read; built setup.exe reports **blank `FileVersion`** (ProductVersion 1.0.1 comes from Inno's `AppVersion`) | P1 — compounds F5 |
| **F5** | SmartScreen *"Windows protected your PC"* | Ships unsigned by design for v1 ([`docs/RELEASE.md:283`](../RELEASE.md#L283)) | Documented decision | P1 — **open by decision, not oversight**: moved to [code-signing](2026-07-27-code-signing.md) 2026-07-27, gated on a certificate |
| **F6** | License page defaults to *"I do not accept"* | Inno's built-in default; no directive exists to change it | Owner screenshot | **Fixed 2026-07-25 (W0)** |
| **F7** | Add/Remove Programs shows a dead *Publisher website* link | `AppPublisherURL=https://github.com/` is a placeholder ([`knaif.iss:70`](../../installers/windows/knaif.iss#L70)) | Script read | P2 |
| **F8** | Nothing in the installed tree says **what knaif is or who maintains it** — no project description, no license name, no homepage, no bug address. `{app}\README.txt` opens on `bin\knaif.exe skills list` | The shipped `README.txt` is generated by a heredoc in [`installers/package.sh`](../../installers/package.sh) that is pure quick-start; the only other prose is `LICENSE` (bare Apache-2.0 text) and `licenses/`. `AppPublisher=knaif` names no entity and F7's URL goes nowhere, so Add/Remove Programs adds nothing either | Owner observation (2026-07-25) + read of the `README.txt` heredoc and [`knaif.iss:64-83`](../../installers/windows/knaif.iss#L64-L83) | P1 — **the only finding that also affects the Linux/macOS artifacts** |
| **F9** | Reinstalling with a skill **deselected** leaves it installed and still listed by `skills list`; kind/version changes leave orphaned DLLs in `bin\` | `[Files]` only ever *copies*. There is no `[InstallDelete]`, so a component that stops being selected keeps its payload from the previous install ([`knaif.iss:127-128`](../../installers/windows/knaif.iss#L127-L128)) and the component tree stops describing what is on disk | Script read (not reproduced — needs the F1 upgrade path working first) | P1 |
| **F10** | Typing `knaif` in an already-open terminal right after install fails with *"not recognized"* | `ChangesEnvironment=yes` broadcasts `WM_SETTINGCHANGE`, which only reaches processes started **after** it. No finish-page text says to open a new terminal; there is no `InfoAfterFile` and `[Icons]` is deliberately empty, so the wizard ends with no next step at all | Script read + documented Windows behaviour | P2 — first impression |
| **F11** | **`NOTICE` is never distributed.** It is not in the installed tree, not in the zip, not in `licenses/` | [`installers/package.sh:320`](../../installers/package.sh#L320) copies `LICENSE` and the third-party files into `licenses/`, but there is no `cp NOTICE`. Apache-2.0 **§4(d)** requires the NOTICE file to travel with redistributions, and [`AGENTS.md`](../../AGENTS.md) designates `NOTICE` as *the* legal-attribution surface — it carries the Qwen3 derivation attribution for the shipped models | Read of the `package.sh` staging block + `ls` of the staged tree: `LICENSE README.txt bin contracts licenses skills`, no `NOTICE` | **P0 — license compliance, and cross-OS like F8.** One `cp` |

## Decision — ARM64 Windows *(settled 2026-07-25: warn and allow)*

`ArchitecturesAllowed=x64compatible` ([`knaif.iss:74`](../../installers/windows/knaif.iss#L74))
matches **ARM64 Windows as well as x64** — that is what `x64compatible` means, as opposed to
`x64os`. So an ARM64 box installs the x64 build and runs llama.cpp inference under Prism emulation,
with no warning anywhere and the `ggml-cpu-*` variant dispatch selecting against an emulated CPUID.

- [→] **Warn and allow — decision stands, implementation moved to [`docs/TODO.md`](../TODO.md)**
  *(2026-07-27)*. Keep `x64compatible`, and add an `IsArm64` check in `InitializeSetup` showing a
  one-time *"this is an x64 build; it will run under emulation and inference will be slow"* message
  with a continue/cancel choice. Keeps the install working and stops it lying.
  **Why it did not ship with this plan:** there is no ARM64 machine to test on, and a wizard path
  that cannot be exercised is exactly how F1 and F2 reached users. Writing it blind would put
  untested code in `InitializeSetup` — the one procedure that already gates every install, including
  the stale-install rescue. It waits for a box, or for a native ARM64 artifact to make it moot.

Rejected: **`x64os`**, which refuses to install at all — a slow knaif beats no knaif, and the CLI's
non-inference surface (`skills list`, `skills deps`, `models pull`) is unaffected by emulation.
Shipping a native ARM64 artifact is the real fix and is out of scope (see *Out of scope*).
The one thing not to do is leave it as-is: today's behaviour is the single shape that is both slow
**and** silent.

## Workstreams

### - [x] W0 — License page defaults to accept *(shipped 2026-07-25)*

`CurPageChanged` forces `WizardForm.LicenseAcceptedRadio.Checked` on `wpLicense`
([`knaif.iss:291-300`](../../installers/windows/knaif.iss#L291-L300)). Re-applies on Back.
Apache-2.0 is a permissive grant requiring no click-through assent, so the page is informational
and defaulting to refusal only adds a step every user undoes.

### - [x] W1 — Task-page correctness *(F2, F3 — shipped 2026-07-26)*

- [x] **Flatten the `deps\*` task names.** Rename to `depsffmpeg` / `depsgs` / `depssoffice` /
  `depstesseract`, keeping each task's `GroupDescription` and `Components:` filter. Update the four
  matching `Tasks:` references in `[Run]`.
  **Do not fix this by adding a parent `deps` task** — Inno's task tree force-checks children when
  the parent is checked, so a parent would re-break the `unchecked` flags the moment anyone ticks it.
  Flat names are the only shape where per-task defaults survive.
- [x] **Gate the `getmodel` task itself.** Add `Check: NeedsModel` to the `[Tasks]` entry; Inno hides
  a task whose `Check` returns False, so the whole **AI model:** group disappears once the GGUF is
  present. Keep the `Check` on the `[Run]` entry too — it is the guard against the store being
  emptied between the wizard page and the install step.
- [x] **Make `ShouldInstall` actually mirror the runtime probe.** [`knaif.iss:163`](../../installers/windows/knaif.iss#L163)
  claims it "mirrors the runtime `knaif skills deps` probe". It does not:
  [`deps.rs:175-205`](../../native/crates/knaif-core/src/deps.rs#L175-L205) resolves
  `$KNAIF_<CMD>_BIN` **first**, then scans PATH honouring `PATHEXT` and **command aliases** — for
  Ghostscript, *any one* of `gs` / `gswin64c` / `gswin32c` satisfies it
  ([`deps.rs:243`](../../native/crates/knaif-core/src/deps.rs#L243)). The installer probes one bare
  name + `.exe` ([`knaif.iss:174-197`](../../installers/windows/knaif.iss#L174-L197)), so a box the runtime
  considers satisfied still gets a redundant winget install. Take the alias list and the
  `KNAIF_*_BIN` override; W1 is already editing these lines. **Read the aliases from
  `skills/*/skill.yaml`-declared deps if they are reachable at compile time — otherwise duplicate
  them and let W6 assert the two lists agree.**
  - *Settled: they are **not** reachable — ISPP cannot read YAML — so the lists are duplicated in
    `[Run]` and [W6's lint](../../python/core/tests/test_installer_iss.py) asserts they agree with
    `skills/*/skill.yaml`, in both directions and including `all_required`.*
  - **`all_required` is part of the contract and was missing from this bullet** *(added 2026-07-26)*.
    [`deps.rs:26`](../../native/crates/knaif-core/src/deps.rs#L26) defines it: `true` means the
    commands are **distinct binaries and every one must resolve**; the default `false` means they are
    **alternative names and any one satisfies**. `ffmpeg` sets `all_required: true` over
    `[ffmpeg, ffprobe]` ([`skills/ffmpeg/skill.yaml:21`](../../skills/ffmpeg/skill.yaml#L21)), so a
    probe that treats the list as aliases reports satisfied when only `ffmpeg` is on PATH. Alias
    semantics alone are **not** runtime-equivalent.
  - **The source of truth is `skills/*/skill.yaml`, not `deps.rs`.** `deps.rs` holds the schema and
    the probe; the `gs/gswin64c/gswin32c` list at `deps.rs:243` that this plan originally cited is a
    `#[cfg(test)]` fixture. W6's lint must parse the YAML contracts.
- [x] **Say that setup waits for the 2.5 GB download** *(settled 2026-07-25: keep it in setup)*. The
  `getmodel` `[Run]` entry ([`knaif.iss:159-161`](../../installers/windows/knaif.iss#L159-L161)) has
  no `nowait`, and Inno **disables Cancel during the `[Run]` stage** — so setup sits on
  *"Downloading…"* for the length of a 2.5 GB transfer, uncancellable, with the only progress in a
  detached console window the user did not ask for. **Decision: keep it there** — a CLI whose
  headline verb (`run`) does not work after install is the worse failure — but stop it being a
  surprise: extend the task description with **"setup will wait for this"**, so the wizard discloses
  the wait at the point the user opts in rather than at the point it starts. Independent of F3, which
  removes the task entirely when the GGUF is already present.
- [x] **Re-verify by eye** (per *Verification protocol* — this page cannot be probed silently):
  ffmpeg checked, the other three unchecked, *"Install supporting tools (via winget):"* renders as
  its own heading, and the model task is **absent** on a box that already has the GGUF.

### - [x] W2 — Upgrade and uninstall robustness *(F1 — shipped 2026-07-26)*

> **Residual risk — F1 has no root cause, and this workstream does not give it one.** The evidence
> says the `{AppId}_is1` key was written at install and later removed by something nobody has
> identified; a fresh install into a scratch dir writes it correctly, so the script is exonerated but
> the mechanism is still unknown. Everything below is therefore **rescue and detection, not
> prevention** — a machine can land in the broken state again. Accept that knowingly: the rescue
> makes the state recoverable, which is the part that actually hurt. If it recurs on a box that has
> not been hand-edited, that is the signal to investigate the cause for real rather than extend the
> rescue.

- [x] **Stale-install rescue in `InitializeSetup`.** When `{app}\unins000.exe` exists but the
  `{AppId}_is1` key does not, offer to run the orphaned uninstaller before continuing. This is the
  only item that helps a machine already in the broken state — including the owner's, which has a
  1.0.0 tree with no registry key.
- [x] **`AppMutex`** + a matching named mutex held by `knaif.exe` in `apps/cli/src/main.rs`. Today an
  upgrade while the CLI is running hits a locked `bin\knaif.exe` and silently defers to reboot.
  Add **`SetupMutex`** in the same edit — two concurrent setups are the same class of problem and
  it is one extra directive. A per-user install needs no `Global\` namespace for either.
- [x] **Clear the stale payload before installing** *(F9)*. `[InstallDelete] Type: filesandordirs`
  over `{app}\skills`, `{app}\contracts` and the staged `{app}\bin` libs, run before `[Files]`.
  This is safe **only** because `~/.knaif` is deliberately outside `{app}`
  ([`knaif.iss:331-339`](../../installers/windows/knaif.iss#L331-L339)) — all three dirs are pure
  staged payload with no user data in them, so a wipe-and-recopy costs nothing but disk churn and is
  the only shape where deselecting a component, dropping a file, or switching kind leaves a tree that
  matches what was chosen. **State that dependency in the `[InstallDelete]` comment**: if anything
  ever starts writing user state under `{app}`, this section becomes destructive.
- [x] **`UninstallDisplayIcon={app}\bin\knaif.exe`** (depends on W3 for a non-generic result).
- [x] *(F7 — `AppPublisherURL` and the rest of the identity block)* **Owned by W3, not here.** Both
  workstreams were editing the same `[Setup]` directives; the single table in *W3 → Propagate the
  publisher identity* is the one place that list lives. Do W3's table in one edit and leave `[Setup]`
  alone in W2.
- [→] **Confirm the upgrade path end to end — moved to [`docs/RELEASE.md`](../RELEASE.md) §4**
  *(2026-07-27)*. install → bump `AppVersion` → install again → assert no "folder exists" warning,
  the dir is reused from `InstallLocation`, and the Add/Remove row's `DisplayVersion` advances.
  **This is a recurring release check, not a one-off plan task**, and it needs a *second* build at a
  higher version — something a release cut produces anyway and this plan never does. Leaving it here
  would either hold the plan open indefinitely or get ticked once and never run again.
  - **What is still unverified, and it is more than "we did not try an upgrade".**
    **`[InstallDelete]` has never executed**: on a fresh install all four target dirs are absent, so
    every entry was a no-op. It is a destructive section that has literally never run — W6's lint
    proves the *text* is safe (never `{app}` itself, always beneath it), but not that Inno runs it
    before `[Files]` and leaves a working tree. **`AppMutex` has never been exercised** either,
    because a fresh install has no running `knaif.exe`; if its string and `hold_app_mutex`'s ever
    disagree the directive silently does nothing and the deferred-to-reboot bug returns unsignalled.
  - **Run it under a throwaway `AppId`, never the production one** — see the RELEASE.md subsection.
    A test install registered under a throwaway GUID but sitting in the *default* directory is its
    own hazard: the real installer will not recognise it as a prior version, so it installs over the
    same tree and leaves **two Add/Remove rows sharing one directory**, where uninstalling either
    breaks the other. Uninstall test builds when done.

### - [x] W3 — Identity: icon, version metadata, and saying who made this *(F4, F8, F10, F11 — shipped 2026-07-27)*

> **Ordering:** W1 is first for the *installer*, but the `NOTICE` staging fix (F11, below) is a
> licensing obligation and a one-line change — take it out of order and ship it with whatever
> lands next, rather than holding it behind the icon and build-script work in this workstream.

- [x] **`media/knaif.ico`** — multi-resolution (16/32/48/64/128/256) from the existing logo.
- [x] **`apps/cli/build.rs`** using `winresource`, embedding the icon **and** VERSIONINFO
  (`FileVersion` / `ProductVersion` / `CompanyName` / `FileDescription`) into `knaif.exe`. Must be
  `cfg(windows)`-gated and must not perturb the Linux/macOS builds or the `dynamic-backends` staging.
- [x] **`.iss`**: `SetupIconFile`, `VersionInfoVersion={#AppVersion}`. Set the rest of the `VersionInfo*`
  block too — **`VersionInfoCompany`, `VersionInfoDescription`, `VersionInfoProductName`,
  `VersionInfoCopyright`** — F4 only caught the blank `FileVersion`, but `setup.exe`'s whole
  Properties → Details tab is empty, and that tab is the one thing a cautious user checks *because*
  of the SmartScreen prompt in F5.
- [~] **`WizardSmallImageFile` — closed as won't-do** *(2026-07-27)*. This plan asked for 55×58 +
  2x/3x BMPs. Measured against the installed Inno 6, `WizClassicSmallImage.bmp` is **55×55 —
  square**, so the requested dimensions were wrong. More to the point, Inno 6 ships **only** the
  Classic images; there is no `WizModernSmallImage`, and with `WizardStyle=modern` the wizard simply
  renders no small image unless one is supplied — which is what it does today, and it looks correct.
  This is the only purely decorative item in the plan, the DPI variant sizes still could not be
  confirmed without the help file, and an unfinished checkbox on an otherwise closed plan costs more
  than the missing image. **Decision: do not ship one.** If wizard branding is ever wanted, render
  the DPI set the shipped help names from `media/logo-square.png` — a short standalone job, not a
  blocker for anything here.
- [x] Re-run `installers/smoke.sh` on the rebuilt artifact — the icon work touches the build script,
  which is exactly where a staging regression would hide.

#### Who maintains this *(F8)* — the cross-OS half

The installed tree currently answers "what is this and who wrote it" nowhere: `README.txt` is pure
quick-start, `licenses/` is third-party attribution, and **`NOTICE` — the file that would have
answered the question — is not shipped at all**. `LICENSE` now names the right holder (done, below)
but an Apache-2.0 text is not an introduction to a product. This is not a Windows problem:
everything left in this subsection is `package.sh` staging, so **fixing it once fixes all three OS
artifacts**.

- [x] **Stage `NOTICE`** *(F11 — do this first; it is a licensing obligation, not polish)*. Add
  `cp NOTICE "$STAGE/"` beside the existing `cp LICENSE` at
  [`package.sh:320`](../../installers/package.sh#L320), and a matching `Source:` line in
  [`[Files]`](../../installers/windows/knaif.iss#L119-L128) so it lands in `{app}`. Apache-2.0 §4(d)
  requires it, and it is the file carrying the Qwen3 derivation attribution for the shipped models.
  **Add it to `installers/smoke.sh`'s expected-tree assertion** — a file that must ship but that
  nothing executes is exactly the kind that silently stops shipping again.

- [x] **Lead `README.txt` with identity before the quick start**: one line on what knaif *is*
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
  CLA required** (see [code-signing](2026-07-27-code-signing.md) on why Apache-2.0 §5 is sufficient).
- [x] **Propagate the publisher identity into the installer — every value already exists in-repo.**
  Nothing here needs inventing; these are placeholders and blanks standing next to known-good values:

  | `.iss` directive | Value | Source |
  |---|---|---|
  | `AppPublisher` ([`:52`](../../installers/windows/knaif.iss#L69), today the bare product name) | `Blackdeep Technologies Ltd.` | `NOTICE` |
  | `AppPublisherURL` *(F7 — today `https://github.com/`)* | `https://blackdeep.tech` | [`README.md:261`](../../README.md#L261) |
  | `AppSupportURL` | `https://github.com/blackdeep-tech/knaif/issues` | `blackdeep-tech/knaif` origin |
  | `AppUpdatesURL` | `https://github.com/blackdeep-tech/knaif/releases` | as above |
  | `AppContact` | `knaif@blackdeep.tech` | [`pyproject.toml:23`](../../python/core/pyproject.toml#L23) |
  | `AppReadmeFile` | `{app}\README.txt` | F8 is what makes it worth pointing at |
  | `VersionInfoCompany` | `Blackdeep Technologies Ltd.` | as above |

  - [→] **Reconcile `AppPublisher` with the certificate subject — carried by
    [code-signing](2026-07-27-code-signing.md) S3, since it needs a certificate to exist.** The
    cert subject is **not self-declared**: the CA issues it from official registry records, so it
    is worth reading the issued cert rather than assuming it matches. Windows shows the
    cert subject as the *verified publisher* in the SmartScreen/UAC prompt while Add/Remove Programs
    shows `AppPublisher`, so **those two are the pair that must agree** — a user comparing them has
    no way to tell a benign mismatch from a malicious one. If the issued cert differs from
    `AppPublisher`, change `AppPublisher` to match it, and **leave the copyright notices alone**:
    `LICENSE`/`NOTICE` are ownership statements with no matching requirement against a certificate.
- [x] **Give the wizard a last page that says what to do next** *(F10)*: `InfoAfterFile` (or a custom
  finished label) carrying **"open a new terminal, then `knaif skills deps`"** — *there is no
  `knaif doctor` subcommand; "doctor" is only a descriptive term in the source comments
  ([`main.rs:67`](../../apps/cli/src/main.rs#L67)). The real command is `skills deps`.*
  `ChangesEnvironment=yes`
  only reaches processes started after the broadcast, so the PATH task genuinely does not work in
  the shell the user already had open — and with `[Icons]` deliberately empty, the wizard otherwise
  ends offering nothing at all.

**Forward note:** the same `.ico` is what the future UI executable needs. For a CLI this is polish;
for a windowed app a default icon reads as unshipped, so W3 is a prerequisite for that work. The
publisher string is a harder dependency still: [code-signing](2026-07-27-code-signing.md) signs under it.

### - [→] W4 — Code signing *(F5 — moved out 2026-07-27)*

**Moved to its own plan: [code-signing](2026-07-27-code-signing.md).** It was the only workstream
here gated on an external party — a certificate — so leaving it in place meant this plan could
never close while everything else was ready to ship. The extracted plan carries the full certificate
landscape, the SignPath Foundation eligibility finding, the OSS/CLA decisions, and the payload +
installer signing tasks unchanged.

**Deferred 2026-07-27:** the SignPath Foundation application waits until knaif has more release
history — their programme favours established projects. Nothing in *this* plan depends on it.

Two items there are worth knowing about from here:

- **S0 is not blocked and is not signing**: submitting artifacts to Microsoft's Security Intelligence
  portal addresses the antivirus half of F5, needs no certificate, and should become a standing
  release step.
- **W3's `AppPublisher` reconciliation** is carried in that plan's S3, since it can only be done once
  a certificate subject exists.

### - [x] W5 — Docs *(shipped 2026-07-27)*

- [x] [`installers/windows/README.md`](../../installers/windows/README.md) — its *Uninstall* section
  describes upgrade behaviour that F1 shows can silently fail; add the detection dependency. Its
  *What the installer does* section also states the winget tools are optional, which F2 makes untrue
  in the shipped wizard — correct it when W1 lands, not before.
- [x] [`docs/RELEASE.md`](../RELEASE.md) — the new verification protocol below. *(Signing and the
  Microsoft submission step are [code-signing](2026-07-27-code-signing.md) S3's, not this plan's —
  do not wait for them to close W5.)*

### - [x] W6 — Regression guard: lint `knaif.iss` in the test suite *(makes F2 non-recurring)* *(shipped 2026-07-27)*

W1 ends in *"re-verify by eye"* — a human step that cannot run in CI, on a page the *Verification
protocol* proves cannot be probed silently. F2 shipped in v1.0.1 precisely because nothing checked
it. **The bug class is fully decidable from the text of the script**, so it does not need the wizard:

- [x] **[`python/core/tests/test_installer_iss.py`](../../python/core/tests/test_installer_iss.py)** —
  16 assertions over a small Inno reader (sections, `\`-continuations, `;`-splitting that honours
  double quotes exactly as Inno does):
  - every task `Name:` containing `\` has its parent declared in `[Tasks]` *(this is exactly F2)*,
    **plus** a second test that dependency tasks stay **flat** — the first would be satisfied by
    adding a `deps` parent, and that "fix" re-breaks the defaults the moment anyone ticks it;
  - every `Tasks:` and `Components:` filter resolves to something declared, in `[Files]`, `[Run]`,
    `[Registry]` and `[Tasks]`;
  - every `Check:` names a function defined in `[Code]` (string literals stripped first, Inno
    builtins whitelisted);
  - **the winget offers match `skills/*/skill.yaml` in both directions** — same tools, same command
    lists, `ShouldInstallAll` iff `all_required`, and **the task's default checked state against the
    tool's `required` flag**, which is the assertion F2 actually needed;
  - `[InstallDelete]` covers every `{app}\…` directory `[Files]` writes, never targets `{app}`
    itself, and never reaches outside it *(F9, and the reviewer's coverage ask)*;
  - every parameter parses as `Key: value` — the guard against an unquoted `;` inside a value, which
    is how `ShouldInstallAll('ffmpeg;ffprobe')` would silently truncate.
- [x] **Contract source corrected:** the lint parses `skills/*/skill.yaml`, **not** `deps.rs` — see
  the W1 note above; the alias list in `deps.rs` is a `#[cfg(test)]` fixture. It reads `deps.rs` for
  one thing only: that the `KNAIF_<CMD>_BIN` env key it builds is the one the Pascal probe reads.
- [x] Cross-check the shape against [`test_version_consistency.py:18-46`](../../python/core/tests/test_version_consistency.py#L18-L46),
  which **already parses `knaif.iss`** for `AppVersion` — the precedent, the path constant and the
  regex idiom exist; this is an extension of an established test, not a new mechanism.
- [x] **Verified by mutation, not by passing.** A lint that has never failed is not evidence. All 14
  mutations were injected into `knaif.iss` / `skills/documents/skill.yaml` one at a time and every
  one was caught: nesting `depsgs` under an undeclared parent (F2 as shipped), dropping its
  `unchecked` flag, `All`→`Any`, `,`→`;` in a command list, a dropped alias, an undefined `Check`
  function, deleting an `[InstallDelete]` entry, widening one to `{app}`, pointing one at
  `~/.knaif`, a misspelled task filter, a misspelled component filter, a tool added to `skill.yaml`
  alone, a `required:` flip with no installer change, and a section-header typo.

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
- **Always compile scratch builds with a throwaway `AppId`** — `ISCC /DAppIdGuid=<throwaway>`
  (added 2026-07-26; `AppIdGuid` is overridable, and an overridden build labels itself
  *"(TEST BUILD)"* in Add/Remove Programs). Inno treats two builds sharing an `AppId` as the **same
  application** and derives the same `{AppId}_is1` uninstall key from it. Without this, a scratch
  install registers itself against the production key — and the teardown step below then deletes the
  *real* install's Add/Remove registration, **recreating F1 by following this protocol**. Never
  delete the production `{AppId}_is1` key as test cleanup.
- **Never run the uninstaller with `/SUPPRESSMSGBOXES`.** `CurUninstallStepChanged` asks whether to
  delete `~/.knaif`, and `SuppressibleMsgBox` answers **IDYES** — a "cleanup" of a test install
  destroys the real model store and costs a 2.5 GB re-download. Tear down by hand instead: remove
  the scratch dir and delete the **throwaway** build's `{AppId}_is1` key.
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
