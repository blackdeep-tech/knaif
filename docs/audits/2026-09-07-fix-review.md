# Review of the audit fixes — 2026-09-07

Reviewed branch `audit/f6-regression-fail-open`, from `fc8e86a` through `92399f4`, against the original [core-principles audit](2026-09-07-core-principles-and-rtx5080.md) and the project's deterministic safety, dual-runtime parity, and executing-evaluation requirements.

**Disposition: changes still needed.** The branch closes the direct internal-tool execution hole, restores the destructive-intent confirmation requirement, explicitly refuses unsupported native chains, and repairs training paths. However, native sandbox containment remains bypassable in two cases, and the regression/parity fixes still permit false passes. The confirmation fix also introduces a terminal-clarification regression.

This review makes no implementation, contract, configuration, corpus, or snapshot changes. Its only tracked addition is this report. Reproductions use audit-owned fixtures and mocked native inference. Tests/builds create normal generated artifacts. The earlier real-model evaluation is evidence about `fc8e86a`, **not a post-fix evaluation**.

## Findings requiring follow-up

### R1 — P1: validate and consume the same resolved native input

**Location:** `skills/ffmpeg/native/src/run.rs:114–115`, also `149–157` and `196–201`. Introduced check in `a2c029d`; incomplete closure of F3. **Reproduced on Windows with the rebuilt current native CLI.**

`assert_input_in_sandbox("clip.mp4", sandbox)` checks `sandbox/clip.mp4`, but `probe_input(Path::new(input), ...)` still receives the original relative string. FFprobe and the rendered FFmpeg command consequently open `cwd/clip.mp4`. The checked file and consumed file differ whenever the process working directory differs from the sandbox. Concat has the same problem and also renders the original input list.

Reproduction: create valid, audit-owned `clip.mp4` files in separate `sandbox/` and `outside/` directories. Start the native CLI with `cwd=outside`, `--sandbox=<absolute sandbox>`, and the mock plan below. Set `KNAIF_LLM_BACKEND=mock`, `KNAIF_LLM_MOCK_RESPONSE` to the JSON, and `KNAIF_SKILLS_ROOT` to the repository's absolute `skills` directory.

```json
{"plan":[{"tool":"strip_audio","args":{"inputs":["clip.mp4"],"output":"<absolute sandbox>/review-out.mp4"}}]}
```

The dry-run exits 0 and renders `ffmpeg -y -i clip.mp4 -an -c:v copy <sandbox>/review-out.mp4`. The real run without `--yes` reaches the destructive-command confirmation error **after probing the outside file**. No output was written. Using a working directory without the clip instead breaks a valid sandbox-relative request.

Return the resolved, checked input path and use it for probing, recipe construction, and rendered commands, including every concat input. Add a test where cwd and sandbox contain different files with the same name; checking the validation helper alone does not exercise this failure.

### R2 — P1: lexical `..` removal precedes symlink resolution

**Location:** `native/crates/knaif-core/src/sandbox.rs:39–47`. New shared primitive in `a2c029d`; incomplete closure of F4. **Source-reviewed Rust defect, with the relevant POSIX filesystem behavior reproduced in WSL.**

`resolve_real` lexically normalizes the whole path before canonicalizing its existing ancestor. On POSIX, resolving a symlink and then `..` is different from removing both components before following the link:

```text
sandbox/link -> outside/deep
requested: sandbox/link/../secret.txt
lexically normalized and checked: sandbox/secret.txt
actual filesystem target: outside/secret.txt
```

The guard accepts the first path while native skills retain the original path for I/O. For example, documents' `input_path` checks `p` and returns that same uncanonicalized `p` (`skills/documents/native/src/run.rs:511–519`). Thus a link followed by a parent component can still escape the sandbox on Linux/macOS; the same primitive also checks outputs.

A disposable WSL fixture demonstrated that `Path(os.path.normpath(p)).resolve()` stays inside the sandbox, while `p.resolve()` points outside and `p.read_text()` reads the audit-owned outside file. This is an OS-semantics reproduction, **not a claim that the Linux Rust binary was run**; WSL had no Rust toolchain available. The current Windows junction tests cover a simple link escape, not this POSIX link-plus-parent case.

Preserve filesystem traversal order: resolve links before interpreting subsequent parent components, including when handling a nonexistent output tail. Add Unix tests for existing inputs and new outputs below a symlink followed by `..`; keep the Windows junction tests as a separate case.

### R3 — P2: missing comparison identity still passes regression checks

