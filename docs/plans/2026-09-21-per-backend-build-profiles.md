# One directory per backend — cargo profiles for the native builds

**Status:** Planning · **Created:** 2026-09-21 · **Completed:** —

**Goal:** Give each native build kind (`base` / `cpu` / `vulkan` / `cuda`) its own cargo profile,
so its binary and its staged llama/ggml libraries live in their own directory instead of
overwriting each other in `target/release/`.

This began as a prerequisite for
[the skill workbench](2026-09-21-skill-prompt-workbench.md) — its compute selector needs two
binaries to exist at once, which today is impossible. It is worth doing on its own merits: the
shared directory is the documented root cause of one P1 and one P0 packaging defect, and the
current defence against both is a hand-maintained set of guards plus a manual cleanup step in
`docs/RELEASE.md`.

## Why the collision exists

Cargo's artifact path is `target/<profile>/<bin-name>`. Features are an **input** to the build,
not part of the artifact's identity — cargo's feature model is additive and unified across the
whole graph, so "the release build of `knaif-cli`" is one artifact no matter which features
produced it. Two feature sets, one destination, last build wins.

Cargo *does* hash features into per-unit metadata, which is why one
`target/release/build/llama-cpp-sys-2-*/out` directory exists per feature set and they all
coexist. The top-level binary is the exception: it is hard-linked to the stable unhashed name so
callers can find it.

The staged shared libraries collide for a second, separate reason. `llama-cpp-sys-2`'s build
script derives its staging destination from `OUT_DIR`:

```rust
// llama-cpp-sys-2-0.1.150/build.rs:88
fn get_cargo_target_dir() -> Result<PathBuf, Box<dyn std::error::Error>> {
    let out_dir = env::var("OUT_DIR")?;
    let path = PathBuf::from(out_dir);
    let target_dir = path.ancestors().nth(3).ok_or("OUT_DIR is not deep enough")?;
    Ok(target_dir.to_path_buf())
}
```

`OUT_DIR` is `target/<profile>/build/<pkg>-<hash>/out`, so `nth(3)` is `target/<profile>/`. Every
feature set therefore hard-links `llama.dll` / `ggml.dll` / `ggml-base.dll` / `llama-common.dll`
into the same place, with:

```rust
// build.rs:1201
if !dst.exists() { std::fs::hard_link(asset.clone(), dst).unwrap(); }
```

**That `nth(3)` is the whole opportunity.** Change the profile and the destination moves with it,
with no patch to the crate and no `CARGO_TARGET_DIR`.

## What this has already cost

Both defects are recorded, not hypothetical.

| | Defect | Root cause as recorded |
|---|---|---|
| **C1** (P1) | `just package-linux --kind=cpu` after a `vulkan` build panics: `hard_link … AlreadyExists` | *"Every feature set gets its own `llama-cpp-sys-2` out dir but they all hard-link into the same `target/release/`… a stale SONAME symlink left by the other kind reads as absent while still occupying the name."* — [portable-builds C1](2026-07-27-portable-builds.md) |
| **C2** (P0) | A `--kind=cpu` run built an AppImage **from the leftover `vulkan` staging tree**, smoke-tested it, and printed `PASS` | Staged-dir glob selected the wrong tree. *"A mislabelled artifact that reports success."* |

The surviving defences are all compensating controls for the shared directory:

