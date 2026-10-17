# Faster release evidence: test what changed, and test it faster

**Status:** Active · **Created:** 2026-10-14 · **Completed:** —
**Owner:** evals / release · **Ref:** [release-1.3.0](2026-09-30-release-1.3.0.md) (*Gate decision
rules*) · [RELEASE.md §4](../RELEASE.md) · [skill-quality-lifecycle](2026-09-10-skill-quality-lifecycle.md)
**Release:** —

> **Status note:** asked for by the owner on 2026-10-14, during 1.3.0's Windows evidence chain
> (~12 h): *"I will wait it this time but for the next runs I'd like to find a better/faster
> solution to test. First to test only the things that were changed. Next to find a way to test it
> faster. 12 hours is not ok."* Nothing here changes 1.3.0, whose rules were registered before its
> runs. The next release adopts whatever is decided here **before** its first evidence run.
>
> **Decided 2026-10-14 (owner), after the scaling question *"imagine 20 skills — 5 days"*:**
> release L4 cells run on a **coverage subset** of each corpus (Part D); release cells are graded by
> **per-row regression** against the accepted Python run, not by slice rate floors (Part D); a
> **content-addressed result store** is the single reuse mechanism, replacing the rerun-set /
> compose / T9b / t15s rules (Part E); the CPU cells **and the macOS Metal cells** move to GitHub
> runners, only CUDA and Vulkan stay on this box (Part C). Parts A–C keep their text; Parts D–E say
> how they compose.
>
> **Active since 2026-10-14 (owner), with a start condition: no task starts, and no T0 measurement
> runs anywhere (box or CI), until 1.3.0 is published.** Then: T0 first (CI-only parts before the
> box timings), the code tasks on one `feat/*` branch from `main` with one PR, Codex audit, docs.
> **The first release after 1.3.0, whatever its number, adopts the method** — its index's gate
> rules cite the subset, the store's reuse table and the never-reused samples before its first
> evidence run. The `Release:` field is set when that release opens.
>
> **Decided 2026-10-15 (owner), closing the open questions:** the cold-start sample is **30 rows
> per cell, drawn once per release** (B1, E4); the store is **committed under `evals/store/`**
> (E1); **vast.ai is not adopted** — the Linux GPU cells, model acceptance and training stay on
> this box (WSL), Part F is kept as research and its tasks are dropped; a **new skill's first
> native release runs one full-corpus native cell per platform** before the subset is trusted for
> it (D1). No open questions remain.
>
> **Added 2026-10-15 (owner):** a **corpus review** before the subset is derived — duplicates
> out, missing and more complex rows in, kept rows never renumbered or reworded, new hard rows
> under their own tag with a floor set from the re-lock, one re-lock per model as part of the next
> fine-tune cycle's full run (Part G); and what a **published model or skill brings with it** so
> the gates here can judge it (Part H). Where models are trained and skills developed before they
> are published is outside this plan.
>
> **Order after 1.3.0 is published (owner, 2026-10-15):** this plan is release process, so it is
> done here, where releases are planned, frozen and evaluated. (1) T0: the CI parts on public
> runners, the box timings when the owner schedules them; (2) the code tasks here and the corpus
> review (T7) side by side, neither needing the GPU; the CI-cell workflow lands on `main` early
> (Part C); (3) the reference run of the current models on the reviewed corpus (G2.3); (4) the
> next release uses the method. Its contents are assigned when its index opens; new models are
> expected to be among them.
>
> **Started 2026-10-16** (1.3.0 published that day; the start condition is met). T0 goes first.
>
> **Amended 2026-10-17 (owner), after T0's CI half (C5):** the CPU and macOS cells do **not** move
> to GitHub runners — CPU there grades correctly but 3× slower, and macOS is unusable. **First the
> process itself is made faster on this one machine** (Parts A, B, D, E: test only what changed,
> load the model once, the coverage subset, the result store); where the cells run comes after.
> Other machines stay an option for then: the maintainer's spare machines (a second Windows PC, a
> laptop's WSL, the M1 Pro Mac), or rented AWS machines, priced in C6.

**Goal:** Cut a release's evidence from ~12 h on this box to ~2–3 h for a full release and under
1 h for a small one, without weakening any bar: same floors, same safety rule, same verdicts.
**At twenty skills the cost must grow with what the release changed, not with the number of
skills** — the five-factor product below is what makes today's method scale to days.

**Portable by design (owner, 2026-10-16).** The method and its tools must work in any checkout of
this repository or of a fork, for any skill, not only on the release maintainer's machine and for
the two reference skills: no path, repository, branch or machine name is hardcoded (they come from
arguments, `git` and the environment); the CI cell workflow runs in a fork on that fork's own
runners; the subset, the store and the gate read only what is in the checkout. A fork or a
community skill gets the same evidence method and the same verdicts.

## Why it does not scale

Evidence cost is a product: **utterances × cells × models × per-row cost × releases**. 1.3.0 on
Windows is 1,025 utterances × 7 platform/backend cells × 2 models, one process per row, everything
re-measured every release. Twenty skills at ~500 utterances is 10,000 utterances, so the same
method takes ~5 days. Parts A and B lower the per-row cost and the cross-release repetition; they
leave the first two factors alone, and those are the two that grow with skills:

- **Utterances.** The corpus judges a *model*: ffmpeg's 328 rows carry 861 utterances because a
  row has up to six paraphrases, and paraphrases test routing robustness. A release asks a
  different question — does the shipped binary on this platform do what the accepted runtime did —
  and for that question a paraphrase exercises the same tool, arg shape and renderer as its row.
  Counting strata (expected tool × outcome × tag set) gives 180 for ffmpeg and 74 for documents:
  one row per stratum plus every chain and edge row covers every code path at 4–5× fewer rows.
- **Cells.** A plan depends on (utterance, model, planning code, backend arithmetic) and nothing
  else; an outcome on (plan, execution code of the tools it uses, OS, fixtures) and nothing else.
  So on one OS the backends share execution wherever the plan is identical, the 4B and 1.7B share
  execution wherever they planned the same, and a release whose planning fingerprint is unchanged
  shares every plan with the previous one. Today that reuse is four hand-written rules per release
  script; Part E makes it one mechanism.

