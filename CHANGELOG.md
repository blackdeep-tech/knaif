# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — unreleased

> **Not finished.** The CUDA opt-in surface is written up below, but the payloads are **not yet
> published**: `contracts/backends/backend-manifest.yaml` still says `status: unpublished` and every
> `url` is a placeholder, so `backend install cuda` refuses with an explanation rather than
> downloading. Before tagging: build both payloads, upload the assets, fill in the manifest, flip
> the status, and re-run the three backend cases against the *packaged* payload on each OS. Set the
> release date at the same time. See
> [`docs/plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md`](docs/plans/2026-07-17-post-v1-ci-and-cuda-opt-in.md),
> Workstream U, and `docs/RELEASE.md` §7 for the publish order.

**The first knaif release with downloadable binaries.** No GitHub Release existed before it: 1.0.0
and 1.0.1 published the `knaif` wheel to PyPI, and the Windows artifacts built alongside them were
never uploaded. The Linux tarball and AppImage below are therefore not newly *supported* — they are
the first artifacts of any kind to reach a user, and Windows is in the same position.

**Planning behaviour is unchanged from 1.0.1.** Nothing in the pipeline that turns an utterance into
a validated plan was touched — no prompt, validation, expansion or skill change. What makes this a
minor release rather than a patch is the new `knaif backend` subcommand; everything else is packaging
correctness and artifacts that should always have shipped.

Two consequences worth stating plainly: every artifact here is unproven in the field by definition,
and every defect fixed below was found by looking rather than by a bug report — the Windows
artifact **could not start on a machine without Visual Studio installed**, and nothing in the
release process could have noticed, because every check ran on the box that built it.

**The supported floor is now measured and stated**, rather than inherited from whichever machine
cut the release:

