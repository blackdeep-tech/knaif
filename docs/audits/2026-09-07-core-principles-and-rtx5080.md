# Core-principles audit and RTX 5080 verification

Date: 2026-09-07. Audited checkout: `fc8e86a` (`1.1.0` workspace).

## Assessment

The repository has substantial working functionality and passes its existing Python and Rust checks. However, the deterministic safety boundary, native execution parity, and regression gates do **not** yet satisfy the project's stated principles. Passing the current tests is insufficient evidence of those guarantees.

The most urgent defects are executable internal tools accepted from model output, destructive intents executing without confirmation after expansion, and native sandbox escapes. These were reproduced with harmless subprocesses and disposable fixtures, not inferred solely from comments.

This audit changes no implementation, contracts, model configuration, training data, or acceptance snapshots. The audit and the required evaluation-history entry are documentation changes. Test/build caches, regenerated evaluation fixtures, saved evaluation outputs, and disposable audit fixtures are generated artifacts.

## Scope and method

Reviewed the Python planning/validation/expansion/dispatch path, native planning and CLI dispatch, FFmpeg and documents execution boundaries, shared contracts, regression/parity utilities, model configuration, and relevant architecture/requirements/operations documentation. Used `AGENTS.md`, `docs/REQUIREMENTS.md`, and the dual-runtime port contract as the acceptance criteria.

Evidence labels below distinguish **reproduced** behavior, **source-reviewed** gaps, and **previously documented** debt. The model was replaced by a supplied JSON response for native safety/chain probes, so those results isolate deterministic code and do not depend on model routing quality. Full model evaluation is recorded separately below.

This is not an exhaustive security certification or a review of every training experiment, installer, website, and optional backend. No deployment, dependency upgrade, model promotion, or snapshot re-lock was performed.

## Findings

### F1 — Critical: model-emitted internal FFmpeg steps can execute arbitrary programs

**Reproduced, including acceptance through `infer()`.**

Sources: [planner.py](../../python/core/knaif/planner.py), `validate_step`, line 412; [agent.py](../../python/core/knaif/agent.py), `_parse_and_check`, line 978; [tools.yaml](../../skills/ffmpeg/tools.yaml), `run_preview`/`run_batch`, lines 184/205; [steps.py](../../skills/ffmpeg/python/steps.py), lines 154/237; [_deps.py](../../skills/ffmpeg/python/_deps.py), line 45.

`internal: true` only hides a tool from the prompt. Validation still accepts it from the model against the complete registry. Both execution steps are classified `safe`. `RunPreviewStep` accepts an arbitrary argv list, and `run_ffmpeg()` passes it unchanged to `subprocess.run`; it neither pins the executable to FFmpeg nor verifies that deterministic expansion produced the command.

Reproduction with a loaded FFmpeg agent and a temporary sandbox:

```python
payload = {"plan": [{"tool": "run_preview", "args": {"command": [
    sys.executable, "-c",
    'import sys; sys.stderr.write("AUDIT_BENIGN_EXECUTION")'
]}}]}
agent.execute_plan(payload, dry_run=False, confirmed=False)
```

Result: `returncode: 0`, `stderr_tail: AUDIT_BENIGN_EXECUTION`. A supplied-model stub returning a `run_preview` plan was also accepted by `agent.infer(..., use_mock=False)` without a validation error. No malicious shell command or destructive payload was executed.

**Impact:** model output can select the executable and its arguments, bypassing the central rule that the model only proposes domain intents and deterministic code constructs commands. `shell=False` does not prevent this because the executable itself is model-controlled. Sandbox validation does not inspect this argv.

**Recommended correction:** validate model proposals against public tools only; keep a separate trusted validation/dispatch path for expanded internal steps. Pin subprocess executables and enforce argument/path boundaries at the execution boundary as defense in depth. Add negative tests through the inference-to-execution path, not just prompt-hiding tests.

### F2 — High: destructive intent safety is lost during expansion

**Reproduced with both a minimal intent and an actual FFmpeg artifact.**

Sources: [agent.py](../../python/core/knaif/agent.py), expansion around line 425, intent execution loop at line 570, destructive check at line 660; [intents.py](../../skills/ffmpeg/python/intents.py), `_build_batch_block`; [tools.yaml](../../skills/ffmpeg/tools.yaml), `strip_audio` at line 80.

