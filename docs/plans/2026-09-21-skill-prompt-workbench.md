# A skill and prompt workbench — one notebook, both runtimes

**Status:** Planning · **Created:** 2026-09-21 · **Completed:** —

**Goal:** One notebook at `notebooks/skill_workbench.ipynb` for exercising skills and prompts
by hand: either runtime, any model on disk, a chosen compute backend, dry-run or real
execution, and enough measured output — including timing — to form a judgement.

It exists to answer *"is this model or prompt good enough to publish"* interactively, which no
current tool does. The eval suite answers it at corpus scale and takes 25 minutes; the existing
testers answer it for one utterance and are wrong in three specific ways.

## Why not just fix the existing testers

`skills/ffmpeg/notebooks/ffmpeg_skill_tester.ipynb` and its documents twin are stale, and two
of the three faults are not notebook bugs:

1. **`SKILL_DIR = ROOT / "src" / "skills" / <name>`.** `src/` was removed in the four-pillar
   restructure. The last commit touching `notebooks/` (2026-09-13) changed only
   `baseline_reviewer.py`; the `.ipynb` cells predate the move.
2. **`MODEL_CONFIGS` lists only untuned base models** — `Qwen3-4B-Q4_K_M`, Phi-4, gemma. No
   knaif fine-tune at all. The documents notebook claims it "mirrors the eval suite's active
   backend set (`eval_backends.yaml`)"; nothing under `notebooks/shared/` reads that file, or
   `models.yaml`, or the manifest.
3. **The notebook does not show the model the production prompt.** The widget calls
   `agent.infer_stream(...)`, which has **no `registry_override` parameter**, so the model sees
   all 30 tools where production and the eval lane send a retrieved subset of ~8.

(3) is why this is a new notebook plus a small core change rather than a cell edit. Measured on
the first 14 corpus utterances against `knaif-qwen3-4b-v2-q4_k_m.gguf`, **4 of 14 plans differ
(29%)**, and the notebook's prompt makes the model look *worse*:

| utterance | notebook (30 tools) | production (retrieved) |
|---|---|---|
| `re-encode clip.mp4 with libx264 at crf 18` | `quality: visually_good` | `quality: high_quality` |
| `convert clip.mp4 to use hevc codec` | `video_codec: h265` | `video_codec: hevc` |

`prompt.yaml:73` says crf ≤ 20 → `high_quality`, so production is right and the bench is wrong.
A publish decision taken on that bench is taken on a prompt the model never ships with.

## What it must do

- **Runner:** Python or native, selectable, and ideally both at once for a side-by-side.
- **Model:** every version available, not a hand-maintained subset.
- **Backend:** the compute backend, selectable — and *reported as measured*, not as chosen.
- **Mode:** the operator decides dry-run or real execution.
- **Output:** the rendered commands, the artifacts, and statistics including execution time.

## Settled design decisions

Each was measured on this machine on 2026-09-21, not assumed.

### D1 — "Backend" means three things, so the selector has three rows and never uses the word alone

The word covers three unrelated axes in this repo, and conflating them is what made the first
draft of this plan hard to review:

| Row | What it picks | Scope |
|---|---|---|
| **Runtime** | who runs it — Python, native, or both | both |
| **Inference** | what generates the tokens — `llama.cpp`, `ollama` | Python only |
| **Compute** | which chip — i.e. *which build* | native only |

Plus one lever that deliberately crosses both runtimes, below.

**Compute is a build picker, because compute is a compile-time feature.**
`apps/cli/Cargo.toml` declares `cuda = ["knaif-llm/cuda"]` and `vulkan = ["knaif-llm/vulkan"]`,
and `knaif run --help` has no `--backend`. CUDA versus Vulkan is **two builds**, each a full
llama.cpp rebuild, not a runtime toggle.

The dropdown therefore lists **builds that exist**, labelled by what each one reports. An
abstract "Vulkan" entry that silently ran CUDA would be worse than no selector at all.

**Two builds can now coexist**, which when this was written they could not: every feature set
overwrote the same `target/release/knaif.exe` and the same staged llama/ggml libs. Each kind now
builds into `target/release-<kind>/` via `just build-native-kind <kind>` — see
[per-backend build profiles](2026-09-21-per-backend-build-profiles.md). Two consequences for the
selector:

- It registers **directories**, not bare exe paths. A `dynamic-backends` build is not
  self-contained; its core libs sit beside it, so the directory is the unit.
- The label comes from `knaif backend list --json`, not from the path. A profile directory names a
  *kind*, and a kind names a feature set — but nothing stops someone pointing a hand-built binary
  at the wrong directory, so the binary still has the last word.

Two levers do work per run, and both are offered:

- **force CPU** — `KNAIF_N_GPU_LAYERS=0` natively, `n_gpu_layers=0` on the Python side. Verified:
  every layer moves to CPU (`load_tensors: layer N assigned to device CPU`). **This is the one
  control that crosses both runtimes**, and the exception to D1b, because a CPU-versus-GPU
  comparison is worthless if only one side moves.
- **backends dir** — `KNAIF_BACKENDS_DIR`, meaningful only for a `dynamic-backends` build. The
  binary's own JSON reports `dynamic_backends: true`, so the selector reveals a path box
  pre-filled with the reported directory and hides it otherwise. No config-schema growth: it is a
  per-run field, not something `workbench.local.yaml` remembers.

### D1b — Python's compute is *shown*, not chosen

It was decided when the venv was built; changing it means rebuilding `llama-cpp-python`, which a
notebook has no business doing. The panel states it as a fact beside the compute row. The force-CPU
lever above is the single exception.

### D1c — The inference row keeps a second real option

With `mock` excluded (D1d) and **zero ollama stanzas** in `eval_backends.yaml` — the only mention
is a comment at line 38 — the row would have had exactly one entry, which is a control that does
nothing. An ollama arm is therefore added to the config, greyed with its reason when ollama is not
reachable. It also answers a question worth being able to ask: does a prompt behave the same under
a different serving stack?

### D1d — No mock arm

The mock planner emits canned plans (`inputs: ['speed']` and similar) that read exactly like model
failures. In a bench built for judging models, that is a trap, not a feature. `agent.infer()`
defaults to `use_mock=True`, which is the same trap one layer down — T3's runner always passes it
explicitly.

### D1e — A build is labelled by what it reports, never by its path or filename

`--version` is bare `CARGO_PKG_VERSION`; nothing prints the compiled feature set, though the binary
knows (`cfg!(feature = "cuda")`, `main.rs:1825`). So the native CLI gains **`backend list --json`**,
printing `built_with`, `dynamic_backends`, `backends_dir` and `entries`. It **always emits**, with
`dynamic_backends: false` and an empty `entries` on a static build, so the workbench parses one
shape from every binary.

It costs nothing — no model is loaded — so it runs on every inventory, and there is no cache to
invalidate when a rebuild replaces a binary. A directory named `release-vulkan` holding a CUDA
build cannot lie to the operator.

Since the workbench parses it, the key names are interface: keep a test on the shape.

### D2 — The backend is *measured*, and the existing detector cannot do it

`native_lane.detect_backend()` matches `llama_prepare_model_devices: using device (\S+)`, which
is device *enumeration*. Proven wrong:

    default              -> CUDA0
    KNAIF_N_GPU_LAYERS=0 -> CUDA0     # every layer was on CPU

The workbench reads **`load_tensors: layer N assigned to device X`** instead and reports the
distribution. This reaches past the notebook — it is what the L4 lane records and what
`docs/PERFORMANCE.md` numbers are attributed to — so the fix lands in `native_lane.py` and the
notebook consumes it.

### D2b — Python's placement is measured too, via an fd-level capture

