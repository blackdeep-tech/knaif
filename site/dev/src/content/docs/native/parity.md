---
title: Proving parity
description: Two layers of cross-runtime checking — golden fixtures for the deterministic pipeline, and a live diff on real utterances.
sidebar:
  order: 2
---

"Same behaviour on both sides" is a claim, and claims drift. Parity is checked in two
layers, because they catch different failures.

## Layer 1 — golden fixtures

`contracts/parity/planner_cases.json` holds cases for the deterministic pipeline: parse →
normalize → apply defaults → validate.

Both validators consume it — `python/core/tests/test_planner_parity.py` and the
`knaif-core` parity test — and **both must produce the same valid/invalid outcome and the
same error substring**:

```json
{ "name": "enum_reject",
  "plan": {"plan": [{"tool": "compress", "args": {"input": "x", "quality": "mid"}}]},
  "valid": false,
  "error_contains": "must be one of" }
```

The registries in that file are **inline `tools.yaml` text**, deliberately. Parity is then
measured on an identical registry, independent of any skill's real content — so a case
cannot start passing because someone edited ffmpeg.

These run in the ordinary test suites, need no model, and take milliseconds. They are the
regression net for the contract itself.

### One contract per deterministic stage

The validator is not the only stage that runs the same way on both sides. Everything
between the utterance and the executed command is deterministic, and each stage has its
own fixtures:

| contract | what it pins |
|---|---|
| `prompt_cases.json` | the rendered prompt, given the same utterance and registry |
| `retrieval_cases.json` | which tools are selected, **and in what order** |
| `generation_settings.yaml` | `max_tokens`, `n_ctx`, decoding, thinking |
| `chain_linking_cases.json` | how a plan's intermediate files are bound together |
| `planner_cases.json` | parse → normalize → defaults → validate |
| `expansion_cases.json` | the rendered command, per plan step |

Read that list in order and it is the pipeline itself. The gaps are what bite: a check
that stops at the plan envelope measures what the eval harness sees, not what a user sees.
Two real native defects — one that ran only the first step of a plan, one that fed a step
the wrong file — left the envelopes byte-identical and were invisible until the stages
either side of the planner were pinned too.

## Layer 2 — live diff on real utterances

```bash
just parity <skill> --limit 20
just parity <skill>              # the full sweep
```

This pins **both runtimes to the identical GGUF** and diffs the rendered output. Results
land in `evals/parity/`.

Pinning the model is the whole trick. Without it a difference could be the model sampling
differently, and you would be debugging the wrong layer.

## Reading a mismatch

A parity diff tells you the two runtimes disagree. It does not say which is right, and the
instinct to "fix native" is usually wrong.

1. **Is the declarative half genuinely shared?** A native crate that parses YAML its own way
   rather than through `knaif-core` is the most common cause.
2. **Is the prompt byte-identical?** Note `serde_yaml` is configured with `preserve_order`
   precisely because prompt example JSON must stay in trained key order — reordering keys
   silently changes what the model was tuned against.
3. **Is Python actually correct?** If not, fix Python first, then port. Fixing only native
   converts one bug into a divergence, which is worse.

## Where parity sits on the ladder

It is **phase 5** of [the eval ladder](/evaluate/ladder/) — after the artifact-level
verifiers, not instead of them.

That ordering matters: parity proves the two runtimes agree, not that either is *right*.
Two runtimes can agree perfectly on the wrong command. Establish correctness in Python with
an executing verifier first, then prove native matches it.

## When to run it

- Before shipping a native port for the first time.
- After any change to the shared declarative contract — `tools.yaml`, `prompt.yaml`,
  registry or validation semantics.
- After a Python-side planner change, since parity is what catches the port not following.

`just check-native` (fmt + clippy, warnings are errors) and `just test-native` are the fast
gates you run continuously; parity is the slow one you run at meaningful boundaries.
