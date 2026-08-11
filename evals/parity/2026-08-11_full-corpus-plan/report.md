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

So the divergence is **mostly deterministic** — which makes it findable rather than noise. Worth
noting why S2's control missed this: it measured Vulkan-vs-CPU at 60/60, but on the chain-heavy
subset, i.e. exactly the population that would not show the effect.

## Sampling is ruled out

- **Native**: pure argmax over raw logits — `llama.rs:335`,
  `.max_by(|a, b| a.logit().total_cmp(&b.logit()))`.
- **Python**: `temperature=0.0`, and llama-cpp-python's `_init_sampler` takes
  `elif temp == 0.0: sampler.add_greedy()`. `add_penalties` runs first but is neutral
  (`repeat=1.0`, `freq=0.0`, `present=0.0`).

Both are greedy on unmodified logits. Identical prompts + greedy decode should give identical
tokens, so the remaining suspect is what gets *tokenized*.

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