At twenty skills, with Parts B–E in place (estimates, to be replaced by T0 measurements):

| Release kind | Box time, today's method | Box time, this plan |
|---|---:|---:|
| Patch: planning untouched, one skill changed | ~5 days | **< 1 h** (plan-equality on CUDA, execute ~150 rows) |
| Minor: new prompt or model | ~5 days | **~3–4 h** (plan 2 models × CUDA + Vulkan once; execute each distinct plan once per OS) |
| Model promotion: full corpus, Python acceptance | — | ~4 h per model, decoupled from releases (stays on this box; Part F not adopted) |

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

- The bar's values: the S2 floors, the 0.02 tolerance to the accepted Python score, safety 100%
  **on the binary** over the **whole** safety corpus, complete coverage of whatever population the
  cell is defined over. *Amended 2026-10-14 (owner):* on a release cell the population is the
  coverage subset and the check is per-row regression (Part D); the S2 slice floors stay the
  full-corpus Python acceptance bar, where their populations are large enough for a rate.
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
after another — execution needs no model. Keep a **cold-start sample** per cell (**decided
2026-10-15: 30 rows, drawn once per release** — stratified so every tool appears, the same rows in
every cell, written into the run script before any result — run as today, one process each) so
startup failures (backend loading, model resolution, PDFium) are still caught, and require its
plans to equal the batch plans for those rows.

- Needs: `knaif plan --batch` to emit exactly what `knaif run` would execute (it emits plans; the
  rendered commands come from the same code), and an execute-from-plan path in the lane.
- Risk to answer first: does batch planning give the same plan as a fresh process? The daemon check
  says yes on 2026-10-06 and 2026-10-11 (byte-identical in process vs daemon); B1 needs the same
  check for `plan --batch` vs `run`, on every backend.

**B2. Cross-backend cells by confirmation.** Vulkan and CPU differ from CUDA only in arithmetic.
Batch-plan each backend (B1), re-run in full only the rows whose plan differs from CUDA, and add a
**fresh pre-drawn confirmation sample** on that backend (the step 1.3.0's amendment found missing).
1.3.0's 4B CPU cell (~4.3 h) would have been ~30–40 min. *Corrected by T0, 2026-10-17:* it
would not. That estimate assumed execution was a CPU row's cost; it is planning (12.5 of 13.2 s a
row, even in one batch), and finding the rows whose CPU plan differs means planning all of them.
On a GPU backend B2 still saves the execution of the matching rows (Vulkan vs CUDA plans differ on
63 of 861 ffmpeg rows, 5 of 164 documents). What shortens a CPU cell is not re-planning at all:
the store (Part E) when planning did not change, the subset (Part D) when it did.

**B3. Daemon plan equality on a stratified sample** (every tool, both skills, ~150 rows) instead of
the whole corpus twice; the full corpus only when the daemon or the generation path changed.

**B4. Smaller things.** Regenerate fixtures once per stage, not per cell; run safety once per
binary × backend rather than per cell where the binary and model are the same; reuse L3's Python
side between releases when the Python fingerprint did not change.

## Part C — move the CPU cells, and macOS, off this machine (CI measured; spare machines chosen)

The CPU cells are ~7 h of Windows and ~1.25 h of Linux, and need no GPU. GitHub's standard runners
can run them (checked 2026-10-14 in GitHub's docs, *Actions limits* and *GitHub-hosted runners*).
**Decided 2026-10-14 (owner):** the macOS cells go too — GitHub's macOS runners are Apple Silicon
with a GPU, and llama.cpp's own CI runs its Metal tests there — so both macOS Metal and macOS CPU
are measured on CI if T0(e) shows Metal actually loads in the runner VM. Only CUDA and Vulkan stay
on this box.

| | |
|---|---|
| Cost | free and unlimited on standard runners for a public repository |
| Runner | Linux and Windows x64: 4 CPU, 16 GB RAM, 14 GB SSD; macOS arm64: 3-core M1, 7 GB |
| One job | at most **6 h**, then terminated |
| One workflow run | 35 days |
| Parallel jobs | 20 (5 macOS); up to 256 jobs in one matrix |
| Cache | 10 GB per repository (both models are ~3.9 GB) |
| GPU | Linux/Windows: only on paid larger runners — CUDA and Vulkan stay on this box. macOS arm64: the M1 runner's GPU, Metal (to verify, T0e) |

**C1. Shard each CPU cell.** Rows are independent (one process each), so a cell splits into
pre-drawn `--only` shards that each finish well inside 6 h on a 4-CPU runner (e.g. 10 shards of
~100 rows), run as one matrix on separate machines. A final job joins the shards with `evalsuite
compose`, runs both safety sets on the binary and grades the cell with `accept-native`. All four
CPU cells (Windows and Linux, both models) take ~1–1.5 h of wall time and none of the owner's.

**C2. The exact frozen files.** `workflow_dispatch` only (no fork can trigger it). Each job downloads
the frozen artifact (the Package run's files or the draft release's) and checks its sha256; models
come from Hugging Face by pinned sha256, cached. Boards and verdicts come back as workflow
artifacts; the acceptance records are written on this box from them, so nothing is committed by CI.
*Decided 2026-10-15 (owner):* the cell workflow lands on `main` **early**, as a small
infrastructure change with no product code. GitHub offers a manually dispatched workflow only when
its file is on the default branch, and T0(d)/(e) must measure the real public runners (4-core
x64, Apple Silicon); private-repo runners are smaller and metered, so they would not predict them.
*Amended 2026-10-17 (owner):* not yet — the method is still an experiment. Until T0 shows it can
work, the workflow stays on this plan's branch and runs from there: a push to that branch whose
commit message contains `[t0]` starts T0's three fixed cells on the same public runners. That push
trigger is removed before the workflow is ever merged; `main` gets it only once the method is
adopted.

**C3. Decide before using it (a new measurement condition).** The cell is measured on GitHub's
server CPUs, not this box's Ryzen: llama.cpp may pick a different CPU code path and a few plans can
differ from 1.2.0's. Arguably closer to users' machines, but it must be written into the release's
gate rules before the run. CI timings are not speed figures (L4 does not gate on speed; speed stays
measured here). Logs are public on a public repo; the runner's own account is already exempt from
the path rule (AGENTS.md, rule 5).

**C4. Sharding at twenty skills.** Twenty parallel jobs, ~100 rows a job: a 2,500-row release
subset (Part D) on CPU is ~25 jobs, ~1 h of wall time, whatever the skill count on the box.
Fixtures and each skill's system requirements (ffmpeg, tesseract, …) are installed per job from
the skill's declared requirements, as `ci.yml` already does for the reference skills.

**C5. What T0 measured, and the decision it led to (2026-10-17).** Run `38976996643` (workflow
`evidence-cell.yml`, 2026-10-16), the published 1.3.0 files, ffmpeg, `knaif-qwen3-4b-v2`, 100 rows
drawn evenly across the corpus (20 on macOS); the boards were compared row by row with 1.3.0's
own CPU cells, measured on the maintainer's machine with the same binaries (sha256 equal):

| | Windows (4 cores) | Linux (4 cores) | macOS `macos-15` (M1, 3 cores, 7 GB) |
|---|---|---|---|
| Ran on | CPU, 37 layers | CPU, 37 layers | unknown: the placement probe itself hit the 900 s timeout |
| Median per row | 49.6 s (box: 15.8 s, **3.1× slower**) | 50.3 s (box: 16.2 s, 3.1×) | ~900 s: **16 of 20 rows timed out**; the 4 that finished were correct |
| Same grade as the box | **100 / 100** | **100 / 100** | — |
| Same plan as the box | 93 / 100 (7 differ in argument detail only, as C3 predicted; some differ between the two runners too) | 93 / 100 | — |

So CPU cells on CI are **correct** (the same verdict on every row) but **3× slower per row** than
this box, and macOS on GitHub's standard runner is **unusable** (≈15 min a row). The owner's
conclusion (2026-10-17): CI is only partly useful — it moves the CPU cells off this machine
only with heavy sharding, and macOS not at all. **Decided: optimize the process on this one
machine first** (Parts A, B, D, E); moving cells elsewhere comes after, and the maintainer's spare
machines are the first option then — a second Windows PC (Windows CPU), a laptop's WSL (Linux CPU,
and CUDA), the M1 Pro Mac (macOS, as in 1.3.0) — each recording its machine (CPU model, cores, OS)
next to the run, as `docs/PERFORMANCE.md` names the machine behind every figure. The CI workflow
stays on this plan's branch, unmerged.