| Platform | Floor |
|---|---|
| Windows | x64, **Windows 10 or later** (verified on 11 24H2; the floor itself rests on Microsoft's support statement for the UCRT and the v14 runtime, not on a run of ours) |
| Linux | x64, **glibc 2.34+ *and* `libstdc++` with `GLIBCXX_3.4.30` / `CXXABI_1.3.13`** — Ubuntu 22.04+, Debian 12+, Fedora 36+, Mint 21+ |
| Not supported | RHEL / Rocky / Alma 9 — glibc 2.34 is new enough, its `libstdc++` is one version short |

`libstdc++`, not glibc, is the binding constraint on Linux; any support claim quoting a glibc
number alone is measuring the wrong thing.

### Added

- **`knaif backend install cuda`** — the opt-in NVIDIA CUDA backend, in one command. ~668 MB,
  needs an R580+ driver, and it takes effect on the next run; `knaif backend list|verify|remove`
  complete the set. The payload itself has worked since the native branch closed, but the only way
  to get it was to copy files into `~/.knaif/backends` by hand — and on Windows there was no payload
  to copy at all. Both are fixed here.

  **This matters most on the newest NVIDIA cards.** On Blackwell (RTX 50xx) the bundled Vulkan
  backend generates at roughly CPU speed — ~5.7 tok/s against the CPU's ~5.9 on knaif's own
  workload. That is not a slower option, it is a product that reads as broken, and it is why this
  release waited for CUDA rather than shipping without it. On older NVIDIA cards CUDA is faster and
  genuinely optional, and knaif says so in those terms rather than the alarming ones.

  knaif offers it on first run when it finds an eligible GPU, and the Windows installer runs the
  same command from a task it shows only when the GPU and driver already qualify — and, because it
  is shown only there, one that is checked by default. A driver below the CUDA 13 floor gets an
  update hint and no offer — downloading 668 MB that then fails to load is the least debuggable
  outcome available.
  `KNAIF_NO_CUDA_NUDGE` silences the offer — but not the report that an installed payload is being
  ignored, which is about a download the user already paid for rather than one being suggested.

  The backend is ABI-coupled to the binary that loads it, so an upgraded knaif **refuses** a payload
  left behind by an earlier release rather than loading it, and says how to update it. That check
  had to exist here: `~/.knaif/backends` lives outside the install directory by design, survives an
  upgrade, and is scanned first, so no install-time mechanism could have caught it.

  Copying the payload in by hand still works and stays documented as the fallback.
- **Linux x64 artifacts** — `knaif-1.1.0-linux-x64.tar.gz` and `knaif-1.1.0-linux-x86_64.AppImage`,
  both carrying CPU and Vulkan backends.
- **A pinned Linux release container** (`installers/linux/Dockerfile`, `just package-linux`). It
  builds a git commit checked out inside the container rather than a mounted worktree, so the
  runtime floor is a property of the artifact instead of the builder's distro, and no uncommitted
  change can reach a published binary. Every input is pinned: base image digest, an Ubuntu apt
  snapshot, LunarG's Vulkan stack, `appimagetool` by checksum, and the Rust toolchain. A fixed apt
  snapshot receives no security updates — the trade is recorded in the Dockerfile and the pins get
  bumped every release.
- **Portability guards that fail where the mistake is made.** `scripts/check_pe_imports.py` parses
  every Windows binary's PE import table and `scripts/check_elf_deps.py` audits `DT_NEEDED` plus
  the maximum required `GLIBC_` / `GLIBCXX_` / `CXXABI_` symbol version, each against an explicit
  baseline of what the OS provides. Both run on any machine and are now required packaging steps.
  `installers/linux/check-floor.sh` proves the Linux floor in both directions — it must pass at the
  floor and fail below it.
- **`knaif.exe` carries an icon and VERSIONINFO** (`apps/cli/build.rs`), and `setup.exe` fills its
  Properties → Details tab — the tab a cautious user checks precisely because the build is unsigned.
- **A lint over `knaif.iss`** (`python/core/tests/test_installer_iss.py`) asserting that the
  installer's winget offers match `skills/*/skill.yaml` in both directions, that task and component
  filters resolve, and that `[InstallDelete]` never widens beyond `{app}`'s payload directories. It
  was verified by injecting all 14 mutations it claims to catch.

### Fixed

- **The Windows artifact did not start on a clean Windows machine.** All 13 staged binaries import
  the VC++ runtime — `VCRUNTIME140`, `VCRUNTIME140_1`, `MSVCP140`, and `VCOMP140`, the OpenMP
  runtime every `ggml-cpu-*` variant links — and none of it was staged. On a box without Visual
  Studio the process exited `0xC0000135` printing nothing at all. All four now ship in `bin\`
  beside the exe, where Windows resolves them first. The redistribution grant covers them and adds
  no attribution obligation.
- **The Linux artifact was missing `libgomp.so.1`** for the same reason — the GNU OpenMP runtime
  ships with GCC, not with a base system, so the CPU backends failed to load on any machine without
  a compiler. Now staged.
- **`NOTICE` was absent from every artifact on every OS**, an Apache-2.0 §4(d) obligation and the
  file carrying the Qwen3 derivation attribution for the models knaif downloads. It now ships in
  the staged tree, the zip, the tarball, the AppImage and the installed tree, and `smoke.sh`
  asserts it. No published artifact was ever affected — there were none.
- **The Windows installer pre-checked Ghostscript, LibreOffice and Tesseract**, installing AGPL
  software and a ~350 MB office suite against the script's own stated intent. Four dependency tasks
  named a parent task that was never declared, so they rendered as children of a checked task,
  which defeated their `unchecked` flags and discarded their group heading. The names are flat now.
  A silent install never builds the task tree, which is why nothing caught this.
- **The 2.5 GB model download was offered even when the GGUF was already on disk.** The `Check` sat
  on the `[Run]` entry but not the `[Tasks]` entry, so the wizard promised a download it would then
  correctly skip. The task also now discloses that setup waits for the transfer.
- **The installer's dependency probe disagreed with the runtime's.** It tested one bare name plus
  `.exe`, while `knaif skills deps` resolves `$KNAIF_<CMD>_BIN` first, honours `PATHEXT`, accepts
  command aliases (`gs` / `gswin64c` / `gswin32c`), and requires *every* command when a skill sets
  `all_required` — so a box the runtime considered satisfied still got a redundant winget install.
- **The license page defaulted to "I do not accept."**
- **Upgrades over an existing install.** An upgrade while `knaif.exe` was running hit a locked
  binary and silently deferred to reboot (`AppMutex`, matched by a mutex the CLI now holds); a
  deselected component or a dropped file kept its payload from the previous install
  (`[InstallDelete]` over the staged directories, which sit outside `~/.knaif` by design); and a
  machine whose uninstall registry key had gone missing was offered no way out — setup now detects
  an orphaned `unins000.exe` and offers to run it. `UninstallDisplayIcon` points at the exe.
- **Add/Remove Programs showed a dead publisher link and named no entity.** Publisher, homepage,
  support, updates and contact are now the real values.
- **The installed tree said nothing about what knaif is or who maintains it.** `README.txt` now
  leads with identity — what it does, the maintaining entity, the license, and where bugs go —
  before the quick start, on all platforms. The wizard's final page says to open a new terminal
  and run `knaif skills deps`, since the PATH change cannot reach an already-open shell.
- **The AppImage shipped without `NOTICE` and with a 1×1 transparent icon**, because it is
  assembled by a second script that the earlier `package.sh` fix never reached. `smoke.sh` now
  accepts an `.AppImage` and runs the same assertions against it.
- **`LICENSE` and `NOTICE` named the wrong copyright holder.** Both root files and both
  `python/core/` copies read *"knaif contributors"* — the convention for a jointly-owned project,
  which contradicted `NOTICE`. All four now read **Blackdeep Technologies Ltd.**

### Known limitations

Unchanged from 1.0.0: no macOS artifact, no published CUDA artifact (Vulkan covers NVIDIA
hardware; the manual payload route is Linux-only), Windows binaries are unsigned so SmartScreen
will warn, the `io` skill is stale, and external tools are not bundled.

## [1.0.1] — 2026-07-25

Bug-fix release. All fixes are in the Python runtime; the native runtime is unchanged and
its version tracks the workspace. The `knaif.cli` SDK did not validate arguments on Python
3.10, the oldest interpreter `requires-python` advertises — found by a clean-room run on the
version floor rather than the development environment (3.13).

### Fixed

- **CLI SDK arguments are now schema-validated on Python 3.10.** `Arg` and `Opt` were
  unhashable dataclasses, so `get_type_hints(include_extras=True)` raised on 3.10 (which
  hashes `Annotated` metadata; 3.11+ does not). `build_registry` caught the error and fell
  back to no schemas, so every argument silently skipped validation. `Arg`/`Opt` are hashable
  again, and the fallback now warns instead of degrading in silence.
- **Optional argument types resolve regardless of wrapper order.** Python 3.10 re-wraps a
  `None`-defaulted parameter as `Optional[Annotated[…]]`, putting the union outside
  `Annotated` — the reverse of 3.11+. Annotation unwrapping now peels both layers in any
  order, so `X | None` and `Optional[X]` derive the same schema on every supported version.
- **Model-load and Ollama failures no longer print to stdout.** They go through the warnings
  machinery, so a failed load can't contaminate the output of a CLI that embeds knaif. The
  unreachable-Ollama message also no longer carries the raw urllib3 exception chain.
- **The test suite collects on Python 3.10.** A test imported `tomllib` unconditionally
  (stdlib only from 3.11); it now falls back to the `tomli` backport, added to the `dev` extra.

## [1.0.0] — 2026-07-20

First public release. knaif turns a natural-language request into a **validated JSON
action plan** and executes it through skill packages. The model only ever proposes a
plan; every step of validation, safety classification, expansion, confirmation, and
execution is deterministic code.

### Runtimes

- **Python library and SDK** (`knaif`) — the authoring, evaluation, and training runtime.
  `CommandAgent` drives the full pipeline: prompt construction → inference → plan parse →
  validation → intent expansion → optimization → variable resolution → execution.
- **Native CLI** (`knaif`, Rust) — the shipped runtime, with `skills`, `models`, `plan`,
  and `run` commands. It is a **port, not a rewrite**: both runtimes read the same
  language-neutral YAML contracts and the same skill bundles, so the same utterance
  renders the same command on either side. Cross-runtime parity is enforced by
  `just parity <skill>` against shared contract fixtures.

### Skills

Skills are self-contained bundles under `skills/<name>/` — declarative YAML at the top,
per-language implementations beneath.

| Skill | Runtimes | Status |
|---|---|---|
| `ffmpeg` | Python + native | supported |
| `documents` | Python + native | supported |
| `io` | Python only | stale — under rebuild |

### Developer SDK

`knaif.cli` adds a natural-language front end to an existing CLI. Declare commands with a
click-like decorator, or wrap an existing `click.Group` with `nk.from_click(cli)`.

### Models

Two fine-tuned models, downloaded on first run rather than bundled:

| Model | Quant | Base |
|---|---|---|
| `knaif-qwen3-4b-v1` | Q4_K_M | `Qwen/Qwen3-4B` |
| `knaif-qwen3-1.7b-v1` | Q6_K | `Qwen/Qwen3-1.7B` |

Both are derivative works of Qwen3 (Apache-2.0) — see [`NOTICE`](NOTICE) and
[`docs/PROVENANCE.md`](docs/PROVENANCE.md). Downloads are pinned to a commit SHA and
verified against a recorded SHA-256. Mock inference and an Ollama backend need no
download at all.

### Safety model

- The model emits only `{ "plan": [...] }` — unknown tools and unsupported arguments are rejected.
- Sandbox-sensitive paths are validated before execution **and again after variable resolution**.
- `dry_run=True` previews a plan without side effects.
- Steps marked `safety_category: destructive` require explicit confirmation.
- Preview gates can route through a caller-supplied `confirmer` callback.

### Evaluation

Per-skill corpora and a committed acceptance bar live inside each bundle. Verifiers run
cheapest-first — `cheap` (routing only) → `output_diff` → `success` (real execution graded
against each row's criteria). `success` is the honest metric.

### Platforms

| Platform | Artifacts |
|---|---|
| Windows x64 | portable `.zip`, per-user installer `.exe` |
| Linux x64 | portable `.tar.gz`, `.AppImage` |

Each default artifact carries **CPU and Vulkan** backends.

### Known limitations

- **macOS is not supported at v1.** The Rust core is cross-platform and the inference
  backend trait keeps macOS reachable; only packaging is deferred to a fast-follow.
- **No CUDA artifact.** CUDA is a manual, opt-in build. The default artifact's Vulkan
  backend covers GPU acceleration on NVIDIA hardware.
- **Windows builds are unsigned**, so SmartScreen will warn on first run.
- **The `io` skill is stale** and under rebuild; `ffmpeg` and `documents` are the
  production skills.
- External tools are **not bundled** — FFmpeg in particular must be installed separately
  and remains under its own license.

1.0.0 and 1.0.1 have no GitHub Release — they shipped only as a PyPI wheel, so their links
point there.

[1.1.0]: https://github.com/blackdeep-tech/knaif/releases/tag/v1.1.0
[1.0.1]: https://pypi.org/project/knaif/1.0.1/
[1.0.0]: https://pypi.org/project/knaif/1.0.0/