The destructive check runs on expanded leaf steps. It never checks the original intent's `safety_category`. A destructive intent expanding into safe leaves therefore runs with `dry_run=False, confirmed=False`. The optional `require_approval` gate defaults to false and is not a replacement for the mandatory destructive-category rule.

A minimal registry with `danger: destructive` expanding into `leaf: safe` invoked the leaf with `ctx.confirmed == False`. More concretely, the real fine-tuned model proposed `strip_audio` for a disposable clip; executing that plan with `confirmed=False` created `silent.mp4`. All expanded steps were marked safe. The output was independently readable by FFprobe.

**Impact:** normal public FFmpeg requests can bypass mandatory authorization; F1 is not required to exploit this defect. Preview-enabled workflows can additionally perform their preview before a later confirmation gate.

**Recommended correction:** preserve and enforce the original intent's safety requirement before any expanded side effect. Keep any leaf-level checks as additional requirements. Cover destructive intent → safe leaves, direct destructive steps, previews, and declined approval separately.

### F3 — High: native FFmpeg inputs bypass sandbox resolution and validation

**Reproduced through the compiled native CLI.**

Sources: [run.rs](../../skills/ffmpeg/native/src/run.rs), `probe_input` at line 44 and calls at lines 114/148; [engine.rs](../../skills/ffmpeg/native/src/engine.rs), output sandbox check at line 779; [planner.rs](../../native/crates/knaif-core/src/planner.rs), path checks only for `path`/`src`/`dst`.

Native expansion calls `probe_input(Path::new(input), ...)` directly. It does not resolve `inputs` against the sandbox or check their containment before probing. Checking the generated output path does not protect the input.

Two disposable-fixture probes established the consequences:

- With `--sandbox <sandbox>`, `clip.mp4` existed inside that directory, but native execution failed with `input not found: clip.mp4` because it looked in the process working directory.
- With an absolute input outside the sandbox and an explicit output inside it, native dry-run emitted `ffmpeg -y -i <outside>/clip.mp4 ... <sandbox>/escaped-read.mp4`. Execution without consent reached the destructive confirmation error **after probing the outside input**, instead of rejecting a sandbox escape. No output was written in this second probe.

**Recommended correction:** port Python's shared input resolution semantics, including relative paths, file lists/globs/directories, and containment checks before every probe/read. Cover both single-input and concat expansion.

### F4 — High: native lexical sandbox checks permit Windows junction escapes

**Reproduced with documents; source-reviewed in core and FFmpeg helpers.**

Sources: [documents run.rs](../../skills/documents/native/src/run.rs), `lexical_abs`/`assert_in_sandbox` around lines 595–620; [FFmpeg engine.rs](../../skills/ffmpeg/native/src/engine.rs), line 659; [core planner.rs](../../native/crates/knaif-core/src/planner.rs), lines 32–80. Python comparison: [planner.py](../../python/core/knaif/planner.py), `_resolve_path`, line 113.

The Rust helpers normalize `.` and `..` lexically but never resolve filesystem links. A path can start with the sandbox text while the filesystem resolves it outside that directory.

Created an audit-owned junction `<sandbox>/junction` targeting an audit-owned sibling `<outside>`, containing a one-page PDF. Native `run documents "Inspect junction/example.pdf" --sandbox <sandbox> --dry-run`, driven by a supplied `inspect_document` plan, printed `pdf: 1 page(s), 1362 bytes, encrypted=false, text_layer=true`. It read the outside document. The equivalent Python call rejected it with `Path ... is outside sandbox ...`.

**Impact:** the explicit native sandbox boundary is ineffective against junctions and analogous links. This is an actual filesystem read, not merely acceptance of a nonexistent path. Output helpers have the same lexical-only design; an outside write was not attempted.

**Recommended correction:** use shared, filesystem-aware containment semantics, resolving existing ancestors for not-yet-created outputs. Test Windows junctions and Unix symlinks, relative sandboxes, and output paths through linked parents. Consider link replacement races separately; lexical normalization alone cannot solve them.

### F5 — High: native `run` silently drops every plan step after the first

**Reproduced; already acknowledged in the parity script.**

Source: [main.rs](../../apps/cli/src/main.rs), `steps.first()` at line 710 and single dispatch at line 757. Existing acknowledgment: [parity_check.py](../../scripts/parity_check.py), module scope note.

