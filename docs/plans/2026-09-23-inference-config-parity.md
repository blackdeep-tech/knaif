# Inference config parity — measure the noise floor, then make both lanes compute alike

**Status:** Active — T1–T7 done; T8 (training backlog) open · **Created:** 2026-09-23 · **Last worked:** 2026-09-24 · **Completed:** —
**Owner:** core · **Ref:** found in the workbench; bears on T8 (L3/L4) of
[2026-09-11-reject-clarify-taxonomy.md](2026-09-11-reject-clarify-taxonomy.md) and on the open
TODO *"The eval lane is not bitwise reproducible, and nothing says so"*

**Goal:** Know how much of any eval number is noise from *how llama.cpp is configured* rather than
from the model, prompt or code — and then remove the part that comes from the two lanes being
configured differently, so a Python eval and a native eval measure the same computation.

**Not a goal:** making GPU inference bit-exact across machines or llama.cpp versions. The two
lanes bundle different llama.cpp builds and will keep doing so; the aim is to stop *choosing*
different settings on top of that.

## What was found

Same GGUF (`knaif-qwen3-4b-v2-q4_k_m`), same utterance (*"convert clip.mp4 to mkv then cut its
audio, then take 2-4 seconds, then reverse, then crop it to 320x200"*), greedy decoding on both:

| Checked | Result |
|---|---|
| randomness | none — Python `extract_audio` 4/4 fresh runs (GPU and CPU); native `strip_audio` 4/4 (GPU and CPU) |
| prompt + chat template | identical — 2486 token IDs compared one by one |
| sampling | both greedy; llama-cpp-python's `repeat_penalty` default is 1.0 (a no-op) |
| native repair retry | did not fire |

What differs is the **context configuration**, which no contract pins:

| | flash attention | `n_batch` | `n_ubatch` | KV cache across calls |
|---|---|---|---|---|
| Python (llama-cpp-python 0.3.23 defaults) | **off** | 512 | 512 | **reused** — prefix match with the previous call |
| native (llama-cpp-2 0.1.150) | llama.cpp default (**auto**) | = `n_ctx` (8192) | 512 (default) | fresh context per call |

Probability of the second step's tool token, same prompt, Python lane, varying only the config:

| flash attention | `n_batch`/`n_ubatch` | `extract` | `strip` |
|---|---|---|---|
| off | 512 (**Python today**) | **0.68** | 0.32 |
| off | 8192 | 0.26 | **0.74** |
| on | 512 | 0.20 | **0.80** |
| on | 8192 | 0.41 | **0.59** |

A change that should be numerically irrelevant moved a decision by ~50 points. Two consequences:

1. **The lanes disagree by configuration, not only by port bugs.** L3 counts these as
   disagreements; some fraction of them are this.
2. **Within the Python lane, a row's result depends on the row before it.** The eval runner keeps
   one model loaded, so each prompt is decoded after a prefix match against the previous row's —
   a different batch layout per corpus order. That is the likeliest cause of the TODO's
   `documents_042#0` flip across identical runs (recorded there as "GPU reduction order").

**What is NOT affected:** the fine-tuned weights (training never ran through either inference
config), and every conclusion whose margin exceeds the noise floor. The floor is unmeasured, which
is the whole problem.

## Decision rule — pre-registered

Measured as the **per-utterance outcome flip rate** (T2): the share of eval utterances whose
graded outcome changes between two configs of the same model. Compared against the **smallest
decision margin already taken** — v2 over v1 on the promotion verdict, and each skill's distance
from its acceptance floors (`evals/runs/2026-09-15_t6g-sizing-vs-sending_success/PROMOTION_VERDICT.md`,
`skills/*/acceptance.yaml`).

- **Flip rate < smallest margin:** the existing evaluations and the v2 decision stand. Align anyway
  (T3–T5) so future numbers mean one thing; re-lock only if T6 moves a snapshot.
- **Flip rate ≥ smallest margin:** the affected decision is reopened, not re-argued. Align first,
  re-measure on the aligned config, re-lock both snapshots in their own commit, and re-derive the
  v2 promotion verdict from those numbers before any publish.

Set now, before the measurement, so the result cannot choose its own threshold.

## Tasks

### - [x] T1 — Make the three knobs settable (no behaviour change)

`InferenceOrchestrator` takes `flash_attn`, `n_ubatch` and `reset_cache_per_call` from
`model_config`, defaulting to today's behaviour. `reset_cache_per_call` calls `llm.reset()` before
each completion so no prefix is reused. Tests first: each key reaches `Llama(...)` / the call path;
defaults unchanged.

**Done 2026-09-23.** `flash_attn`/`n_ubatch` are passed to `Llama(...)` only when set; the reset
runs before both `infer` and `infer_stream`. Also fixed: `n_batch` was unbound on a load by
`model_path=`. Verified on the real v2 GGUF, same utterance, two calls each:

| config | call 1 decoded | call 2 decoded / reused | step 2 |
|---|---|---|---|
| today | 2486 | 1 / 2485 | `extract_audio` |
| `reset_cache_per_call` | 2486 | 2486 / — | `extract_audio` |
| + `flash_attn`, `n_batch = 8192` | 2486 | 2486 / — | **`strip_audio`** (as native) |

### - [x] T2 — Measure the flip rate

Plan-level runs of the **whole** ffmpeg and documents eval corpora with v2, cheap verifier (plan
outcome only — this measures decisions, not execution), saved under `evals/runs/` with `INDEX.md`
rows:

| Run | Config |
|---|---|
| A | Python as today (warm, fa off, 512) — the existing baseline's config |
| A′ | A again — the within-config run-to-run floor |
| B | Python, `reset_cache_per_call` — isolates corpus-order dependence |
| C | Python, fa on + `n_batch = n_ctx` — native's config |
| D | native `plan --batch` — the shipped lane, as reference |

Report per pair (A/A′, A/B, A/C, C/D): utterances whose normalized plan differs, whose graded
outcome differs, and the list of flipped rows. **C/D is the prediction test**: if aligning the
config is enough, C and D should agree far more often than A and D do. Record everything in this
file under *Results*.

### - [x] T3 — Pin the config in the contract

Add `flash_attn`, `n_batch`, `n_ubatch` and `reset_cache_per_call` (or its native equivalent: a
fresh context per call, which native already does) to `contracts/runtime/generation.yaml`, set to
native's values. Extend `test_settings_parity.py` / `test_generation_settings.py` so a drift in
either runtime fails `just check`.

### - [x] T4 — Set both runtimes explicitly

- Python: `orchestrator.py` passes the contract values; the eval backends and `models.yaml`
  stanzas stop relying on llama-cpp-python defaults. The workbench's agent builder
  (`notebooks/shared/workbench/selectors.py`) uses the same values.
- Native: `knaif-llm/src/llama.rs` sets `with_flash_attention_policy` and `with_n_ubatch` instead
  of inheriting llama.cpp's defaults, which can change under a crate bump without notice.

### - [x] T5 — The workbench says which config it ran

The panel shows model file, runtime, build and the four settings above. Today a pasted panel cannot
say which model the Python lane loaded, which slowed this investigation.

**T3–T5, T7 done 2026-09-24.** Contract gained `flash_attn: auto`, `n_batch: 8192`,
`n_ubatch: 512`, `reset_cache_per_call: true`, asserted on both sides; native sets
`with_flash_attention_policy(FLASH_ATTN_AUTO)` and `with_n_ubatch(N_UBATCH)` from `knaif-llm`
constants; the orchestrator defaults to the compute values (reuse stays an embedder default);
`models.yaml` v1/v2 and the three pinned eval arms carry all four; the workbench builds its Python
config from the contract and the panel prints model + config. A `-legacycfg` arm reproduces the
pre-parity config. Verified on hardware: native, the shipped v2 stanza and the workbench all plan
`strip_audio` for the utterance that started this, native's trace showing `flash_attn = auto` →
enabled, `n_batch = 8192`, `n_ubatch = 512`.

### - [x] T6 — Re-measure and apply the decision rule

Python `eval-success` on both skills with the aligned config, against the snapshots. Apply the rule
above. Any re-lock is its own commit, with the reason and the flip-rate evidence in the message.

### - [x] T7 — Docs

`docs/INFERENCE.md` (the pinned settings and why), `docs/EVAL_FRAMEWORK.md` (the measured noise
floor, and that a difference smaller than it is not a finding), and close or rewrite the TODO item
on bitwise reproducibility with the real cause.

### - [ ] T8 — Feed the fragile rows to training

T2's flipped rows are exactly the utterances where the model sits on a decision boundary
(*"cut its audio"* → `extract_audio` vs `strip_audio` is the first known one). List them in
`docs/FINE_TUNING.md`'s backlog as candidate train rows for the next cycle — the fix for a fragile
decision is a sharper model, not a luckier config.

## Sequencing

**Before** the v2 L3/L4 runs (T8 of the taxonomy plan): those compare the two lanes, and this gap
is exactly what distorts that comparison. **Before** the v2 publish only if T2 trips the decision
rule; otherwise the publish can proceed on the existing evidence while T3–T7 land.

## Results

### T6 — 2026-09-24

Full corpora (ffmpeg 851, documents 164), `success`, fixtures regenerated, code `5ea3044`, same
v2 weights in two configs (`evals/runs/2026-09-24_config-parity-t6_success/report.md`):

| | legacy config | aligned config (shipped) |
|---|---|---|
| ffmpeg outcome / knaif | 0.93655 / 0.98545 | **0.93772** / 0.98373 |
| documents outcome / knaif | 0.98171 / 1.0 | 0.97561 / 0.99782 |
| S2 | ffmpeg **NOT ACCEPTED** (`batch` 26/29 < 0.92); documents ACCEPTED | **ACCEPTED 39/39 + 36/36** |
| safety | 11/11, 9/9 | 11/11, 9/9 |

legacy vs aligned: ffmpeg 9 outcome flips (1.06%, net +0.12 pp), documents 1 (0.61%, net −0.61 pp)
— the full-corpus noise floor, matching T2's estimate. The prediction's third clause failed: the
legacy arm missed `batch` by one utterance (`ffmpeg_077#1` flips with config; `ffmpeg_229#3/#4`
now emit `output: "*.hevc"`, most likely from prompt change `be3f20f`, which had never been
eval-measured). So the rule said stop and re-derive the v2 verdict.

**Verdict re-derived** (`evals/runs/2026-09-24_v2-verdict-aligned_success/report.md`), criteria
fixed before the run: v1 on the aligned config, same code and fixtures — ffmpeg 0.91892 (v2
**+1.88 pp** > the +1.06 floor), documents 0.96951 (v2 **+0.61 pp**), v1 still missing ffmpeg's
in-corpus `safety` slice (0.529 < 0.75). **v2 STANDS.** Both snapshots re-locked from T6's aligned
run: ffmpeg 0.9388954172 / 0.9801227169 → **0.9377203290 / 0.9837335620**, documents
0.9817073171 / 1.0 → **0.9756097561 / 0.9978213508**. documents' knaif below 1.0 is now the
settled value: `documents_042#0` is config-dependent, not a flake, and under the shipped config
this is what it gives.

### T2 — 2026-09-24

Full write-up: `evals/runs/2026-09-23_config-parity-flip_cheap/report.md`. Population: 469 first
phrasings (326 ffmpeg + 143 documents) — the `cheap` verifier runs only each row's first
utterance, which the run design missed; T6 covers the rest.

| Pair | ffmpeg decision / outcome flips | documents |
|---|---|---|
| A vs A2 (same config twice) | 0 / 0 | 0 / 0 |
| A vs B (cold cache) | 7 / 1 | 2 / 1 |
| A vs C (native's config) | 11 / **4 (1.23%)**, net 0.00 pp | 2 / 0 |
| A vs D (native) | 14 | 2 |
| C vs D | **3** | **0** |

Safety 100% in A, B and C (ffmpeg 11/11, documents 9/9). All three predictions held.

**Rule applied: the existing evaluations and the v2 verdict stand** — largest outcome flip rate
1.23% against the smallest decision margin 1.83 pp (documents, v2 over v1), and a net shift of
0.00 pp from moving to native's config. Next: T3–T5. The margin is ~1.5× the noise, not 10×, so
T6's full-corpus `success` re-measure on the aligned config is still required, and slices within
1–2 utterances of a floor are settled there.
