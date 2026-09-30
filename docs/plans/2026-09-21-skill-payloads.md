# Skill payloads — the libraries knaif fetches for itself

**Status:** Planning · **Created:** 2026-09-21 · **Completed:** —

> **Status note:** for knaif 1.2.0 the owner chose to **bundle** PDFium (2026-09-25,
> [release-1.2](2026-09-25-release-1.2.md) R0), so it no longer gates OCR on this plan. The payload
> tier itself stays open for later.

**Goal:** A third delivery tier for skill dependencies: native libraries that knaif downloads on
demand, sha256-pinned, into a directory outside the install. PDFium is the first case — it is what
makes `documents` able to OCR a PDF or compress a scanned one, which the shipped artifact has never
been able to do.

## The gap

`skills/documents/skill.yaml` declares Ghostscript, LibreOffice and Tesseract under
`dependencies.external_tools` — tier 3, "the user installs it, we detect it". That is right for
those three. But PDFium is in no tier at all: `feats_for_kind` now compiles the code path in
([per-backend build profiles](2026-09-21-per-backend-build-profiles.md), P6), and
`installers/package.sh` stages no library, so the capability is reachable only by someone who
builds from source or hand-places a DLL and sets `$KNAIF_PDFIUM_PATH` — an environment variable
documented nowhere outside `pdfium_backend.rs`.

The result today, in a downloaded artifact:

```
$ knaif run documents "ocr scan.pdf"
could not load the PDFium library. Put the pdfium runtime library next to the
executable or set KNAIF_PDFIUM_PATH to the folder containing it.
```

Actionable, but only just. Nothing tells the user where to get it, and nothing can, because no
version has been chosen, hashed or published.

## Why a tier exists between "compiled in" and "user installs"

The dividing line is **not size**. It is whether knaif can fetch the thing legally, pin it, and
version it:

| Tier | Delivery | What belongs there | Today |
|---|---|---|---|
| **1 — compiled in** | in the binary | pure-Rust, small, no runtime dep: `lopdf`, `zip`, `quick-xml`, `calamine`, `image`, `regex` | working |
| **2 — payload** | knaif downloads on demand | permissively licensed native libs knaif can pin and host: **PDFium** | **missing** |
| **3 — external tool** | user installs, knaif detects | licence-encumbered, huge, or already ubiquitous: ffmpeg, Ghostscript, LibreOffice | `external_tools` + `doctor` |

ffmpeg sits in tier 3 because of licensing and ubiquity, not because it is large. PDFium sits in
tier 2 because it is BSD, self-contained, and enables a whole capability class — and because when
knaif owns the fetch it can *offer* ("OCR needs the PDF renderer, install it? [Y/n]") instead of
erroring.

### The size argument, settled with a number

The default artifact is 29 MB. **The model is 2.33 GB**, and knaif does nothing without one. The
first payload, measured, is **6.92 MB** — 0.3% of that model, and one eighth of the Vulkan backend
already shipping unconditionally. Size is therefore not the reason to keep something out of the
artifact; *licence and staleness* are, which is exactly why CUDA (≈½ GB, ABI-coupled) is a payload
and Vulkan (58 MB, the fallback path) is not.

## Settled design decisions

### S1 — Vulkan stays bundled, and this plan does not change that

The obvious question once a payload mechanism exists is "should Vulkan be one too". No.

