# Faster release evidence: test what changed, and test it faster

**Status:** Draft · **Created:** 2026-10-14 · **Completed:** —
**Owner:** evals / release · **Ref:** [release-1.3.0](2026-09-30-release-1.3.0.md) (*Gate decision
rules*) · [RELEASE.md §4](../RELEASE.md) · [skill-quality-lifecycle](2026-09-10-skill-quality-lifecycle.md)
**Release:** —

> **Status note:** asked for by the owner on 2026-10-14, during 1.3.0's Windows evidence chain
> (~12 h): *"I will wait it this time but for the next runs I'd like to find a better/faster
> solution to test. First to test only the things that were changed. Next to find a way to test it
> faster. 12 hours is not ok."* Nothing here changes 1.3.0, whose rules were registered before its
> runs. The next release adopts whatever is decided here **before** its first evidence run.

**Goal:** Cut a release's evidence from ~12 h on this box to ~2–3 h for a full release and under
1 h for a small one, without weakening any bar: same floors, same safety rule, same verdicts.

## Where the 12 hours go (1.3.0, Windows, measured 2026-10-14)

| Stage | Wall time | Rows | What costs |
|---|---:|---:|---|
| L3, 4 cells on CUDA | 59 min | 942 | both runtimes plan every row, one process per row |
| L4 GPU, 8 cells | 3 h 17 min | 4,100 | per cell: ffmpeg 35–51 min, documents 4.5–7 min |
| L4 4B CPU, in full | ~4.3 h (1.2.0) | 1,025 | CPU generation, one process per row |
| L4 1.7B CPU, in full | ~2.7 h (1.2.0) | 1,025 | same |
| Daemon plan equality | ~1–1.5 h | 2 × 1,025 × 2 | the whole corpus twice per model |
| Linux (WSL) | ~3.5 h | | the same shape again |

Three things drive it:

1. **Every cell re-runs every row**, although most rows go through code the release did not touch.
   1.3.0 changed the daemon, prompting, the installer and two documents dry-run checks; the
   planning path (prompt, model, generation settings) did not change at all.
2. **One process per row.** L4 measures the shipped path, `knaif run` started fresh for each
   request: a model load (~1–2 s on the GPU) and backend setup every time, ~7,000 times on Windows.
   The same 861 ffmpeg requests plan in **~400 s with one load** (`plan --batch`, 2026-10-06).
3. **CPU cells measured in full** (~7 h of the 12), because plans are not trusted across backends
   without a confirmation step.

## What must not change

- The bar: `native ≥ max(S2 floor, Python − 0.02)` on outcome and knaif score, complete coverage,
  every required slice, safety 100% **on the binary**, over the **whole** safety corpus.
- Pre-registration: the method for a release is chosen and written into its run script before any
  result, and a change is an amendment written before the stage it affects.
- Every verdict stays tied to the artifact it describes (binary and model sha256, fingerprints).
- What L4 claims: the shipped binary, executing for real, graded on the files it produces.

Every shortcut below is a **reuse rule** (like 1.2.0's T9b and T15s): it must say what it reuses,
why that is safe, and what fresh measurement confirms it, and the gate must mark the cell as
reused/composed so the release notes can say so.

## Part A — test only what changed

**A1. Plan-equality pre-check (cheap, every release).** With one model load per model × backend,
`plan --batch` the whole corpus on the frozen binary and compare with the accepted run's plans, row
by row (the decision and canonical levels of `scripts/flip_rate.py`). ~7 min per model on CUDA.

- Rows whose plan is **identical** and whose execution path did not change keep their accepted
  verdict; the cell is recorded as *reused, confirmed by plan equality*.
- Rows whose plan **differs** are re-run in full (`native --only`), and `compose` swaps them in —
  the T9b mechanism, with the confirmation T9b relied on now built in.

**A2. Execution scope from the diff.** A plan identical to before can still execute differently if
the code that renders or runs it changed. Map changed files to tools: each skill's native crate and
Python handlers are per tool, so `git diff <last accepted freeze>..<this freeze>` names the tools
whose rendering/execution changed, and every row whose plan uses one of them is re-executed. A
change to shared execution code (the chain executor, sandbox, path handling) re-executes every row.

**A3. Finer fingerprints in the gate.** Today any change to `native/` makes every L4 record stale
(the `native` fingerprint hashes the whole tree). Split it into planning (prompt, retrieval,
generation, parse/validate/expand) and execution (per skill, per tool where the layout allows), so
the gate itself says which records a change can affect. This is what makes A1/A2 checkable rather
than a judgement made in a run script.

**A4. When everything changed** (a new model, a new prompt, new generation settings, new
llama.cpp): A1 finds most plans differ and the release falls through to the full run — Part B makes
that run fast.

## Part B — make a full run faster

**Constraint (owner, 2026-10-14): nothing runs in parallel on this machine.** Parallel work on one
box distorts the timings and loads the owner's machine too much. Speed comes from doing less (Part
A), loading the model once (B1) and moving work to other machines (Part C), not from more threads
or more processes here.

**B1. Split L4 into planning and execution.** Plan every row in one batch per model × backend (one
model load instead of one per row), then execute the planned commands and grade the outputs one
after another — execution needs no model. Keep a **cold-start sample** per cell (e.g. 30 pre-drawn
rows run as today, one process each) so startup failures (backend loading, model resolution,
PDFium) are still caught, and require its plans to equal the batch plans for those rows.