A valid supplied plan containing `strip_audio` to `silent.mp4`, followed by `resize_video` to `small.mp4`, produced only the first FFmpeg command and exited 0 under `run --dry-run`. The second step was not previewed or executed. This is distinct from whether a native prompt induces the model to propose a chain: even a correct chain is truncated.

**Impact:** the shipped runtime cannot satisfy multi-step requests or provide Python-equivalent variable binding/execution. A successful exit can describe partial task completion.

**Recommended correction:** implement a full native plan executor with ordered dispatch, variable resolution, post-resolution validation, confirmation, and failure/terminal handling. Until that exists, explicitly reject unsupported multi-step execution instead of reporting success for only the first step. Include a deterministic supplied-plan test so this guarantee does not depend on a GPU/model eval.

### F6 — High: regression commands can report success without current evidence

**Reproduced.**

Sources: [justfile](../../justfile), `eval-regression`, line 551; [evalsuite/cli.py](../../python/core/knaif/evalsuite/cli.py), `cmd_regression`, line 1066; [snapshot.py](../../python/core/knaif/evalsuite/snapshot.py), `diff_snapshots`, line 27.

`just eval-regression ffmpeg` supplies no current scoreboard. `cmd_regression` initializes `current = baseline`, so it compares the snapshot to itself. An explicitly provided nonexistent `--current` path also silently takes this branch.

Observed both commands exit successfully with `No regressions above threshold=0.02. OK`:

```powershell
just eval-regression ffmpeg
uv run --frozen -m knaif.evalsuite regression --skill documents --current sandbox/does-not-exist-audit.json
```

There is a second fail-open behavior in the comparator: it ignores a metric if either side is missing and does not require matching verifier or evaluation population. A success scoreboard with `total=847` and score 1.0 versus a cheap scoreboard with `total=1` and score 1.0 passed. A baseline containing outcome accuracy versus `{}` also passed.

**Impact:** the advertised acceptance gate can be green without running a model, executing an artifact, or comparing compatible measurements. This also weakens any training/promotion workflow that relies on it.

**Recommended correction:** require an existing, explicitly identified current run; reject missing metrics and incompatible verifier/corpus/model identity before computing deltas. Make the recipe accept a run path or deliberately run a fresh evaluation. Preserve the distinction between a quality regression and invalid comparison evidence.

### F7 — High: parity normalization can erase meaningful command differences

**Reproduced with the actual comparison helper.**

Source: [parity_check.py](../../scripts/parity_check.py), `canon_token`, line 85, and `Outcome.key`, around line 141.

The comparator treats any token containing `/` as path-like and reduces it to the final component. This conflates different files and destroys FFmpeg filter expressions containing division.

Observed outputs:

```text
a/clip.mp4 -> clip.mp4
b/clip.mp4 -> clip.mp4
scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2 -> 2
scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2 -> 2
```

**Impact:** matching parity keys need not mean matching input/output files or image geometry. Combined with the script's documented non-strict treatment of native chain limitations, a favorable parity report is weaker than the project's “same rendered command” promise.

**Recommended correction:** normalize paths only in identified argv positions and resolve them against the shared working directory; preserve filter strings verbatim or parse them semantically. Test different directories with identical basenames and expressions containing `/`. Treat unsupported chains as failed acceptance coverage, even if diagnostic reports classify them separately.

### F8 — Medium: Python and native feed the fine-tune different planning prompts

**Live Python measurement plus native source review; previously documented, not fixed.**

Sources: [main.rs](../../apps/cli/src/main.rs), line 1051; [native prompt.rs](../../native/crates/knaif-core/src/prompt.rs); [agent.py](../../python/core/knaif/agent.py), `build_prompt`; [build_dataset.py](../../python/training/build_dataset.py). Prior analysis: [native/Python planning parity plan](../plans/2026-08-08-native-python-planning-parity.md).

For `trim clip.mp4 to 5 seconds then resize to 720p`, Python retrieval selected these five public tools in order: `resize_video, trim_video, convert_video, compress_video, strip_audio`. The live registry has 13 public FFmpeg tools. Native passes the full registry into `build_prompt`, filters internal tools, and orders public tools by YAML order. Python's retrieved path also selects examples, whereas native uses the static examples block. The dataset-builder code uses Python retrieval, consistent with the documented training pipeline; its current entry point is broken, as recorded in F12.

**Impact:** Python eval scores do not establish shipped native planning quality. These differences are plausible causes of quality differences, but this audit does not claim a measured causal effect or an accuracy delta. Native model inference was not benchmarked here.