- [`package.sh:151`](../../installers/package.sh#L151) — *"Cargo overwrites target/release/knaif on every
  build"*, then two `exe_imports_llama` guards, one of which was
  *"hit while testing this very change"*.
- [`package.sh:191-195`](../../installers/package.sh#L191-L195) — `out_dir()` cannot pick by mtime and must
  identify a build by the backends it emitted, because *"the dirs are indistinguishable by name.
  Newest-wins silently staged ggml-vulkan into a CPU artifact."*
- [`docs/RELEASE.md:250-252`](../RELEASE.md) — *"Stale lib copies break `build.rs`… Delete them before
  switching kinds."* A manual step, in a release procedure, that a profile makes unnecessary.
- [`build-in-container.sh:122-127`](../../installers/linux/build-in-container.sh#L122-L127) — a comment
  warning that `CARGO_TARGET_DIR` would silently break `package.sh`'s relative paths.

Profiles do not make the guards wrong. They demote them from load-bearing to belt-and-braces,
which is where a guard should sit.

## Settled design decisions

### P1 — Custom profiles, not `CARGO_TARGET_DIR`, not copying the exe aside

Three options were considered:

| | Mechanism | Verdict |
|---|---|---|
| copy the exe aside | `cp target/release/knaif.exe build/knaif-cuda.exe` | **Rejected.** Works only for a statically-linked build. The shipped `dynamic-backends` build is explicitly not self-contained — [`package.sh:20-21`](../../installers/package.sh#L20-L21): *"its core libs … must ship beside it."* A bare-exe registry breaks the moment a packaging-style build is registered. |
| `CARGO_TARGET_DIR` per kind | `target-cuda/`, `target-vulkan/` | **Rejected.** Fully independent trees, but `build-in-container.sh` already documents that it *"would silently break"* `package.sh`, and it moves the `build/` cache out from under every relative path in the repo. |
| **custom profiles** | `--profile release-cuda` → `target/release-cuda/` | **Chosen.** Stable cargo, no env dance, and `get_cargo_target_dir`'s `nth(3)` relocates the staged libraries for free. The `target/` root and every relative path under it keep their shape. |

### P2 — One profile per packaging kind, named after the kind

`package.sh` already has a closed vocabulary of kinds — `base`, `cpu`, `vulkan`, `cuda` — and
`feats_for_kind` already maps each to its feature set. The profiles mirror it exactly, so there is
one name for a build kind across the justfile, the profiles, and the packaging script.

`base` gets a profile too. It is the cheapest build and therefore the one most likely to clobber
something expensive, which is exactly the direction
[`package.sh:176-186`](../../installers/package.sh#L176-L186) says was *"missing"* and had to be guarded
after the fact.

### P3 — The first build of each profile is a full rebuild; flipping afterwards is free

This is the cost, stated plainly rather than discovered later. A new profile is a new unit key for
the entire graph, so nothing is shared with `release` — the first build of each profile compiles
everything, llama.cpp included.

**Measured, not estimated** (T1, this machine, 2026-09-21): the first `release-cpu` build —
264 crates plus a from-scratch llama.cpp — took **1m 13s**. The 15-30 minute figure the justfile
warns about is CUDA-specific and comes from its 183 CUDA translation units; it does not generalise
to the other kinds. Only `release-cuda` is genuinely expensive to stand up.

What it buys, per kind, from then on:

| | today | with profiles |
|---|---|---|
| flip cuda → vulkan | relink, plus a manual wipe of staged libs, or C1 panics | nothing to do |
| both binaries present | impossible | yes |
| wrong-backend staging | prevented by a hand-written sniffing heuristic | prevented by the directory |
| disk | one build tree | one build tree **per kind built** |

`rm -rf target/release-<kind>` is the targeted cleanup, matching RELEASE.md's existing advice to
wipe directories directly rather than trust `cargo clean`.

### P4 — `package.sh` takes `--profile`, defaulting to `release`

Nothing about a hand-run `package.sh` changes unless the flag is passed. The justfile recipes pass
it; an operator following RELEASE.md step by step is unaffected. `BIN` and the `out_dir()` glob
both derive from it.

### P5 — The GPU dev wrappers move to the release profiles

`just native-cuda` and `just native-vulkan` pass no `--release`, so they build into `target/debug/`
today — and clobber each other there exactly as the release builds do. They move to
`--profile release-<kind>`.

This is a deliberate behaviour change: those two recipes exist for *real inference*, the thing a
debug build of llama.cpp is worst at, and both already warn about a long first compile. The
alternative — a parallel set of `dev-cuda` / `dev-vulkan` profiles — doubles the profile count to
protect a build nobody wants. `just native-mock` stays on plain `dev`; it has no llama.cpp in it
and nothing to collide with.

## The work

### [x] T1 — The profiles

Add to the root `Cargo.toml`, which today has no `[profile.*]` section at all:

```toml
[profile.release-base]
inherits = "release"
[profile.release-cpu]
inherits = "release"
[profile.release-vulkan]
inherits = "release"
[profile.release-cuda]
inherits = "release"
```

Verify the mechanism before anything is built on it: one `--profile release-cpu` build with
`--features llama,dynamic-backends`, then assert `target/release-cpu/` holds both the binary and
the four staged libraries, and that `target/release/` was not touched. `release-cpu` is the cheap
kind — prove `nth(3)` relocates staging there before paying for a CUDA compile.

**Done 2026-09-21.** [`scripts/verify_build_profile.sh`](../../scripts/verify_build_profile.sh) is
the assertion, written before the profiles existed and confirmed failing against the unbuilt tree.
It checks the profile directory, the binary, that `llama-cpp-sys-2`'s `OUT_DIR` moved with the
profile, that all four core libs are staged beside the exe, and — per `package.sh`'s rule — reads
the kind from the `ggml-*` backends the build emitted rather than from its name.

Result:

| | `target/release/` | `target/release-cpu/` |
|---|---|---|
| binary | 74,577,408 B, mtime **unchanged** (Sep 16 15:55) | 10,478,080 B, new |
| staged libs | four, mtimes **unchanged** (Sep 11) | own copies of all four |
| `llama-cpp-sys-2` out dir | — | `target/release-cpu/build/llama-cpp-sys-2-*/out` |

A before/after listing of `target/release/` is identical, so the claim under test — that the
profile relocates both the binary and the staged libraries, leaving the shared directory alone —
holds. The two binaries now coexist, which is what C1 made impossible.

The build needed the MSVC environment: **cmake is not on PATH outside a Developer shell**, and the
VS-bundled copy lives under `Common7/IDE/CommonExtensions/Microsoft/CMake/`. T2 must locate it
rather than assume the caller's shell is set up.

### [ ] T2 — `just build-native-kind <kind>`

One recipe mapping kind → features → profile, reusing the same mapping `package.sh`'s
`feats_for_kind` holds, so the two cannot drift. `package-native` calls it instead of its inline
`cargo build`. `native-cuda` / `native-vulkan` gain `--profile release-<kind>` per P5.

### [ ] T3 — `package.sh --profile`

`BIN="target/$PROFILE/$EXE"` and `out_dir()`'s glob become
`target/$PROFILE/build/llama-cpp-sys-2-*/out`. Default `release`, so the un-flagged path is
byte-identical to today.

Keep both `exe_imports_llama` guards and the backend-sniffing `out_dir()`. They stop being the only
thing standing between a release and a mislabelled artifact, but a guard that has fired in anger
twice is not one to delete in the same change that removes its reason to fire.

### [ ] T4 — `build-in-container.sh`

Pass the profile through. Update the `CARGO_TARGET_DIR` comment at lines 122-127: the reason it
warned is now handled, but the warning itself stays true and should say why it is no longer the
only option.

### [ ] T5 — Repoint the L4 lane

`eval_backends.yaml:81` pins `binary: target/release/knaif.exe`. Point it at the profile directory
for the kind the lane is meant to measure, and say in the stanza comment which kind that is —
the lane has no other way to state what it is measuring.

**This marks L4 stale for `ffmpeg`.** The lane's binary path is part of what its evidence
describes, so the recorded verdict stops describing the tree. That re-run is already outstanding
(T8 of the reject/clarify work), so the cost is sequencing, not extra work — but it must not be
discovered after the fact.

### [ ] T6 — Docs

- `docs/RELEASE.md` — the per-OS/kind build commands gain the profile; the *"Stale lib copies break
  `build.rs`… Delete them before switching kinds"* trap is scoped to the legacy shared-directory
  path and marked unnecessary for profile builds.
- `docs/NATIVE.md` — one short section: one directory per kind, and what `rm -rf target/release-<kind>`
  is for.
- Leave the [portable-builds](2026-07-27-portable-builds.md) C1/C2 rows alone. They are the record
  of what happened; a line there pointing here is enough.

### [ ] T7 — Reproduce C1, then fail to reproduce it

The acceptance for this whole plan is that the recorded defect no longer occurs.

1. On the shared `release` profile, build `vulkan` then `cpu` and observe the documented failure —
   or record that a warm cache no longer reproduces it, which is evidence too.
2. On the profiles, build two kinds back to back. Assert both binaries exist, both carry their own
   staged libraries, and neither directory was written by the other build.
3. `just package-native <kind>` for each kind built, with `installers/smoke.sh` on each artifact.

A 15-30 minute CUDA compile cannot be a unit test. This is a rehearsal with a written result, run
once on this machine, recorded in the plan.

### [ ] T8 — Hand back to the workbench

[The workbench plan](2026-09-21-skill-prompt-workbench.md) changes in two places once this lands:

- **D1 / T5** — `workbench.local.yaml` registers **directories**, not bare exe paths, and the
  fallback scan covers `target/release*/` rather than `target/release` plus the cross-compile
  `target/*/release`. The mockup's note 3 ("keep them elsewhere") becomes "each kind already has
  its own home".
- **The risk row** *"Backend comparison needs builds that do not exist yet"* narrows to what it
  should always have said: a Vulkan comparison needs a Vulkan build, and now there is somewhere
  for it to go.

## What this is not

- **Not a change to what gets built.** Same features per kind, same `feats_for_kind` mapping, same
  artifacts. Only the directory moves.
- **Not a fix for the guards.** `exe_imports_llama` and the backend-sniffing `out_dir()` stay.
- **Not `CARGO_TARGET_DIR`.** Every relative path under `target/` keeps its shape; that is the
  point of choosing profiles over a separate root.
- **Not a CI change.** CI runs `cargo clippy -p knaif-cli --features llama` and no feature-matrix
  release build, so it never had the collision.

## Risks

- **T3 and T4 touch the release path.** Mitigated by the default: `package.sh` without `--profile`
  behaves exactly as today, so the change is opt-in per call site, and T7 rehearses each kind
  end to end before anything is cut from it.
- **P5 changes what `just native-cuda` produces** — a release build where it produced a debug one,
  which means one long first compile for anyone who used those recipes. Stated in the recipe
  comment, which already warns about the compile.
- **Disk.** One full build tree per kind built. On a machine that builds all four, that is real.
  `rm -rf target/release-<kind>` is the answer and belongs in NATIVE.md, not in a footnote.
- **T5 marks L4 stale.** Known, sequenced against an outstanding re-run, and named here so it is a
  decision rather than a surprise.