**Location:** `python/core/knaif/evalsuite/snapshot.py:66–83`. Added in `2d18ce5`; incomplete closure of F6. **Reproduced against the current function.**

Verifier and population checks run only when *both* scoreboards declare the field. A current scoreboard that omits `verifier` and/or `total` can still be certified against a baseline that declares them, provided the numeric metrics are present. This bypasses the newly added compatibility guard without demonstrating a comparable executing evaluation.

```python
from knaif.evalsuite.snapshot import diff_snapshots

baseline = {
    "verifier": "success", "total": 847,
    "outcome_accuracy": 1.0, "avg_knaif_score": 1.0,
}
current = {"outcome_accuracy": 1.0, "avg_knaif_score": 1.0}
assert diff_snapshots(baseline, current)["passed"] is True  # observed
```

When the baseline declares comparison identity, require the current scoreboard to declare matching identity. Missing/null verifier or population should fail with an actionable error, just as missing baseline-reported metrics now do. Test missing fields separately from explicitly unequal values.

### R4 — P2: plan-mode parity still conflates different paths and values

**Location:** `scripts/parity_check.py:218–221`, with `canon_plan_step` / `_canon_scalar` at `167–193`. Incomplete closure of F7 in `cdbdc70`. **Reproduced with the actual comparison keys.**

Command mode now uses argv positions and the shared cwd correctly. Plan mode still ignores `cwd` and routes every string through basename normalization. Two valid `inspect_document` plans for `a/report.pdf` and `b/report.pdf` consequently have identical keys even when the caller supplies `cwd="C:/work"`. `compare` uses those keys to accept a match. This matters for the supported `--mode plan` and batch parity workflow.

```python
import runpy
Outcome = runpy.run_path("scripts/parity_check.py")["Outcome"]

def plan(path):
    return Outcome(kind="plan", plan=[
        {"tool": "inspect_document", "args": {"input": path}}
    ])

assert plan("a/report.pdf").key(cwd="C:/work") == \
       plan("b/report.pdf").key(cwd="C:/work")  # observed false match
```

Non-path strings are also affected: aspect values `4/3` and `16/3` both normalize to `str:3`. Normalize plan paths using their argument contracts and the shared cwd, preserving non-path strings. Exercise plan comparisons as well as rendered-command comparisons in the parity self-test.

### R5 — P2: destructive-intent propagation blocks terminal clarification

**Location:** `python/core/knaif/agent.py:693–704`. Regression introduced by `bfb544b`. **Reproduced with the real FFmpeg skill and disposable sandbox.**

A destructive intent can deterministically expand into a harmless terminal `clarify`, for example `prepare_for_platform` with an unknown platform. The new inherited-category check runs before that terminal handler and raises a confirmation error instead of returning the question. The earlier `all_terminal` branch explicitly skips approval for these workflows (`agent.py:558–563`), but the new execution guard still blocks them.

```python
agent.execute_plan({"plan": [{
    "tool": "prepare_for_platform",
    "args": {"inputs": ["clip.mp4"], "platform": "no_such_platform"},
}]}, dry_run=False, confirmed=False)
```

Observed: `ValueError: 'prepare_for_platform' is a destructive intent; requires confirmed=True when dry_run=False (blocked before executing 'clarify').` No execution is needed to ask which platform the user meant. This turns a recoverable interaction into an error and cannot be resolved by the normal terminal-only approval path because that path deliberately skips the callback.

Preserve the inherited safety gate for actionable workflows while allowing terminal-only clarification/refusal results. Add a destructive intent that expands to `clarify` to the trust-boundary tests; retain the tests proving that expansion into safe execution leaves cannot erase confirmation.

## Status of the attempted fixes

| Original finding | Review result |
|---|---|
| F1: public access to internal workflow tools | Direct public/model validation now rejects internal tools; trusted expansion still works. The original reproduced entry point is closed. |
| F2: destructive intent loses its category | Unconfirmed execution is now blocked before the first expanded handler. Preserve that protection while fixing R5. |
| F3: native input containment | Absolute outside inputs are now checked, but relative inputs still violate the boundary: R1. |
| F4: links escape native sandbox | Simple junction escapes are covered; filesystem traversal remains incorrect for link-plus-parent paths: R2. |
| F5: native silently truncates plans | Fixed by explicit refusal of plans with more than one step. Current rebuilt CLI emits the refusal without rendering/executing the first command. Full native chain execution remains deliberately deferred. |
| F6: regression gate fails open | Missing current paths, declared identity mismatches, and missing metrics are addressed. Missing identity is still accepted: R3. |
| F7: lossy parity normalization | Rendered-command arithmetic and different source directories are addressed. Plan mode remains lossy: R4. |
| F12: stale training paths | Union dataset generation succeeds with 738 rows: 404 FFmpeg and 334 documents. Preference/distillation path repairs were source-reviewed; no training was performed. |