The promoted generation budget is **512 on both sides**; do not revive the previously corrected claim that this fine-tune uses 2048 in Python. Follow the existing plan's controlled prompt comparison before attributing remaining model failures.

### F9 — Medium: committed acceptance snapshots do not cover the current acceptance surface

**Measured from the checked-in corpus and snapshot files; FFmpeg debt already documented in TODO.**

| Skill | Current corpus rows | Current utterances | Snapshot `total` | Snapshot verifier |
|---|---:|---:|---:|---|
| FFmpeg | 314 | 847 | 297 | `cheap` |
| Documents | 143 | 164 | 129 | `success` |

Sources: each skill's `data/eval.jsonl` and `data/eval_snapshot.json`; [TODO.md](../TODO.md), around lines 735 and 792. The `total` field is reported as stored; row counts and expanded utterance counts are distinct units, so subtracting them is not a reliable coverage calculation.

FFmpeg's snapshot explicitly violates the requirement that an acceptance bar use an executing verifier. Neither snapshot embeds enough corpus identity to establish coverage of today's complete expanded utterance population. The documents snapshot uses the right verifier, but its stored population also differs from the current corpus.

**Recommended correction:** after fixing the fail-open gate, deliberately evaluate and adopt compatible full-corpus executing snapshots in their own change. Store corpus hash/row IDs, model checksum/config, and code identity. This audit does not re-lock the bar or call a comparison between different populations a regression.

### F10 — Medium: runtime output verification does not enforce the requested result

**Source-reviewed; explicitly documented existing gap.**

Sources: [steps.py](../../skills/ffmpeg/python/steps.py), `VerifyOutputsStep`, line 273; [intents.py](../../skills/ffmpeg/python/intents.py), `_build_batch_block`; [agent.py](../../python/core/knaif/agent.py), `_step_failed`, line 42; [ARCHITECTURE.md](../ARCHITECTURE.md), “Known gap: validation stops at dispatch.”

The batch expansion does not supply expected properties to verification. `VerifyOutputsStep` records a probe summary and marks a successfully probed file verified without asserting the requested duration, dimensions, codec, etc. Core failure detection examines subprocess return codes, not `verified: false`. The independent executing eval verifier is stronger than this production success criterion.

**Impact:** a zero-exit command that produces the wrong artifact can still look successful to the application. Eval success does not imply equivalent runtime verification exists.

**Recommended correction:** carry deterministic expected properties from the recipe into verification and make verification failures stop/report failure through the generic handler-result contract. Keep this distinct from improving model routing.

### F11 — Medium: the advertised generic native skill contract is still a stub

**Source-reviewed architectural debt and documentation mismatch.**

Sources: [native skill API](../../native/crates/knaif-skill-api/src/lib.rs), entire file; [main.rs](../../apps/cli/src/main.rs), skill dispatch at line 757 and confirmation around line 808; [executor.py](../../python/core/knaif/executor.py), domain handlers from line 57; [NATIVE.md](../NATIVE.md), crate table near line 53; `AGENTS.md`, native-port instructions.

`knaif-skill-api/src/lib.rs` contains only module comments, ending “Skeleton only.” There are no `HandlerContext`, `Step`, or `Intent` definitions. Native behavior is implemented through direct FFmpeg/documents CLI branches. Native confirmation is selected by those branches/write previews, not through a generic executor interpreting `ToolDef.safety_category`.

Python also still carries the IO list/find/delete/move implementations in core `executor.py`, and a bare `CommandAgent` defaults to that handler map. This is residual domain behavior in the supposedly domain-agnostic core; it is not a newly introduced skill import.

**Impact:** the documented native extension contract cannot be implemented as written. New native domains require host-specific wiring, and Python/native safety policy has more than one implementation to keep aligned. F2–F5 show concrete consequences of incomplete shared execution semantics.

**Recommended correction:** either implement and use the generic native skill/execution API, or explicitly document the current specialized host and incomplete port. Move legacy IO implementation into its bundle when rebuilding that stale skill. Do not describe a crate name and dependency edge as an implemented API.

### F12 — Medium: the documented training dataset builder is broken after the repository move

**Reproduced; analogous stale paths found in the preference/distillation builders.**

