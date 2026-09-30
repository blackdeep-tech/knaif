---
title: Python to native
description: Why knaif has two runtimes, what "a port, not a rewrite" actually forbids, and how the contract is shared rather than duplicated.
sidebar:
  order: 1
---

knaif ships in two runtimes over one contract.

| Runtime | Role |
|---|---|
| **Python** (`python/core`) | Authoring, evaluation, fine-tuning |
| **Native** (Rust) | What an end user downloads — one binary, local GGUF inference, no Python |

They share the declarative half of every skill **verbatim**: `skill.yaml`, `tools.yaml`,
`prompt.yaml`, profiles, data. Only the handlers are reimplemented.

That split is why a tool's name, arguments, keywords and safety category are declared once
in YAML rather than twice in code. The two runtimes cannot disagree about a tool's shape,
because there is only one description of it.

## A port, not a rewrite

:::danger[This is the rule the whole track exists to enforce]
Same prompt, same validation, same expansion — so **the same utterance renders the same
command on both sides**.
:::

Worth being precise about what that forbids, because each is individually tempting and
collectively fatal:

- No "improved" prompt in the native runtime.
- No extra validation Python does not do, and none skipped that it does.
- No different expansion order, even where the new one is tidier.
- No fixing a Python bug only on the native side.

A divergence is not a native-runtime bug. It is **two products sharing a name**, and users
will find the seam before you do.

If Python's behaviour is genuinely wrong, fix Python first, then port the fix.

## The workspace

| Crate | Responsibility |
|---|---|
| `knaif-core` | The deterministic contract — parse, normalize, validate, registry, prompt build, retrieval, safety gates, clarify gate, skill discovery. **No inference deps.** |
| `knaif-models` | Model store + manifest. **No inference deps**, so a model-management UI can embed it without linking llama.cpp. |
| `knaif-llm` | Backends behind the `LlmBackend` trait — `MockBackend` and `LlamaCppBackend`. |
| `knaif-skill-api` | The native skill contract — `HandlerContext`, `Step`/`Intent` equivalents, sandbox helpers. Mirrors Python's `handler_api` / `tool`. |
| `apps/cli` | The `knaif` binary — argument parsing and output formatting only. |

Two crates deliberately carry **no inference dependencies**. That is what lets a desktop or
mobile front-end embed the engine without dragging llama.cpp along.

## Declaring a runtime

```yaml
# skills/<name>/skill.yaml
runtimes:
  python:
    handlers: handlers.MySkill
  native:
    status: supported
    crate: knaif-skill-my-skill
```

Consumed by `knaif skills list` and the CLI. A skill may be Python-only, native-only, or
both — native v1 ships `ffmpeg` and `documents`.

Your crate is a workspace member in the root `Cargo.toml` and consumes `knaif-skill-api`.

## The authoring loop

```bash
just native-mock -- skills list     # fast build, mock backend, no llama.cpp
just check-native                   # fmt + clippy, warnings are errors
just test-native                    # cargo test --workspace
just parity <skill> --limit 20      # native vs Python on real utterances
```

`native-mock` is the loop you live in — it skips llama.cpp entirely, so the build is fast
and the whole deterministic half is still exercised.

## What is never bundled

GPU SDKs (CUDA, Vulkan) and heavyweight external tools (ffmpeg, LibreOffice, Ghostscript,
Tesseract) ship with **no** artifact. All third-party Rust dependencies are
Apache-2.0-compatible; nothing GPL or AGPL is bundled.

`knaif skills deps [<name>]` reports what each skill declares and whether it resolves on
PATH — detection only, never modifying PATH. It is the same probe that drives the installer
component tree and the `run` preflight.

## Next

| | |
|---|---|
| [Proving parity](/native/parity/) | The golden fixtures, the diff harness, and what a mismatch actually means |