Symmetry is the point: a panel that measures one runtime and takes the other on trust cannot
support a comparison. But `Llama(verbose=...)` ([`orchestrator.py:271`](../../python/core/knaif/orchestrator.py#L271))
writes llama.cpp's load trace to **C-level stderr**, past `sys.stderr`, so Python's capture needs a
file-descriptor redirect around model load — not a `contextlib.redirect_stderr`.

The captured text then goes through the **same parser** as the native side, so both columns report a
distribution produced by one piece of code. The capture runs once per `(model, n_gpu_layers)` per
session and is cached; the redirect must not swallow the notebook's own output.

### D3 — Models come from `eval_backends.yaml`, filtered by what resolves

41 of its entries carry a model path and **all 41 resolve on this machine** (58 files in
`models/`). `models.yaml` holds the curated 4, the manifest the published 3. The selector lists
everything resolvable, grouped — *published* (manifest), *curated* (`models.yaml`),
*experimental* (the rest) — so "all versions" is real without 41 undifferentiated rows. An
entry whose GGUF is absent is shown greyed with its missing path, never silently dropped.

### D4 — Timing is asymmetric today, and the plan closes the gap

Native already emits, under `KNAIF_TIMING=1`:

    [knaif-timing] model load_from_file = 921 ms
    [knaif-timing] new_context = 17 ms
    [knaif-timing] prompt_decode (2250 tokens) = 219 ms
    [knaif-timing] generation (27 tokens) = 141 ms
    [knaif-timing] generate_plan TOTAL = 380 ms

Python has only per-step `duration_ms` (`agent.py:767`) and end-to-end `latency_ms`. There is
**no token-level instrumentation in `orchestrator.py`** — no prompt/generation split, no
tokens per second. Comparing the two runtimes on "time" today compares different things. T4
adds the matching Python numbers; until it lands, the panel labels Python's as end-to-end only.

### D4b — Native runs as a subprocess, not through bindings

Asked directly: must the native side shell out, or could Python call the Rust in-process?
It could, eventually — but nothing exists to call (no PyO3, no maturin, no `cdylib`; the
crates are plain rlibs and `knaif` is a `[[bin]]`), and **bindings would make the backend
problem worse, not better**:

- Backend is a compile-time feature, so an extension module built with `cuda` *is* the CUDA
  build. Offering CUDA and Vulkan means two `.pyd` files, and a Python extension module cannot
  be cleanly unloaded — switching backend would mean restarting the kernel. A subprocess
  switches per call.
- llama.cpp holds VRAM for the life of the process. In-process, the kernel keeps a 2.5 GB
  model resident until restart; flipping between 41 models on a 16 GB card becomes a
  restart-per-model workflow. A subprocess frees on exit.
- `docs/FINE_TUNING.md` records CUDA `illegal memory access` as a driver-crash hazard — *stop,
  do not auto-retry*. In-process that takes the kernel and the session with it.
- The binary is the shipped artifact. Even for a testing-only bench, exercising what users run
  is worth more than exercising a library they never load.

Measured cost of that choice, three runs, `--dry-run`, same model:

| | wall | of which model load | `generate_plan TOTAL` |
|---|---:|---:|---:|
| native subprocess | ~1710 ms | ~910 ms | ~371 ms |

So ~1.34 s of overhead per utterance, most of it model load. If that becomes intolerable the
answer is **a persistent mode on the binary** — a `serve`/REPL reading utterances on stdin,
keeping process isolation while amortising the load — not a binding layer. No such mode exists
today; this plan does not add one.

### D4c — Wall clock must not be compared across runtimes

The same measurement exposes a trap for T6. Python keeps the orchestrator resident, so it pays
load once and very likely reuses the KV cache for a repeated prompt prefix:

| | first call | steady state |
|---|---:|---:|
| Python, resident | 1224 ms load + 623 ms | **~150 ms** |
| native, per subprocess | ~1710 ms | **~1710 ms** |

Read as wall clock that is native being 11x slower, which is nonsense — it is process
lifecycle plus cache warmth, not runtime quality. Python's steady-state 150 ms is even below
native's *prompt decode alone* (219 ms for 2250 tokens), which points at prefix-cache reuse
rather than faster inference.

The panel therefore compares `generate_plan TOTAL` against Python's equivalent generation
window, shows load and process overhead as separate rows, and labels cold versus warm. A
single "time" column across runtimes is forbidden.

### D5 — Execution never touches the real fixtures

A real run provisions a **copy** into a per-run scratch directory, the way
`chain.run_command_chain` and `native_lane.provision_fixtures` already do. A workbench that
overwrites `sandbox/fixtures/` silently poisons every later eval.

### D6 — The notebook is thin; the logic is importable and tested

Cells do configuration and display only. Everything else lives in
`notebooks/shared/workbench/` as ordinary modules with unit tests, because a bug in a notebook
cell is invisible to `just check`. This is also what makes the side-by-side reusable from a
script later.

## The work

### [ ] T1 — `infer_stream` accepts a retrieved registry

Add `registry_override` to `CommandAgent.infer_stream`, matching `infer`. Test: the same
utterance through both, with the same override, builds the same prompt. Without this the
workbench cannot show the production prompt and fault (3) above stays true. **Core, TDD.**

### [ ] T2a — `knaif backend list --json` (native, Rust)

Per D1e: print `built_with`, `dynamic_backends`, `backends_dir` and `entries` as JSON, always,
including on a build with no backend store. A human `Built with: …` line stays in the default
output for anyone debugging a shipped binary.

The workbench parses this, so the key names are an interface — pin the shape with a test, not just
the happy path. **Native, TDD.** No model load, so it stays cheap enough to call on every
inventory.

### [ ] T2b — An ollama arm in `eval_backends.yaml`

Per D1c: the config has zero ollama stanzas today, so the inference row would have one entry. Add a
working arm, greyed with its reason when ollama is unreachable.

### [ ] T2 — Honest backend measurement in `native_lane`

Replace `detect_backend`'s enumeration regex with tensor-placement parsing; return a
distribution such as `{"CUDA0": 36, "CPU": 0}` rather than a single string, keeping the
enumerated device as a separate field. Update the L4 lane's recorded value and its tests.
Verify against both `KNAIF_N_GPU_LAYERS=0` and the default. **Core, TDD.**

Per D2b the parser stops being private to the lane: the Python runner calls the same function, so
it lives where both reach it (`native_lane.py`, whose other consumer is the L4 lane), with the
fd-level capture helper in `workbench/` beside its only caller.

### [ ] T3 — `workbench/runners.py` — one interface, two runtimes

`PythonRunner` and `NativeRunner` returning the same `RunResult`: plan JSON, rendered commands,
produced artifacts, outcome, timings, measured backend, raw stdout and stderr.

- Python: `CommandAgent.from_skill` + `retrieve_tools` + `infer(use_mock=False, ...)` +
  `execute_plan(dry_run=..., confirmed=...)`.
- Native: reuse `native_lane.build_argv` / `parse_run_output` / `provision_fixtures`.
  **Keep the `--` separator.** Without it an utterance containing a dash-prefixed token is
  parsed as knaif's own flags, which produced a fabricated safety breach on `ffmpeg_safety_003`.

A note the tests pin: `agent.infer()` defaults to **`use_mock=True`**. A caller that forgets it
gets canned plans such as `inputs: ['speed']` that read exactly like model failures. The runner
always passes it explicitly.

### [ ] T4 — Python timing parity

Instrument `orchestrator.py` for prompt-token count, generation-token count and their
durations, exposed on the result and named to mirror native's fields so the panel is one table
rather than two. Until it lands the panel labels Python "end-to-end only".

### [ ] T5 — `workbench/inventory.py` — models, binaries, skills

Resolve models per D3. Discover builds by scanning `target/release*/` — which now covers both
plain `release` and every `release-<kind>` profile — plus any directories declared in
`workbench.local.yaml`. Record for each its `--version` and its compiled feature set, read from
`knaif backend list --json`. List skills from `list_skills()` with their `runtimes.native.status`.

### [ ] T6 — `workbench/panel.py` — the output

Per run: outcome, plan JSON, rendered commands, artifacts produced (size, and for media an
`ffprobe` summary), and a timing table. Side-by-side mode diffs the two runtimes' plans and
commands and highlights the first divergence. Statistics across repeated runs of one utterance
— n, mean, p50, p95 — because a single sample is not a latency measurement.

### [ ] T7 — `notebooks/skill_workbench.ipynb`

Config cell, selector cell, run cell, panel cell, side-by-side cell. Under 15 cells. A markdown
header stating plainly what the bench does and does not certify.

### [ ] T8 — Retire or repoint the old testers

Either delete the two skill testers or fix their `SKILL_DIR` and point their markdown at the
workbench. Leaving them as they are is the worst option: they are what someone finds first, and
they are wrong in a way that flatters nothing.

### [ ] T9 — Tests and docs

Unit tests for every `workbench/` module, with a mock runner on the native side so CI needs no
GGUF. A row in `docs/SANDBOX.md` for the scratch directory. A line in `AGENTS.md` under
*Notebooks* naming the workbench as the interactive entry point.

## Open questions

Two things the mockup raised that are **not decided**, and should be before T7:

- **Real widgets, or a plain config cell?** The mockup draws `ipywidgets` dropdowns. That adds a
  dependency, and widgets do not render for anyone reading the notebook on GitHub. A plain config
  cell edited by hand is uglier, works everywhere, and needs nothing new.
- **Is eight cells the right grain?** Selectors and run are separate so a dropdown can change
  without re-scanning the inventory. They could be one cell; so could stats and the side-by-side.

A rendered mockup of the eight cells, the selector and the panel output exists as a published
artifact — useful for judging the shape, but it is a drawing, not a spec. This plan is the spec.

## What this is not

- **Not an acceptance instrument.** It runs whatever you type: no corpus, no floors, no safety
  gate. `just eval-accept` is the bar and this cannot move it. The header cell says so.
- **Not a native verdict.** Both skills are `runtimes.native.status: in-progress`, and
  `check-gate` reports L3 and L4 stale for ffmpeg. Native output here is informative, not
  release-grade; a native disagreement must not block a Python publish decision.
- **Not a replacement for `parity_check.py`**, which measures L3 over a corpus. One utterance
  at a time is a different question.

## Risks

- **T1 and T2 change core and the L4 lane.** Both are TDD with the full suite re-run. T2 changes
  a recorded field, so saved L4 records keep their old shape and the reader tolerates both.
- **Scope creep toward a mini-eval.** The corpus-scale question is answered elsewhere. If the
  workbench grows floors or verdicts, that is the signal it has gone wrong.
- **Backend comparison needs builds that do not exist yet.** Less true than it was: kinds now
  coexist, and `just build-native-kind <kind>` stands one up without a Developer PowerShell. The
  selector still shows what is there and names what is missing rather than pretending.
- **The eval lane's binary is a third feature set.** `target/release/knaif.exe` — what
  `eval_backends.yaml` points at — was built `llama,cuda,pdfium`, which is neither a packaging kind
  nor a dev wrapper. The workbench will happily register it; it just is not any `release-<kind>`
  directory, and the label must come from the binary rather than from an assumed kind.