Sources: [build_dataset.py](../../python/training/build_dataset.py), `ROOT` at line 19 and skill/data paths at lines 117–118; [build_preference_dataset.py](../../python/training/build_preference_dataset.py), line 87; [build_ffmpeg_distill.py](../../python/training/build_ffmpeg_distill.py), lines 53–54/284; [FINE_TUNING.md](../FINE_TUNING.md), reproducible pipeline.

The documented command was run with its output redirected to a disposable location:

```powershell
uv run --frozen python python/training/build_dataset.py --skills ffmpeg,documents --out "$env:TEMP/knaif-audit-20260907-union.jsonl"
```

It failed before writing a dataset: `FileNotFoundError: .../src/skills/ffmpeg/skill.yaml`. The script still uses `src/skills/...`; that directory no longer exists. Its `ROOT = ...parent.parent` now resolves to `<repo>/python`, so simply removing `src/` from a path would not fix all path resolution either. The preference and distillation builders retain similar old paths; they were source-reviewed, not run.

**Impact:** the canonical authoring-to-fine-tuning workflow cannot currently reproduce its input dataset from this checkout. Passing data-integrity/unit tests does not establish that the actual builder CLI works. This does not invalidate the existing published model checksum or imply the existing weights were trained incorrectly.

**Recommended correction:** update root/bundle/data resolution across the moved scripts and test the real builder entry point on a small temporary output. Keep training dependencies isolated as the project already requires. No training, preference-data regeneration, or corpus modification was performed by this audit.

Additional data-hygiene observation: the current training files contain 404 FFmpeg and 334 documents examples. A comparison after case/punctuation/whitespace normalization found no exact FFmpeg train/eval overlaps and one documents overlap: `Do something with a file.` versus `documents_079`, a generic clarification case. The existing verbatim-overlap test only covers FFmpeg. This small overlap should be documented or removed in a future corpus change; it is not evidence of hard-case leakage or memorization of artifact-producing requests. Semantic near-duplicates were not audited exhaustively.

## Verification on this machine

### Environment

- Windows, PowerShell; NVIDIA GeForce RTX 5080, 16,303 MiB reported by `nvidia-smi`, driver 616.64.
- CPU: AMD Ryzen 9 9950X3D, 16 physical cores / 32 logical processors. FFmpeg: `N-124716-g054dffd133-20260531`, installed on PATH.
- Python 3.13.12, uv 0.11.2, Rust 1.96.0, `llama-cpp-python` 0.3.23.
- Initial repo `models/` directory was absent. Both public fine-tunes existed in `C:/Users/statu/.knaif/models`. During the audit the user copied the model directory into the repo; final evaluation uses the repo copy.
- `knaif-qwen3-4b-v1-q4_k_m.gguf`, 2,497,280,960 bytes, SHA-256 `6dd7779b2597b4c211c6f83afbc2423cdf2951e631a9d1a27e4f841119644014`. The native-cache and copied repo model match the committed manifest.
- The project entry point `uv run --frozen -m knaif._gpu_check` succeeded and identified CUDA device 0 as the RTX 5080. A bare `import llama_cpp` initially failed DLL loading; the project's DLL preparation resolves it. This is **not** a broken-backend finding.
- Actual model-load trace selected `CUDA0` and reported `offloaded 37/37 layers to GPU`. The evaluation config requests `n_gpu_layers=99`, `n_ctx=8192`, `n_threads=8`, `max_tokens=512`, JSON grammar off, thinking off; llama.cpp generation uses temperature 0.

### Automated checks

| Check | Result |
|---|---|
| `uv run --frozen pytest --tb=short -q` | **1,697 passed, 1 skipped**, 16 warnings, 28.33 s |
| `cargo test --workspace` | Passed, default workspace features |
| `uv run --frozen ruff check .` | Passed |
| `cargo fmt --all -- --check` | Passed |
| `cargo clippy --workspace --all-targets -- -D warnings` | Passed |
| `just type-check-py` | Passed; mypy checked 48 source files |
| `just gen-skills-check` | Passed; generated README skill inventory current |
| `cargo build -p knaif-cli` | Passed; supplied-plan native probes used this build |
| `uv run --frozen -m knaif._gpu_check` | Passed, CUDA offload supported |
| Documented union dataset builder, temporary `--out` | Failed on stale `src/skills` path; F12 |

These runs include the existing shared-contract tests. They do not establish native llama.cpp/PDFium feature-build coverage, website build/accessibility status, or fresh cross-runtime model parity. `just check` as a whole was not run. Most pytest warnings came from intentional missing-model tests; there was also a notebook traitlets deprecation and unclosed `nul` resource warnings.