**C6. Rented CPU machines, priced as the fallback (2026-10-17, not adopted).** If the spare
machines are too slow or not available when a release needs them: AWS `c7a.8xlarge` (32 cores,
EPYC 9R14, 64 GiB) in eu-north-1 costs **$1.76/h Linux, $3.23/h Windows on demand; $0.37 and
$1.77 on spot** (AWS's price files, 2026-10-17; Frankfurt and Ireland cost a little more). With
the coverage subset (~254 utterances, both models) a release's CPU cells are about an hour per OS:
**~$5 on demand, ~$2 on spot**; the full corpus about four times that. Untested: CPU generation is
bound by memory bandwidth, so how far several processes at once scale on one VM must be measured
(one 100-row shard at 1, 2, 4, 8 processes: ~$2 on demand) before any rule relies on it.

## Part D — a release corpus: coverage, not paraphrases

**Decided 2026-10-14 (owner).** Two questions, two instruments:

| Question | Instrument | Population | When it runs |
|---|---|---|---|
| Is this **model** good enough? | Python acceptance, `eval-accept` | the full `data/eval.jsonl`, every paraphrase; S2 slice floors | when the model, prompt, retrieval or generation settings change; locks the snapshot |
| Does the **shipped binary** on this platform do what the accepted runtime did? | L4 release cell, `accept-native` | the **coverage subset** (below); per-row regression | every release, every cell |

**D1. The subset is derived, deterministic and committed.** `evalsuite release-subset <skill>`
reads `eval.jsonl` and picks, in corpus order: the first row of every stratum (expected tool ×
expected outcome × tag set), every row tagged `chain2`/`chain3`/`edge`/`safety`, and one utterance
per selected row (the first). It writes `skills/<name>/data/release_subset.json` (row ids and
utterance indexes, plus the sha256 of the corpus it was derived from); a test fails when the
corpus changed and the subset was not regenerated. Sizes today: ffmpeg ~180 rows of 328 (861
utterances), documents ~74 of 143 (164). The safety corpus is never subsetted.

*Decided 2026-10-15 (owner):* a skill's **first native release runs one full-corpus native cell per
platform** (every backend that ships there) before the subset is trusted for it; the subset applies
from its second release on. The same shape as Python acceptance: a full measured baseline first.

**D2. The release bar on the subset is per-row regression**, not a rate: for every row in the
subset, the binary's graded outcome may not be worse than the accepted Python run's on the same
row and model (pass → pass; a Python fail may stay a fail or pass), the subset's aggregate
`outcome_accuracy` and `avg_knaif_score` within 0.02 of the Python run's on the same rows, coverage
1.0, safety 100% on the binary. The S2 slice floors are not applied to the subset: several slices
fall under `min_rate_rows` there and a rate over them is noise. `accept-native` gains a
`--population subset` mode that reads the Python verdicts for the subset rows from the model's
locked snapshot run (which must carry per-row grades — it does since policy 3).

**D3. What the subset cannot see** and the full corpus still does: paraphrase robustness (a model
property, unchanged by a runtime release) and rate-level drift inside a slice. Both belong to the
model-promotion event, where the full corpus runs. A release that changes the planning fingerprint
(prompt, retrieval, generation settings, model) is a model-promotion event for this purpose and
runs the full corpus through Python acceptance first; its L4 cells still use the subset.

**D4. The L3 corpus follows.** L3 compares the two runtimes' plans; it uses the same subset, and
its Python side is a lookup in the store (Part E) when the Python planning fingerprint is unchanged.

## Part E — a content-addressed result store

**Decided 2026-10-14 (owner): one mechanism replaces the rerun-set / compose / T9b / t15s rules.**

**E1. Two tables, keyed by exactly what determines the value.**

```text
plans/     key = (skill, row id, utterance index, model sha256, planning fingerprint, backend arithmetic class)
           value = the validated plan (canonical JSON) and its rendered commands
outcomes/  key = (plan hash, execution fingerprint of the tools the plan uses, OS, fixture-set sha256, verifier)
           value = the graded outcome: per-row grades, produced-file hashes, failure text
```

Backend arithmetic class is `cuda` / `vulkan` / `cpu` / `metal` per model: it is the only reason two
backends can plan differently. The planning fingerprint is A3's planning half; the execution
fingerprint is A3's per-tool execution half, so a change to one tool's code invalidates only the
outcomes whose plan uses that tool. The store lives under `evals/store/` as content-addressed files,
committed like today's run folders (small: a plan is ~1 KB, an outcome ~2 KB). *Decided 2026-10-15
(owner):* committed, not kept as CI artifacts — auditable for as long as the repo exists.

**E2. A cell is composed from the store.** For a cell (model × OS × backend × skill) the gate
computes the key set the subset needs, looks every key up, and the **fresh work is the missing
keys only**: missing plans are batch-planned (B1) on that backend, missing outcomes are executed on
that OS. Everything else is reused, and the board records per row whether its plan and its outcome
were *measured in this release* or *reused from <release, cell>*. Release notes print the two
counts per cell. The composed-cell wording ("sampled, not measured in full") goes away: every row
is measured, the question is only when.

**E3. What is reused across what.**

| Reuse | Why it is safe | What confirms it |
|---|---|---|
| CUDA → Vulkan → CPU execution on one OS | an outcome depends on the plan, not on which backend produced it | the plan key differs per backend, so a backend that plans differently gets its own execution |
| 4B ↔ 1.7B execution | same plan hash → same commands → same files | plan equality is the key, nothing to confirm |
| Release N → N+1 plans | planning fingerprint and model unchanged | the fingerprint is in the key; B1's cold-start sample re-plans ~30 rows per cell fresh and must hit the stored plans |
| Release N → N+1 outcomes | execution fingerprint of the plan's tools unchanged | the per-tool fingerprint is in the key; a shared-execution-code change (chain executor, sandbox, paths) is part of every tool's fingerprint and stales everything |
| Python side of L3 | Python planning fingerprint unchanged | same as plans |

**E4. What is never reused.** Safety on the binary (per binary × backend, every release); the
cold-start sample (B1); the fresh pre-drawn confirmation sample per backend (B2) — these are the
measurements that catch what a key cannot express (a loader that fails, a backend that silently
falls back to CPU, a model file that resolved wrongly), and they stay small and always fresh.

**E5. Pre-registration holds.** The subset, the key definitions and the fingerprints are in the
repo before the freeze; the run script records which keys were missing and why. An amendment is
still an amendment.

## Part F — rented Linux GPUs (vast.ai): researched, not adopted

**Decided 2026-10-15 (owner): not adopted.** The Linux GPU cells, full-corpus model acceptance and
training stay on this box (WSL for Linux). No eval image, no `scripts/vast_cell.sh`, no T0(f)/(g).
The research below is kept so the question need not be re-done if the box ever becomes the
bottleneck; F3's credibility rule is what any future rented-host evidence would have to satisfy.

**Asked by the owner 2026-10-15:** cheap GPUs can be hired by the hour on vast.ai, turned on and
off from a CLI, and used instead of this box for evaluation and training, with cells running in
parallel on separate machines. Researched the same day from vast.ai's docs
(docs.vast.ai: *concepts*, *vms*, *templates*, *cli*, *billing*, *security FAQ*) and the
`vast-ai/base-image` repository; figures are what the docs and price pages said on 2026-10-15.

**F1. What vast.ai is, and is not.**

| | |
|---|---|
| Instances | **Linux Docker containers** on third-party hosts (gaming PCs to Tier-4 datacentres); a small subset are KVM virtual machines, also Linux (Ubuntu 22.04 templates only, image must come from `docker.io/vastai/kvm`) |
| Windows | **No product.** The only route is a community script that wipes a KVM rental and installs Windows 11 over SSH (one validation on 2026-10-08, "a real CUDA workload has not been tested", no licence). Not evidence-grade |
| macOS | **Not available** on vast.ai or on any hourly provider: AWS EC2 Mac and Scaleway have a 24 h minimum, MacStadium is monthly. GitHub's Apple Silicon runners (Part C) remain the only hourly Mac |
| Custom image | Yes: any registry (Docker Hub, GHCR, private with credentials in the template or `--login`); `vastai/base-image` ships CUDA, Vulkan tools and an `install-display-drivers` helper that writes the NVIDIA Vulkan ICD because some hosts install only the compute driver; a `derivatives/llama-cpp` image exists |
| CLI | `pip install vastai`; `VAST_API_KEY` env var (scoped keys possible) for scripts and CI; `search offers`, `create instance --image … --onstart FILE --ssh`, `start` / `stop` / `destroy`, `copy`, `ssh-url`. `execute` is limited to `ls`/`rm`/`du`; real commands go over SSH |
| On/off | `stop` pauses compute billing but keeps paying for disk **and** a restart waits for free capacity on the *same* host. The reliable pattern is destroy and recreate from the image; the models (3.9 GB from Hugging Face) are re-pulled in minutes |
| Billing | per second, no minimum; disk per GB-hour (higher while stopped), bandwidth per TB. Floor offers 2026-10-15: RTX 3090 $0.12/h, RTX 4090 $0.36/h, RTX 5090 $0.37/h, A100 80 GB $0.40–0.51/h, H100 $1.73/h; verified datacentre on-demand roughly 4090 $0.50, A100 $1.6, H100 $7.5. Interruptible (bid) offers are 50–80 % cheaper and can be killed when outbid |
| Reliability | no SLA; reliability score and `verified` / `datacenter` labels per host; mid-run disconnects are reported by reviewers |
| Privacy | unprivileged containers, but the docs never claim a host cannot read the disk; they advise encrypting data and keeping credentials off the instance. Fine for public GGUFs and a public corpus; never a GitHub or HF write token |
| CPU-only | no product: CPU and RAM come proportionally with the GPU rented; filter `cpu_cores_effective>=16` on a cheap 3090 slot and ignore the GPU. No `cpu_name` search field (the model is in the raw offer) |
| Vulkan | possible in principle on the base image after `install-display-drivers`; **no field report of llama.cpp Vulkan on vast.ai found** — unverified, and no Linux Vulkan cell ships today anyway |

Alternatives checked for the same question: RunPod (Linux only, similar prices), Lambda (Ubuntu only,
no consumer GPUs), TensorDock (KVM VMs, **Windows 10 supported**, 4090 from ~$0.35/h) and GitHub's
larger GPU runners (Linux ~$3.1/h, **Windows ~$6.1/h**, Team/Enterprise plans only, GPU model not
stated). So Windows-with-GPU by the hour exists (TensorDock, Paperspace, AWS G6 Windows AMIs) at a
multiple of vast.ai's Linux price; it is a fallback for the Windows CUDA cells if this box is ever
the bottleneck, not the default.

**F2. What would move to vast.ai** (not adopted). Everything that is Linux and needs a GPU, each
cell on its own instance so the cells run at the same time:

| Work | Today | On vast.ai |
|---|---|---|
| L3 parity, Linux (native vs Python, CUDA) | WSL on this box | one instance per model, ~1 h each in parallel |
| L4 Linux CUDA, both models × both skills | WSL on this box (~2 h) | one instance per cell, ~40 min wall |
| Daemon plan equality, Linux | WSL | same instance as the L4 cell |
| **Python model acceptance, full corpus** (the model-promotion instrument, Part D) | this box, ~4 h per model | one instance per model × skill, in parallel; ~$2 per model |
| **Training** (LoRA, merge, quantize; `python/training/`) | this box | a 4090 or A100 instance; a 742-row LoRA is minutes, the GGUF merge and quant are the longer part |
| Full-corpus plan-equality for a new model or prompt (A1 at scale) | this box | one instance per backend class that exists on Linux: CUDA |

What stays: **Windows CUDA and Windows Vulkan on this box** (no hourly Windows at vast.ai; TensorDock
as a fallback), **Windows CPU, Linux CPU and both macOS cells on GitHub** (Part C). Linux CPU could
run on vast.ai too, but GitHub is free and already needed for macOS, so it stays there.

**F3. Evidence credibility — a rule to write before the first use (like C3).** A rented host is not
this box: the same GPU name spans different CPUs, PCIe generations, driver and CUDA versions, and
the host is shared. Correctness evidence is acceptable **if the run records the offer fingerprint**
(`machine_id`, GPU, CPU model and effective cores, RAM, `driver_version`, `cuda_vers`) in `meta.json`
next to the binary and model sha256, exactly as `docs/PERFORMANCE.md` names the machine behind every
figure. **Speed figures never come from a rented host.** Prefer `verified=true datacenter=true`;
pin `machine_id` across the cells of one release where the market allows; an interrupted or
disconnected run is void and re-run, never composed.

**F4. The resulting mix (as decided 2026-10-15, without vast.ai).**

| Machine | Cells and work | Cost |
|---|---|---|
| This box (RTX 5080) | Windows CUDA, Windows Vulkan, Linux CUDA (WSL): L3, L4, daemon equality. Full-corpus Python acceptance. Training. Speed figures. | owner's time: the Windows and Linux GPU cells on the subset, batch-planned, one after another |
| GitHub runners | Windows CPU, Linux CPU, macOS Metal, macOS CPU (sharded) | free on the public repo |

The mix with vast.ai would have added a third row (Linux CUDA L3/L4/daemon, model acceptance,
training, large plan-equality sweeps; a few dollars per release, ~$2–5 per model promotion, cents
per training run). Not adopted.

**F5. What would have to exist** (not built).

- A **knaif eval image** on GHCR built by CI from the repo: Ubuntu + CUDA runtime, `uv` and the Python
  lane with the CUDA `llama-cpp-python` wheel, ffmpeg and tesseract (the skills' declared system
  requirements), the Linux native artifact of the release under test (downloaded and sha256-checked
  at start, like C2), models pulled from Hugging Face by pinned sha256 on first start.
- `scripts/vast_cell.sh`: search an offer by filters, create the instance with an `--onstart`
  script that runs one cell and uploads its folder (boards, logs, `meta.json` with the fingerprint)
  to a GitHub Actions artifact or back over `vastai copy`, then destroys the instance; a watcher
  in the shape of `scripts/watch_run.sh`. The acceptance records are written on this box from
  the returned boards, as C2 does for CI; nothing is committed from a rented host.
- The training scripts already run from `python/training/`; they need the same image plus the
  training extras and an upload step for the merged GGUF.

**F6. Caution.** None of this is tried. If it is ever picked up: one Linux cell both ways (vast.ai
vs the 1.3.0 WSL result, which exists), compared per row before any rule relies on it, and one
training cycle there with the GGUF's eval compared against the same cycle run here.

## Part G — review the corpus before the subset is derived

**Asked by the owner 2026-10-15:** review every eval row, remove duplicates and near-duplicates,
add what is missing and add more complex rows — and say what that does to comparability.

**G1. What a corpus change breaks, and what it keeps.** Measured 2026-10-15 on today's corpora:

| Skill | Rows | Utterances identical across two rows | Rows sharing tool, outcome and success criteria | Chain rows |
|---|---:|---:|---:|---:|
| ffmpeg | 328 | ~~42~~ **12** | ~~213 in 41 groups~~ **215 rows in 39 groups** | 27 |
| documents | 143 | ~~7~~ **5** | ~~85 in 27 groups~~ **110 rows in 28 groups** | 7 |

*Corrected 2026-10-17 by `evalsuite corpus-audit` (T7).* The 2026-10-15 scan kept only `a-z0-9`
when it compared text, so every Chinese, Russian or Bulgarian utterance collapsed to its file
names (32 ffmpeg rows "shared" the utterance `clipmp4`), and it counted rows beyond the first of
each group rather than rows in groups. The groups also turn out to be a weak duplicate signal:
most are clarify and reject rows, which carry no criteria and test different requests that all
expect a question or a refusal, and single requests written in five languages. What a removal
can rest on is narrower, and is what the audit proposes: a row whose every utterance is already
in another row expecting exactly the same result (tools, outcome, fixture, criteria). That is
**3 ffmpeg rows and 5 documents rows**; the reports are under `evals/corpus-review/2026-10-17/`.

- **Every aggregate breaks.** `diff_snapshots` refuses a population (`total`) mismatch by design,
  so after any row is added or removed each skill's snapshots (4B and 1.7B) must be re-locked
  from a fresh full Python run on the executing verifier. A rate over a different population is a
  different number: the site's accuracy, the premium comparison and 1.3.0's L4 boards stay valid
  as history and are never compared against the new corpus as one figure. Harder rows lower the
  aggregate, and that drop is not a regression. Every published number names its corpus version.
- **Per-row comparability survives for kept rows.** The release bar (D2) and the store (E1) key
  on row id and utterance index. A row that keeps its id and its utterance texts stays comparable
  with every past run; a removed row drops out; a new row has no history until the re-lock.
- **The floors are tied to corpus difficulty.** The S2 slice floors were written against this
  corpus; new complex rows that joined existing slices could push the shipped models under a floor
  and block the next release on rows they never trained on.

**G2. Rules (decided 2026-10-15, owner).**

1. **Kept rows are immutable:** never renumbered, never reworded. The review only **removes** rows
   (duplicates, near-duplicates: same tool, outcome and success criteria with no distinct
   paraphrase value) and **appends** rows with new ids. Removed ids are listed in a
   `data/eval_removed.jsonl` (id, reason, the row kept in its place) so a past run's row can still
   be explained.
2. **New complex rows carry their own tag** (one per skill, e.g. `hard2`), alongside the tags
   they would otherwise get. Their slice reports in every run; its floor is **written after the
   re-lock measures it**, as the S2 floors were first set, so the shipped models are not gated on
   rows they never trained on. Existing floors are unchanged. The next model promotion is where
   the slice is expected to improve.
3. **One re-lock per model per skill**, on the executing verifier, in its own commit, as **part of
   the next fine-tune cycle's full-corpus run**: the cycle needs that run anyway. The subset (D1)
   is derived from the reviewed corpus, never from today's. *Decided 2026-10-15 (owner):* the
   corpus changes once and is re-locked once. Because `diff_snapshots` refuses a population
   change, the **current** models first run once on the reviewed corpus and the prompt the next
   release ships; that run is the reference new models are judged against, row by row, including
   on the new hard rows, and it sets the hard-rows floor (rule 2). New models re-lock with their
   own full runs. **If no new model beats the current ones**, the next release ships on the
   current models and that reference run is their re-lock; a prompt or retriever fix ships only
   if it measures no worse on them.
4. New rows obey the existing train/eval separation test (`test_train_data_integrity.py`) and
   the corpus schema (`docs/EVAL_FRAMEWORK.md`); chains and multi-file requests are the "more
   complex" rows to add first, since those exercise the executor paths a release can break.
5. The safety corpus is reviewed for duplicates the same way but **never shrinks a category**;
   safety stays 100% over the whole safety corpus.
6. The review lands on the plan's `feat/*` branch, ahead of T2b.

**G3. What the review cannot do:** restore a headline comparison across the change. Comparisons
across it are per row on the kept rows (a row-level diff of two scoreboards, which the review
tooling gains as `evalsuite row-diff`), and the release notes and site say "corpus v2
(YYYY-MM-DD)" next to any number measured on it.

## Part H — what a published model or skill brings with it

Models are trained, and skills developed, before they appear in this repository; a release only
ever contains an established result. So that the gates here can judge it, a model or a skill
arrives with its evidence, not only its files (decided 2026-10-15, owner):

- **A model:** the GGUF on Hugging Face (public naming per `docs/MODELS.md`), bytes
  hygiene-scanned; the manifest and `models.yaml` entries and each skill's `recommended_model`;
  the model card; its **full-corpus acceptance run** on the executing verifier, committed under
  `evals/runs/` through `evalsuite redact` (T2b requires a snapshot to name a committed
  `source_run` with per-row grades); the snapshot re-lock in its own commit on the release branch;
  the eval corpus it was accepted on (reviewed per Part G). Training rows and training-script
  changes ship per model, as decided when it is published; `docs/PROVENANCE.md` states per model
  what was published. What is already public stays public. The model then ships through the
  release's own gates (L3, L4 on the subset).
- **A skill:** the bundle plus its stage 1–3 evidence (acceptance run, safety run, snapshot); its
  first native release runs one full-corpus cell per platform (D1).
- The model-promotion instrument (Part D, full corpus) therefore runs **before** publication and
  arrives with it; the store (E1) is filled here from the published run. `just eval-accept` and the
  snapshot gate read this repository only.
- **Enablers for skills made outside this tree** (forks, community skills): Python skills discovered
  from an external path like the native `KNAIF_SKILLS_ROOT`; native skill registration as a
  library crate rather than per-domain branches in `apps/cli` (audit F11); a stable interface a
  client can depend on. Each is a public feature in its own right, planned separately.

## Tasks

- [x] **T0. Measure before building.** On the frozen 1.3.0 binary: (a) time `plan --batch` for one
      ffmpeg cell vs today's 44 min; (b) time executing those plans one after another (no model);
      (c) compare `plan --batch` plans with the cell's `run` plans, every row, on CUDA, Vulkan, CPU;
      (d) time one 100-row CPU shard on a GitHub Windows and Linux runner; (e) on a GitHub macOS
      arm64 runner, load the 4B with the Metal backend and plan 20 subset rows — does Metal load
      in the VM, and how long per row. (a)–(c) run on this box only when the owner schedules them
      (nothing runs here unasked); (d)–(e) are CI-only. This says whether B1, C1 and the macOS
      move hold and what they save; nothing is built before it. *(The vast.ai parts (f)/(g) were
      dropped 2026-10-15 with Part F.)* **(d) and (e) done 2026-10-16/17** (C5): CI CPU cells
      grade identically to the box on 100/100 rows but run 3.1× slower; macOS on GitHub's runner
      is unusable. The process is optimized on this machine first; other machines later. (a)–(c)
      still wait for the owner.
      **(c) on CUDA, answered from 1.3.0's own evidence (2026-10-17, no new run):** the
      daemon check's in-process pass is `plan --batch` over every utterance on the frozen
      binary, CUDA (`evals/parity/2026-10-13_r130-win-daemon-plan-equality/`); against the plans
      1.3.0's CUDA cells dumped from `run`, both models, both skills (2,050 utterances), **every
      row the model planned is identical**. Of the 17 rows that are not, 16 are rows `run` never
      asked the model about: the unsafe-phrase screen rejects them before inference, and `plan
      --batch` does not screen. The 17th is a row the model failed to plan, an error on both
      paths.
      So B1's execution half must keep `run`'s screen and its clarify gate, which it does by
      replaying through `run` itself (T3). On the same evidence, `plan --batch` took ~0.45 s a
      row against ~2.5 s for `run` (the 1.3.0 `TOTALS`): ~6.5 min against ~36 min for the 4B
      ffmpeg cell's planning.
      **(a)–(c) done 2026-10-17 on this box** (`evals/runs/2026-10-17_t0-box_success/`,
      `report.md`; the owner gave the box for the afternoon): the frozen 1.3.0 binary,
      batch-planned and executed by replay, **both models, CUDA, Vulkan (whole cells) and CPU
      (100 rows a skill): the same grade, plan and command as 1.3.0's cell on every one of the
      7,450 rows compared**, and every live cold-start row planned as in the batch. Cell times on
      a GPU about halve (4B CUDA ffmpeg 43.7 → 22.2 min; Vulkan 50.4 → 27.2; 1.7B CUDA 35.4 →
      17.3). On the CPU only 13–34% (4B ffmpeg 17.2 → 13.2 s a row): planning is the cost there,
      not the model load (≈95% of a CPU row even in one batch). (b) Execution alone: 13.7 min
      for 861 ffmpeg rows, 23 s for 164 documents rows.
- [ ] **T0b. C1/C2 workflow:** a `workflow_dispatch` cell workflow (shard matrix for CPU and
      macOS, download and verify the frozen files, cached models, compose + safety +
      `accept-native` job, artifacts). **First half built 2026-10-16**, and T0(d)/(e) run from
      this branch with a `[t0]` commit (C2's 2026-10-17 amendment; **remove the push trigger
      before merging**): `evalsuite shards` (pre-drawn `--only` files, evenly spaced in corpus
      order and dealt round-robin, with a manifest naming the corpus sha256) and
      `.github/workflows/evidence-cell.yml` (a published release's file checked against its
      `SHA256SUMS`, the model against the manifest's sha256 and cached by it, the skill's system
      requirements, fixtures, a lane written at run time, one runner per shard, boards and
      `runner.json` timings as artifacts), guarded by `test_evidence_workflow.py`. Still to add:
      taking the Package run's files as well as a published release's, and the join job
      (compose the shards, safety on the binary, `accept-native`).
- [ ] **T1. Write the rules** as gate-decision rules in the shape of the 1.3.0 ones, for the owner
      to approve: the subset population and per-row regression bar (D2), the store's reuse table
      (E3) and never-reused list (E4), the cold-start and confirmation samples (B1, B2), the daemon
      sample (B3), and how the gate and the release notes print measured vs reused counts (E2).
- [ ] **T2. A3: split the `native` fingerprint** into planning and per-tool execution, with tests
      that a change to one tool's code stales only that tool's records. The halves are the store's
      key components (E1).
- [ ] **T2b. D1: `evalsuite release-subset`** and the committed `release_subset.json` per skill,
      with the corpus-sha drift test; `accept-native --population subset` (D2) reading per-row
      Python verdicts from the locked snapshot run. **No skill needs re-locking for this**
      (checked 2026-10-14): all four snapshots are policy 3 / `success`, and each one's source
      scoreboard is committed with per-row grades and plans (4B: `2026-09-26_r3a-reference_success/sft-v4/`,
      1.7B: `2026-09-27_r5a-1.7b-sft-v9_success/1.7b/`). But a snapshot strips `rows` and names no
      source run — the match above was made by aggregates. `save_snapshot` must record
      `source_run` (folder + scoreboard sha256) and the gate must refuse a snapshot without one;
      the two existing snapshots get the field by a metadata-only edit, not a re-run.
      *Corrected 2026-10-17:* those four source scoreboards are **not committed**: they exist
      only on the maintainer's machine (`evals/**` ignores per-arm boards). Checked the same day:
      each one's aggregates equal its snapshot's exactly, each carries every row (861 / 164),
      and `check_no_local_paths` finds nothing in them. `source_run` can only name a committed
      run, so they are committed first (owner's decision: it publishes per-row results), then
      the field is added.
- [ ] **T2c. E1/E2: the store** — key derivation, lookup, write, and `evalsuite cell` composing a
      board from the store plus a list of missing keys; tests with synthetic fingerprints that
      each reuse row of E3 reuses exactly what it says and nothing more.
- [ ] **T3. B1 in the lane:** one-load batch planning + sequential execution + the cold-start sample, behind a
      lane option, with tests; validated by re-running one 1.3.0 cell both ways and requiring the
      same verdict and the same per-row grades. The lane fills the store (E1) rather than a board.
      **Lane half built 2026-10-17:** `evalsuite native --batch-plan` (one `knaif plan --batch`
      in a folder holding every fixture, then each row through `knaif run` with no model, the
      mock backend replaying its plan: `KNAIF_LLM_BACKEND=mock`, `KNAIF_LLM_MOCK_RESPONSE`, a
      seam the binary already had, so no product code changed); `--live-sample FILE` (the
      cold-start rows run live, their plans compared with the batch's, recorded as
      `batch_live`); `--batch-plans FILE` (execution alone, from a recorded batch); the board
      records `planning: batch` and `timing`, and `accept-native` **refuses** a batch-planned
      board until T1's rules adopt the method. `evalsuite row-diff` (G3) compares two boards
      row by row (grade, plan, command). Smoke-tested on the frozen 1.3.0 binary: 3 rows, same
      grade, plan and command as 1.3.0's CUDA cell. Still to do: the T0 stages, then the store.
- [ ] **T4. Run scripts on the store:** a release's run script becomes "compute missing keys per
      cell, batch-plan them, execute them, run the never-reused samples, compose, grade"; A1/A2
      are what the key lookup does, not separate steps.
- [ ] **T5. Codex audit** of T1–T4 before the next release uses them.
- [ ] **T6. RELEASE.md and EVAL_FRAMEWORK.md** describe the new method (two instruments, the
      store, the CI cells); the next release index's gate rules adopt it; AGENTS.md's lifecycle
      table says L4 measures the subset.
- [ ] **T7. Corpus review (Part G), before T2b.** Per skill: a duplicate report (the G1 scan as a
      script, `evalsuite corpus-audit`), the removal list with reasons (`eval_removed.jsonl`),
      the missing and complex rows under their own tag, the safety corpus reviewed; the owner
      approves the removal list and the new rows before they land. Kept rows byte-identical
      (a test diffs kept ids against the previous corpus).
      **Scan built 2026-10-17:** `evalsuite corpus-audit --skill <s> --out DIR` (report.md +
      audit.json; edits nothing). It proposes only rows wholly contained in another row with the
      same expected result (never a safety row; the larger row is kept), and lists for a person
      the same words with different expected results (5 ffmpeg pairs, each differing only in
      criteria, one also in fixture), near-duplicate paraphrases, and coverage per tool. First
      reports: `evals/corpus-review/2026-10-17/{ffmpeg,documents}/report.md` — **3 + 5 rows
      proposed**. Safety corpora: no repeats. **Approved by the owner 2026-10-17, all eight**
      (ffmpeg 007→080, 050→179, 063→080; documents 067→006, 071→007, 075→008, 082→142,
      115→137): checked against every 1.3.0 board and the 4B reference run, each pair always
      graded alike, and no rate-floor slice falls under `min_rate_rows` (documents
      `multilingual` lands exactly on it, 17 → 16). Built the same day: `evalsuite
      corpus-remove` (applies approved ids from the audit, refuses anything that would lose a
      test, writes `eval_removed.jsonl` with the removed row and `eval_kept.sha256` pinning
      every kept line) and `test_corpus_integrity` (G2.1 over the real corpora). **Not applied
      yet**: the removal lands as the first stage of the re-lock (T7b), on the owner's green
      light. **Owner, later the same day: finish all changes on the corpus before re-locking.**
      The whole change is prepared as `evals/corpus-review/2026-10-17/proposal.md`, waiting
      for approval: the five same-words pairs decided (remove 010 → 168 and 023 → 097; keep
      078/187, 084/185, 097/189, which add a phrasing), so **10 removals** in
      `<skill>/decisions.json`; and **18 new `hard2` rows** (`new_rows.<skill>.jsonl`: 10
      ffmpeg, 8 documents — chains and fan-out requests over the tools that had no chain row),
      each passing the new `evalsuite corpus-check` (schema, tag, no id or utterance the corpus
      or `train.jsonl` has). ffmpeg baselines are `validated_by: null` for the owner's
      notebook. The re-lock script gained an `append` stage (R1 covers it; R5 now says the
      `hard2` floor is written after the run).
- [ ] **T7b. Re-lock on the reviewed corpus**, one commit per model per skill, from the next
      fine-tune cycle's full run; the hard-rows slice floor written then. The subset (T2b) is
      derived after this. **Prepared 2026-10-17, waiting for the owner's green light:**
      `evals/runs/2026-10-18_relock-corpus-v2_success/run_all.sh` (stages `check`, `remove`,
      `run`, `compare`, `lock`; rules R1–R5 pre-registered in the script; ~40 min of CUDA for
      both models, both skills, safety). The lock goes through the new `evalsuite lock`, which
      writes a snapshot from the saved board under `evals/runs/` and records `source_run`
      (folder, file, sha256) — T2b's missing field — so the four re-locked snapshots name their
      committed source runs from the start. *Decided by the owner 2026-10-17:* one re-lock,
      after every corpus change — the script's stages are now `remove` → `append` → `run` →
      `compare` → `lock`, with the new rows in the same run (R5: the `hard2` floor is written
      from this run's measurement, by the owner).
- [ ] **T8. The published-evidence path (Part H):** `evalsuite redact` producing a committable run
      folder and `evalsuite row-diff` (G3); `docs/FINE_TUNING.md`, `docs/MODELS.md`,
      `docs/PROVENANCE.md` (a per-model "what was published" table), `docs/RELEASE.md` and
      AGENTS.md say what a published model and skill bring with them.
- [ ] **T8b. The enablers (Part H)**, each as its own public plan when the owner opens it:
      external skills path for Python, native skill registration as a crate, a stable client
      interface.

## Open questions for the owner

1. ~~How much reuse is acceptable for a minor vs a patch release?~~ **Answered 2026-10-14** by
   Part E: reuse is not a per-release choice; the store's keys decide, and a release reuses
   exactly what its fingerprints left valid. A minor release that changes planning gets no plan
   reuse by construction.
2. ~~The cold-start sample size per cell (30? 50?) and whether it is pre-drawn once per release.~~
   **Answered 2026-10-15:** 30 rows, drawn once per release (B1).
3. ~~Whether CI-measured cells are acceptable evidence.~~ **Answered 2026-10-14:** yes for
   Windows CPU, Linux CPU, macOS Metal and macOS CPU, pending T0(d)/(e); C3's condition (written
   into the gate rules before the run) stands.
4. ~~Where the store lives: committed under `evals/store/` (proposed in E1) or kept as CI artifacts
   with only the composed boards committed.~~ **Answered 2026-10-15:** committed (E1).
5. ~~Whether a new skill's first release may ship on the subset alone, or needs one full-corpus
   native cell (per platform) first.~~ **Answered 2026-10-15:** one full-corpus native cell per
   platform first (D1).
6. ~~Part F: whether a Linux CUDA cell measured on a rented vast.ai host is acceptable evidence,
   and whether model promotion and training move there.~~ **Answered 2026-10-15:** no; Part F is
   research only, everything Linux-GPU stays on this box.
7. ~~Whether an eval image on GHCR is acceptable.~~ **Moot 2026-10-15:** not needed without Part F.
8. ~~Corpus review: how to keep comparability; how new hard rows meet the floors.~~ **Answered
   2026-10-15:** kept rows immutable, remove and append only; own tag, floor set from the re-lock;
   one re-lock per model on the next cycle's full run (G2).
9. ~~What a published model must bring, and how much training detail.~~ **Answered 2026-10-15:**
   the floor is the model plus its evidence (Part H); training rows and scripts per model, stated
   in `docs/PROVENANCE.md`.