F8 (prompt/retrieval parity), F9 (compatible executing snapshots), F10 (runtime output criteria), and F11 (native extension interfaces) remain acknowledged work, not regressions introduced by these commits. Adding a sandbox re-export to `knaif-skill-api` does not supply the still-missing `HandlerContext` / `Step` / `Intent` interfaces.

## Verification performed on the branch

All of these checks passed at `92399f4`:

- Focused regression/trust-boundary tests: **20 passed**.
- Full Python suite: **1,717 passed, 1 skipped, 16 warnings**.
- `cargo test --workspace`, and a fresh `cargo build -p knaif-cli` for CLI reproductions.
- `uv run --frozen ruff check .`, `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`.
- `uv run --frozen mypy python/core/knaif/`: 48 source files checked.
- `uv run --frozen python scripts/parity_check.py --self-test`.
- Union dataset builder using a temporary output path: 738 rows.

Targeted reproductions above expose behavior not covered by those passing suites. Native build/tests use the default feature set, not a native llama.cpp/PDFium release build. No new real-model sweep, website checks, release packaging checks, training, promotion, or snapshot re-lock was performed for this fix review. No Ollama evaluation was used.

## Documentation and original-evaluation follow-up

The original full evaluation finished on `fc8e86a`; `matrix.json` records that SHA and `main`. It used the requested `knaif-qwen3-4b-v1-q4_k_m.gguf` through llama.cpp, with both skills' fixtures regenerated. Its saved run remains `evals/runs/2026-09-07_rtx5080-core-audit_success/` and already has an `evals/INDEX.md` entry. Do not present its results as validation of these later commits.

| Skill | Expected outcome correct | Conditional executing-verifier average |
|---|---:|---:|
| FFmpeg | 764/847 (90.20%) | 97.14% over 575 scored plans |
| Documents | 160/164 (97.56%) | 100% over 153 scored plans |

Further review of the saved FFmpeg rows found 29 scored plans with partial/zero criterion scores, as well as unscored routing/error outcomes. Two useful follow-ups discovered while finishing that evaluation are **pre-existing issues, outside the six fixes**:

- **Single-output chains are not executed faithfully by the eval harness.** `runner.py:37–58` selects the last rendered command; only rows declaring multiple outputs take the chain-execution branch (`runner.py:211–230`). The FFmpeg artifact runner then replaces that final command's first input with the original fixture (`skills/ffmpeg/python/_reporting.py:233–240`). For `ffmpeg_273` (rotate 90 degrees, then compress), the saved plan contains both correct intents, but the artifact is only the compression command. The materialized output remains 1920×1080 rather than 1080×1920. This is evidence of a harness limitation, not proof the model omitted rotation. Single-final-output chains also need their intermediate commands executed.
- **Thumbnail scale type errors escape structural validation.** Five saved errors across `ffmpeg_236` and `ffmpeg_237` contain numeric `scale` values and fail when `_parse_scale` calls `.strip()` (`skills/ffmpeg/python/_engine.py:225–235`). `create_thumbnail` lists `scale` without a type schema (`skills/ffmpeg/tools.yaml:62–69`). Add declarative typing/normalization or an intentional validation error before handler execution; an `AttributeError` is not a useful model-plan validation result.

Documentation corrections to queue with the next implementation pass:

- `docs/TODO.md:331–405` marks F3/F4/F6/F7 fixed without the remaining qualifications above. Keep those entries partial until the relevant reproductions fail safely.
- The new `evals/INDEX.md:93` row overstates hardware invariance ("Quality held across both hardware moves" / "greedy decoding is not moving quality with hardware"). This run has no paired historical per-utterance comparison on an identical code/corpus population; similar aggregate figures cannot establish that claim. The row also still describes F6 as wholly unfixed, despite the subsequent partial fix.
- The original audit's FFmpeg category paragraph connects clarify scores to F2 without demonstrating causation. Those routing scores do not establish a confirmation-boundary failure; keep model routing and deterministic execution findings separate.
- `justfile:589` still says native previews only the first chain step; this branch now explicitly refuses multi-step plans.

The original audit is retained as the record of the pre-fix code. This report is the review of the subsequent fixes and the place to track the remaining counterexamples.