### Small real-model smoke check

Before the full corpus run, six hand-selected requests used the public 4B fine-tune through Python/llama.cpp CUDA, with retrieval and disposable media/document fixtures. They produced valid plans/dry-runs for strip audio, trim, a two-step strip/resize chain, document inspection, document rotation, and a clarification for an unnamed video.

After reviewing the concrete plans, four were executed against audit-owned fixtures. Independent FFprobe/PDF checks found: a readable 32×32 `silent.mp4`, a 0.12-second trimmed clip for the 0.1-second request (frame quantization), a 16×16 final chain output, and PDF rotation 90 degrees. The source clip in this small probe was already silent, so this is not a substantive audio-removal test. The first execution intentionally used `confirmed=False` to reproduce F2; the other writes used `confirmed=True`.

Observed inference call times in request order: 28,448.5; 494.0; 564.6; 282.2; 1,937.5; 265.4 ms. These are a cold first inference plus heterogeneous subsequent calls, **not** a throughput benchmark or a corpus quality percentage. Loading happened before the first timed call. Do not compare them directly to historical medians.

An exploratory Ollama smoke attempt made before the user's backend preference returned one disconnected request followed by connection refusals. It produced no evaluation scoreboard or useful model results. No Ollama evaluations are used in this audit; all scored evaluation uses the requested fine-tune through llama.cpp.

### Full executing evaluation

Run directory: `evals/runs/2026-09-07_rtx5080-core-audit_success/`. Backend key: `qwen3-4b-sft-v3-flat-q4`, which maps to the requested public `knaif-qwen3-4b-v1-q4_k_m.gguf`, not the old `qwen3-4b-ft-q4` model. Retrieval enabled; full corpora, all utterances; one worker; snapshots unchanged.

Commands:

```powershell
just eval-fixtures ffmpeg
just eval-fixtures documents
uv run --frozen -m knaif.evalsuite run --all-skills --backends qwen3-4b-sft-v3-flat-q4 --verifier success --save evals/runs/2026-09-07_rtx5080-core-audit_success --label rtx5080-core-audit
```

Fixture regeneration completed successfully before evaluation. The `success` run renders/plans through Python and materializes artifacts through the skill's artifact runner; this does not execute the native runtime. It is stronger than `cheap`, but only checks the corpus's declared criteria.

| Skill | Utterances | Expected outcome correct | Tool accuracy | Reported schema validity | Conditional verifier average | p50 / p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| Documents | 164 | 160/164 (97.56%) | 97.56% | 99.39% | 100% over 153 scored plans | 228 / 541 |
| FFmpeg | 847 | 764/847 (90.20%) | 89.14% | 98.47% | 97.14% over 575 scored plans | 336 / 754 |

**Interpretation:** outcome accuracy compares `plan`/`clarify`/`reject`/error, not full task correctness. The verifier average is conditional on producing a plan; non-plan failures/refusals do not enter that average. The reported “schema validity” metric is derived from `outcome != error`, not a separate strict audit of raw model JSON. Latencies measure inference plus planning/dry-run work, ending before real artifact execution in `runner.py`; the scoreboard's “time to artifact” label must not be read as real output-ready wall time. First-row warmup is excluded from the latency aggregate.

Documents failure review:

- `documents_105`: the reverse-page-order request produced `order: "original"`; deterministic execution rejected it with `Unrecognized page reference: 'original'`. This was one expected-plan error.
- All three `documents_132` phrasings produced `split_pdf` with `ranges: "1-2"`, whereas the corpus expects clarification. These are outcome mismatches. They have empty success criteria, so the artifact verifier returns 1.0 without checking any criterion.
- Of 153 scored plans, 150 were expected-plan cases and passed their declared criteria; the other three were the above misrouted clarification cases. Eleven non-plan outcomes, including the page-order error, were unscored by the artifact verifier. Thus “100%” is **not** 164/164 successful requests.

