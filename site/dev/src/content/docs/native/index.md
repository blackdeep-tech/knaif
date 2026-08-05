---
title: Python to native
description: Porting a skill to the Rust runtime, and proving parity.
---

:::caution[Scaffold]
Track D — plan §5. Written before launch.
:::

Why the runtime is dual, the `knaif-skill-api` crate, declaring `runtimes:` in
`skill.yaml`, and `just parity <skill>`.

The rule that governs the whole track: **it is a port, not a rewrite** — same prompt, same
validation, same expansion, so the same utterance renders the same command on both sides.

Source material: `docs/NATIVE.md`.
