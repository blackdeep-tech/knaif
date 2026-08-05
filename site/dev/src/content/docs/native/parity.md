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