FFmpeg completed after this document was first drafted; see `evals/runs/2026-09-07_rtx5080-core-audit_success/` and its `evals/INDEX.md` row. 764/847 (90.20%) matched their expected outcome; tool accuracy 89.14%; of 575 scored plans, the conditional artifact-verifier average was 97.14%, i.e. producing a plan does not by itself mean that plan's criteria passed. By category (`intent_metrics.by_category` in the scoreboard), the weakest slices were `clarify` (167/197 correct, 84.77% precision / 83.92% recall), `reject` (29/46 correct, 63.04% precision), `speed` (1/3), and `edge` (39/51). These are **model-routing** scores and carry no claim about the deterministic execution findings above: an earlier draft of this paragraph linked the clarify numbers to F2, which the scoreboard does not support — F2 is a confirmation-boundary defect reproduced directly in code, and whether the model routes an utterance to `clarify` is a separate axis. Keep them separate. This is a category tally from the scoreboard, not a per-row manual review like the documents pass above; a full misroute review was out of scope for completing this table.

Corpus SHA-256 values for reproducing this measurement:

```text
ffmpeg:   df4295c631a358849b6949594da3b2abf52bb303895b8b4c3e979820b77d0469
documents: 828b85928b32e8a22343d9b61987e5add2d5aaf86a3c05db9173ddd5f1db4fb4
```

There are no historical fine-tune success scoreboards on this checkout available for an exact per-utterance paired comparison. The July summaries use different code/corpus populations. Treat this as a fresh machine/code measurement; do not attribute differences from those summaries solely to GPU hardware or call them a measured regression. The incompatible committed snapshots were not re-locked or used to declare this run accepted.

## Documentation corrections to queue

| Location | Inaccuracy or misleading claim | Correction needed |
|---|---|---|
| `docs/REQUIREMENTS.md`, Current Scope | Says the primary interface is Python and a CLI may be added later | Describe the shipped native CLI and Python authoring/eval roles |
| `AGENTS.md`, `list_skills()` example | Shows `io` in normal discovery | Actual result is `['documents', 'ffmpeg']`; `io` is stale and hidden |
| `AGENTS.md`, Architecture diagram | Places approval before expansion | Actual Python pipeline expands, validates, optimizes and preflights before the optional intent approval gate |
| `AGENTS.md` / `docs/NATIVE.md`, native skill contract | Describe `HandlerContext` and native Step/Intent equivalents as available | Those interfaces are not defined in the empty skill-api crate; see F11 |
| `docs/REQUIREMENTS.md`, refusal-routing discussion | Says all safety requirements are deterministically enforced regardless of routing | Qualify until F1–F4 are fixed; low refusal accuracy alone is not a hole, but current deterministic enforcement has real holes |
| `docs/PERFORMANCE.md`, hardware invariance assertion | “Quality never moves with hardware” and “Greedy decoding ... makes the same plan” | Its own paired table reports only 99.4%/98.7% identical decisions. State the observed sample result, not a universal invariance guarantee |
| `eval_backends.yaml`, machine-availability comments | Names files as present on “this machine” based on a July machine state | Make this explicitly historical or provide a live availability command; the user copied many GGUFs during this audit |
| `docs/TODO.md` / usage docs recommending `just eval-regression` | Present a no-op recipe as a regression gate | Correct command semantics and examples together; see F6 |
| `native/crates/knaif-llm/src/lib.rs` comments | Describe llama integration as a future spike | Update to implemented feature-gated behavior |
| `skills/ffmpeg/native/src/run.rs` module comments | Say no subprocess/file access and concat/execution deferred | Actual code probes files and supports execution/concat |
| `scripts/parity_check.py` module description | Says both runtimes use raw-path model selection | Current Python invocation uses a named `models.yaml` entry; describe the actual checksum/path/config identity guard |

The existing planning-parity plan and output-verification architecture note correctly acknowledge important unfinished work. They should remain visible; this audit does not label those gaps as newly discovered or treat their plans as implementations.

## Suggested order of follow-up work

1. Close F1 and F2 with deterministic negative execution tests before making any model-quality changes.
2. Unify native path resolution and link-aware containment (F3/F4); reject or correctly execute complete native plans (F5).
3. Make the regression and parity gates fail closed on missing or lossy evidence (F6/F7).
4. Complete the existing prompt parity investigation (F8) and then measure the actual shipped runtime with executing acceptance criteria.
5. Deliberately refresh compatible executing snapshots (F9), strengthen runtime artifact verification (F10), reconcile the native extension API and documentation (F11), and repair the training entry points before another training experiment (F12).

Keep model routing quality, deterministic safety, runtime artifact correctness, and cross-runtime parity as separate acceptance dimensions. A strong score in one does not substitute for another.