- Needs: `knaif plan --batch` to emit exactly what `knaif run` would execute (it emits plans; the
  rendered commands come from the same code), and an execute-from-plan path in the lane.
- Risk to answer first: does batch planning give the same plan as a fresh process? The daemon check
  says yes on 2026-10-06 and 2026-10-11 (byte-identical in process vs daemon); B1 needs the same
  check for `plan --batch` vs `run`, on every backend.

**B2. Cross-backend cells by confirmation.** Vulkan and CPU differ from CUDA only in arithmetic.
Batch-plan each backend (B1), re-run in full only the rows whose plan differs from CUDA, and add a
**fresh pre-drawn confirmation sample** on that backend (the step 1.3.0's amendment found missing).
1.3.0's 4B CPU cell (~4.3 h) would have been ~30–40 min.

**B3. Daemon plan equality on a stratified sample** (every tool, both skills, ~150 rows) instead of
the whole corpus twice; the full corpus only when the daemon or the generation path changed.

**B4. Smaller things.** Regenerate fixtures once per stage, not per cell; run safety once per
binary × backend rather than per cell where the binary and model are the same; reuse L3's Python
side between releases when the Python fingerprint did not change.

## Part C — move the CPU cells to CI

The CPU cells are ~7 h of Windows and ~1.25 h of Linux, and need no GPU. GitHub's standard runners
can run them (checked 2026-10-14 in GitHub's docs, *Actions limits* and *GitHub-hosted runners*):

| | |
|---|---|
| Cost | free and unlimited on standard runners for a public repository |
| Runner | Linux and Windows x64: 4 CPU, 16 GB RAM, 14 GB SSD; macOS arm64: 3-core M1, 7 GB |
| One job | at most **6 h**, then terminated |
| One workflow run | 35 days |
| Parallel jobs | 20 (5 macOS); up to 256 jobs in one matrix |
| Cache | 10 GB per repository (both models are ~3.9 GB) |
| GPU | only on paid larger runners (Team / Enterprise) — the GPU cells stay on this box |

**C1. Shard each CPU cell.** Rows are independent (one process each), so a cell splits into
pre-drawn `--only` shards that each finish well inside 6 h on a 4-CPU runner (e.g. 10 shards of
~100 rows), run as one matrix on separate machines. A final job joins the shards with `evalsuite
compose`, runs both safety sets on the binary and grades the cell with `accept-native`. All four
CPU cells (Windows and Linux, both models) take ~1–1.5 h of wall time and none of the owner's.

**C2. The exact frozen files.** `workflow_dispatch` only (no fork can trigger it). Each job downloads
the frozen artifact (the Package run's files or the draft release's) and checks its sha256; models
come from Hugging Face by pinned sha256, cached. Boards and verdicts come back as workflow
artifacts; the acceptance records are written on this box from them, so nothing is committed by CI.

**C3. Decide before using it (a new measurement condition).** The cell is measured on GitHub's
server CPUs, not this box's Ryzen: llama.cpp may pick a different CPU code path and a few plans can
differ from 1.2.0's. Arguably closer to users' machines, but it must be written into the release's
gate rules before the run. CI timings are not speed figures (L4 does not gate on speed; speed stays
measured here). Logs are public on a public repo; the runner's own account is already exempt from
the path rule (AGENTS.md, rule 5).

## Tasks

- [ ] **T0. Measure before building.** On the frozen 1.3.0 binary: (a) time `plan --batch` for one
      ffmpeg cell vs today's 44 min; (b) time executing those plans one after another (no model);
      (c) compare `plan --batch` plans with the cell's `run` plans, every row, on CUDA, Vulkan, CPU;
      (d) time one 100-row CPU shard on a GitHub Windows and Linux runner. This says whether B1
      and C1 hold and what they save; nothing is built before it.
- [ ] **T0b. C1/C2 workflow:** a `workflow_dispatch` CPU-cell workflow (shard matrix, download and
      verify the frozen files, cached models, compose + safety + `accept-native` job, artifacts).
- [ ] **T1. Write the reuse rules** (A1, A2, B1, B2, B3) as gate-decision rules in the shape of the
      1.3.0 ones, for the owner to approve, including how the gate and the release notes mark a
      reused or composed cell.
- [ ] **T2. A3: split the `native` fingerprint**, with tests that a change to one tool's code stales
      only that tool's records.
- [ ] **T3. B1 in the lane:** one-load batch planning + sequential execution + the cold-start sample, behind a
      lane option, with tests; validated by re-running one 1.3.0 cell both ways and requiring the
      same verdict and the same per-row grades.
- [ ] **T4. A1/A2 in the run scripts:** the plan-equality pre-check and the diff-to-tools mapping,
      producing the `--only` sets.
- [ ] **T5. Codex audit** of T1–T4 before the next release uses them.
- [ ] **T6. RELEASE.md and EVAL_FRAMEWORK.md** describe the new method; the next release index's
      gate rules adopt it.

## Open questions for the owner

1. How much reuse is acceptable for a **minor** release (new platform, new features) vs a **patch**?
   E.g. patch: A1 + A2 only; minor: B1 + B2 full re-measurement at B-speed.
2. The cold-start sample size per cell (30? 50?) and whether it is pre-drawn once per release.
3. Part C: whether a CPU cell measured on GitHub's runners (not this box) is acceptable evidence
   for `windows-x64|cpu` and `linux-x64|cpu`, and whether macOS CPU moves there too.
