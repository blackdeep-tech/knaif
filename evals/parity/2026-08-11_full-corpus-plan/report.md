# Full-corpus plan parity — post-fix native vs Python (2026-08-11)

S2 at full-corpus scale. Every ffmpeg eval row, both runtimes on the identical GGUF, plus a
backend control on the rows that diverged.

Unblocked by fixing the Windows Vulkan build (`CMAKE_OBJECT_PATH_MAX`, not the `rc.exe RC2136` it
had been filed as — see `docs/NATIVE.md` §10). With Vulkan offload and `plan --batch`, the run took
**28 minutes**, against the ~14 hours the plan had recorded as the blocker.

## Headline

| | |
|---|---|
| exact plan equivalence | **265/314 = 84.4%** |
| tool-sequence agreement | **292/314 = 93.0%** |
| `chain (native 1-step)` | **0** |
| not-comparable | 0 |

## Read the two numbers correctly — this is not a regression

The pre-fix baseline reported **92.4%** and S2 reported **98.3%**. Neither is comparable to 84.4%.

- **92.4%** was a *tool-sequence* metric over 847 utterances. On that same metric this run scores
  **93.0%** — flat to slightly better.
- **98.3%** was per-row agreement on a **chain-heavy 60-row subset**, chosen because chains were
  what the plan was about. Chains are the rows where the model is most confident.
- **84.4%** is *exact plan equivalence* — every argument, modulo materialized defaults — over the
  whole corpus including its ambiguous tail. **That question had never been asked before.**

Quoting 84.4% against 98.3% would report a regression that did not happen.

## What the 49 divergences are

**4 are a harness artifact.** `concat_video` declares `defaults: {output: combined.mp4}`. Native
bakes defaults into the `plan --json` envelope; Python's `infer()` returns the raw model plan
*before* `apply_defaults`:

```text
native : concat_video({inputs: [clip.mp4, clip.mov], output: "combined.mp4"})
python : concat_video({inputs: [clip.mp4, clip.mov]})
```

Both render the identical command — the expansion contracts pin that. `parity_check` flags it
because `output` sits in `_SIGNIFICANT_ARG_KEYS`, which deliberately refuses to excuse output-name
differences as benign defaults. Right rule in general, wrong answer here.

**The rest do not look like a port defect**, because a port defect clusters and this does not:

| tag | rate | | tag | rate |
|---|---|---|---|---|
| concat_video | 58% (mostly the artifact) | | multilingual | 3% |
| clarify | 19% | | strip_audio | 0% |
| resize | 18% | | rotate_video | 0% |
| convert / compress | 15% | | bg / zh | 0% |

The weight sits on `clarify` — the underspecified rows, where the top-two logits sit closest and the
smallest numerical difference flips the argmax.

## The backend control

The two lanes are **not on the same backend**: native runs Vulkan, Python runs CUDA. So the
divergence could have been numerical rather than a code difference. Control: same binary, same
utterances, **only** the backend changed.

| | |
|---|---|
| native Vulkan == native CPU | **32/37** |
| changed under backend alone | 5/37 |

Native is **self-consistent** across two of its own backends, so its side of the divergence is
deterministic. Worth noting why S2's control missed the 5: it measured Vulkan-vs-CPU at 60/60, but
on the chain-heavy subset — exactly the population that would not show the effect.

**This control is narrower than it first looks, and the first reading of it here was too strong.**
It compares native against native. Python runs on **CUDA**, a third backend neither lane of this
control exercises. So it establishes that native does not wobble; it does **not** establish that
the native-vs-Python difference is a code difference rather than backend arithmetic.

## Sampling is ruled out

- **Native**: pure argmax over raw logits — `llama.rs:335`,
  `.max_by(|a, b| a.logit().total_cmp(&b.logit()))`.
- **Python**: `temperature=0.0`, and llama-cpp-python's `_init_sampler` takes
  `elif temp == 0.0: sampler.add_greedy()`. `add_penalties` runs first but is neutral
  (`repeat=1.0`, `freq=0.0`, `present=0.0`).

Both are greedy on unmodified logits. Identical prompts + greedy decode should give identical
tokens, so the remaining suspect is what gets *tokenized*.

## Tokenization is ruled out too — the runtimes feed the model the same integers

`scripts/token_parity.py` renders one utterance both ways and diffs the prompt *and* the token IDs.
The trick is that **both paths are reachable from Python**, so lane B calls the very C function
native calls, removing the build, the backend and the CLI from the comparison:

| | |
|---|---|
| prompt strings after templating | **identical** (7511 chars single-intent, 8043 chain) |
| token IDs | **identical** (1993 / 2184) |
| BOS | no discrepancy — Qwen sets no `add_bos_token`, matching `AddBos::Never` |

So llama.cpp's C++ `apply_chat_template` and llama-cpp-python's Jinja2 rendering agree exactly.
This closes the gap `docs/NATIVE.md` §4.1 had flagged as necessary-but-not-sufficient, as a
**ruled-out** cause.

## What the backend actually accounted for

With tokens and sampling both eliminated, the only remaining variable was the arithmetic. Re-running
the **Python lane on CPU** and comparing against native's CPU envelopes — same backend family, on
the 37 rows that had disagreed with Python-on-CUDA:

| | exact | tool-sequence |
|---|---|---|
| native CPU vs python **CUDA** (the original run) | 0/37 | 0/37 |
| native CPU vs python **CPU** | **23/37** | **33/37** |

**23 of 37 divergences vanish by holding the backend fixed.** Final attribution of the 37:

| | count |
|---|---|
| agree once the backend matches | **23** |
| differ only by a materialized default (the `concat_video` artifact) | 5 |
| genuinely different plans | **9** |

Two of the 9 are the model emitting an invalid plan twice — `adjust_volume` with an unsupported
`target_level`, and a hallucinated `adjust_video` tool. Native's `plan --batch` surfaces those as
`{"plan": [], "error": …}`. **This is not a missing retry**: native ported the validator-feedback
repair (`main.rs:1241`, one retry, same as Python's `repair_invalid_plans`). The model simply failed
both attempts on that side.

**The residual 9 are not proven to be port defects, and should not be quoted as such.** Native
vendors its own llama.cpp through `llama-cpp-sys-2`; Python uses llama-cpp-python 0.3.23's
separately built copy. Two independent llama.cpp builds with different CPU kernel dispatch, so
bit-identical logits were never guaranteed even with the backend family held fixed. A CPU-vs-CPU
comparison narrows the variable; it does not eliminate it.

**Extrapolated true agreement: ~96%** (9 genuine differences per 37 sampled divergences, over 49
total ⇒ roughly 12 of 314 rows). No systematic port defect was found beyond the four already fixed.

## Leading hypothesis — the chat template, not the planner

Prompt parity proves the `(system, user)` **strings** are byte-identical for all 847 utterances. It
does **not** prove the templated, tokenized sequence is. Python calls
`create_chat_completion(messages=[system, user])`, so **llama-cpp-python applies the GGUF's chat
template itself**; native applies its own.

The plan already lists this as pinned only at the prompt-string level. This run is the first
evidence that the gap has consequences.

**Next test, cheap and decisive:** dump the post-template token IDs from both runtimes for the same
utterance and diff them. No scoring, no model quality judgement — just whether the two runtimes feed
the model the same integers.

## Files

| file | |
|---|---|
| `native.jsonl` / `python.jsonl` | one plan envelope per row per lane — the re-gradable evidence |
| `control_vulkan.jsonl` / `control_cpu.jsonl` | the backend control, keyed by row id |
| `utterances.txt` | the exact inputs, in order |