A fallback that requires a second download is not a fallback. Make Vulkan opt-in and an AMD or
Intel user's first run is CPU-only and slow with no indication why — the exact failure
[NATIVE.md §5.5](../NATIVE.md) already identifies for NVIDIA users ("one CPU-speed request and may
reasonably conclude the product is broken"). That would fix waste for one population by creating
the failure for three.

The real distinction, worth writing down because it keeps being rediscovered: **CUDA is additive on
hardware that already has a working path; Vulkan *is* the working path.**

### S2 — Payloads are declared in `skill.yaml`, beside `external_tools`

`dependencies.external_tools` already exists, is parsed by
[`knaif-core/src/deps.rs`](../../native/crates/knaif-core/src/deps.rs), and drives both `doctor` and
the installer's component tree. Payloads get a sibling key, so one declaration point serves both
mechanisms and `doctor` can report the whole dependency picture in one place.

```yaml
dependencies:
  payloads:
    - name: pdfium
      required: false
      provides: [rasterize, ocr_pdf, compress_pdf_lossy]
      why: "PDF page rendering — OCR of PDF inputs and lossy compression without Ghostscript"
  external_tools:
    - name: ghostscript
      ...
```

### S3 — One store, generalised from `BackendStore` — not a second copy of it

`BackendStore` already implements exactly this: fetch → `.part` → hash → rename, per-file sha256,
platform-keyed entries, stage-then-swap of the directory, install receipts, and a `list` / `state` /
`verify` / `install` / `remove` surface. Writing a second one would be the third store in the tree
(`ModelStore`, `BackendStore`, and this), each drifting separately.

The work is to lift the generic half into a `PayloadStore` and keep `BackendStore` as the
backend-flavoured face of it, so the CUDA path is untouched behaviourally. **If that refactor turns
out to be larger than writing the skill store alongside it, stop and write it alongside** — the
goal is one mechanism, not one abstraction at any cost.

### S4 — The version binding is looser than a backend's, stricter than a model's

The backend manifest is deliberately strict: a `ggml-cuda` whose ABI does not match the binary
loading it is undefined behaviour, so an install receipt naming a different knaif release is
refused. A model is the opposite — forgiving, keyed on its own filename.

A skill payload sits between. PDFium's C API is stable across versions and `pdfium-render` binds it
dynamically, so a payload installed under 1.1.0 should keep working under 1.2.0. Therefore:

- The manifest **pins an exact version and sha256** — never resolves "latest".
- The receipt **records** the installing knaif release but does not refuse on mismatch by default.
- An explicit `min_knaif_version` (or a `breaks_below`) is available per entry for the day a
  payload genuinely stops being compatible.

### S5 — `~/.knaif/payloads/<name>/`, outside the install directory

Same reasoning that put `~/.knaif/backends` there: it survives an app upgrade, keeps `install`
elevation-free, and works when the install directory is read-only.

`pdfium_backend.rs` already searches `$KNAIF_PDFIUM_PATH` (a directory) first, so wiring is a
search-path addition, not a redesign.

### S6 — Every skill degrades to something useful without its payload

`documents` already has the right shape: the lossless structural operations — merge, split, rotate,
remove, reorder, encrypt — are pure `lopdf` and need nothing. Only rasterize and PDF OCR need
PDFium. That is the pattern to hold future skills to.

The corollary is about the error, not the code: **a missing payload must name the one command that
fixes it.** `knaif payload install pdfium`, not "set an environment variable".

### S7 — The offer is triggered by use, not by first run

CUDA's nudge fires at startup because a slow first inference is the failure it prevents. A missing
PDF renderer has no equivalent — most sessions never touch a PDF. So the offer belongs at the point
of use, when the intent is known and the user has already asked for the thing that needs it.

`$KNAIF_NO_PAYLOAD_OFFER` suppresses it, mirroring `$KNAIF_NO_CUDA_NUDGE`. A non-interactive run
never prompts; it errors with the command.

### S8 — Licence texts travel with the bytes

Lifted verbatim from the backend manifest's own rule: under loose-file publishing the release
*page* is not what reaches a user's disk. PDFium's BSD notice ships **into the payload directory**,
alongside an entry in `NOTICE` and `docs/PROVENANCE.md`.

## The work

### [ ] T0 — Measure PDFium before committing to the tier — *first figure in, rest pending*

**First figure measured, 2026-09-21: 6.92 MB.** That is `pdfium.dll` (win-x64) as bundled by
`pypdfium2` 5.13.0, already present in this repo's venv — the same `pdfium-binaries` lineage the
Rust side would pull. Earlier estimates in this plan's discussion said "~10 MB"; the real number is
smaller, and the comparison is worth stating plainly:

| | size | ships by default? |
|---|---:|---|
| **PDFium** | **6.92 MB** | no |
| `ggml-vulkan.dll` | 57.96 MB | yes |
| the model | 2.33 GB | user pulls it |

PDFium is **8× smaller than the Vulkan backend that already ships unconditionally**, and 0.3% of
the model download. Whatever the right answer is here, it is not driven by size.

That measurement raises a fair question this plan should answer rather than dodge: **if it is only
7 MB, why is it a payload at all rather than bundled?** The honest reasons are that it is dead
weight for every user who never opens a PDF, that a payload can be re-pinned without a knaif
release, and that `documents` is still `in-progress` natively — but "bundle it" is a defensible
alternative, and T6 is the moment to decide, not now.

Still to measure before T6: the Linux and macOS builds, and whether a single platform's asset is
one file or several.

Also decide, and record: **mirror the binaries as knaif release assets, or link upstream?** The
backend manifest's precedent is to mirror (NVIDIA's redistributables ride a dedicated, never-deleted
`redist-cuda-13.3` tag). Mirroring is what makes the sha256 pin meaningful — an upstream asset can
be replaced under the same URL.

### [ ] T1 — `dependencies.payloads` in `skill.yaml`

Schema, parser beside `parse_external_tools`, and a test that a bundle declaring payloads loads in
both runtimes. Add the declaration to `skills/documents/skill.yaml`. **Core, TDD.**

Nothing consumes it yet; this is the contract both later halves read.

### [ ] T2 — `PayloadStore`, generalised from `BackendStore`

Lift the generic fetch/verify/install/remove machinery so both stores share it. `BackendStore`'s
public surface and behaviour must not change — its tests are the regression gate, and
`knaif backend install cuda` must still work identically. Per S3, if the refactor grows beyond the
value it returns, write the skill store alongside and record why.

### [ ] T3 — `contracts/skills/payload-manifest.yaml`

Platform-keyed entries with per-file `url` / `sha256` / `size_bytes`, mirroring the backend
manifest's shape and its `status: unpublished` guard, so an entry with a `TODO` URL is refused
rather than fetched. Ships inside the artifact beside the other contracts — a manifest nobody has
is a payload nobody can install.

### [ ] T4 — `knaif payload list | install | verify | remove`

Mirrors `knaif backend`, including a `--json` output for the same reason the backend command is
gaining one: something has to parse it.

### [ ] T5 — Wire PDFium to the payload directory

`PdfiumRasterizer::new`'s search order gains the payload directory, ahead of the exe directory and
the system library. The three `bail!` sites in `compress.rs` / `ocr.rs` / `pdfium_backend.rs` change
their message to name `knaif payload install pdfium`.

### [ ] T6 — Publish the PDFium payload

Per-platform assets, real sha256s, BSD licence text staged into the payload directory, `NOTICE` and
`docs/PROVENANCE.md` entries, manifest flipped to `status: published`. A release-readiness test in
the shape of `test_backend_manifest_release_ready.py`.

### [ ] T7 — The use-time offer

Per S7: at the point a tool needs a missing payload, offer to install it; suppressed by
`$KNAIF_NO_PAYLOAD_OFFER` and never shown non-interactively. Extend `doctor` to report payloads
beside external tools, so one command answers "what is missing and how do I fix it".

### [ ] T8 — Decide Tesseract's tier, on the criteria rather than by default

Tesseract is currently tier 3 (`external_tools`, winget/brew/package_manager) and that may be
correct: it is a CLI binary many users already have, and the platform package managers handle it
well. But it is also Apache-2.0 and fetchable, which is the tier-2 test.

**This is a decision task, not an implementation task.** Two cases are what prove a mechanism is
general, so if Tesseract qualifies it should move; if it does not, record why, and the next
candidate skill supplies the second case instead. Do not promote it merely to have two.

### [ ] T9 — Docs and tests

`docs/NATIVE.md` gains a payloads section beside §5.3's loadable backends; `skills/documents/SPEC.md`
states which operations need which tier; `AGENTS.md` names the three tiers under skill authoring, so
the next skill author picks a tier deliberately.

## What this is not

- **Not a change to Vulkan or CUDA.** S1. The backend payload path is touched only by T2's
  refactor, and only in a way its own tests must not notice.
- **Not the first-run sequence.** Today an NVIDIA user does three separate things: download knaif,
  get told to install CUDA, pull a 2.33 GB model — two of which knaif performs itself. Merging
  those into one guided step is the highest-leverage change in this area and is **independent of
  this plan**. It deserves its own.
- **Not a plugin system.** Payloads are declared data fetched by name, not third-party code loaded
  at runtime. Nothing here executes anything the manifest did not pin by hash.
- **Not a way to avoid tier 3.** ffmpeg, Ghostscript and LibreOffice stay where they are.

## Risks

- **The refactor in T2 touches a shipped install path.** `knaif backend install cuda` is how CUDA
  reaches users; its tests are the gate, and S3 gives explicit permission to abandon the refactor
  rather than force it.
- **Building infrastructure for one case.** There is exactly one real payload today. T8 is written
  to resist inventing a second, and the honest outcome may be "PDFium only, for now" — which is
  still worth it, because the alternative is a capability that ships unreachable.
- **A payload that cannot be reached is worse than one that does not exist.** An offline or
  firewalled user must get the same clear error and a documented manual route — drop the file in
  the directory — which is also how the install path gets debugged.
- **Licence review is not optional.** T6 is a publishing task with legal content; the dependency
  licence policy (Apache-2.0-compatible, no GPL/AGPL bundling) governs what may be mirrored at all.

## When to do this

Not next. In order:

1. **Land the build-profiles work** (its T5 plus the L4 re-run, which belong with the v2-promotion
   branch's outstanding L3/L4 runs).
2. **Return to the [skill workbench](2026-09-21-skill-prompt-workbench.md)** — the original
   destination, now with real `release-vulkan` and `release-cuda` builds to select between.
3. **Then this**, or the first-run sequence, whichever the product needs more.

The argument for not rushing it: PDFium affects two operations of one skill, in a runtime whose
`documents` status is still `in-progress`. The argument for not deferring it forever: the shipped
artifact currently advertises OCR it cannot perform, and every release that goes out that way is a
release where the feature is a claim rather than a capability.
