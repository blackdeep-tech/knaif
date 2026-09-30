---
title: The tool registry
description: tools.yaml — what the model sees, what it never sees, and how retrieval finds the right tool.
sidebar:
  order: 3
---

`tools.yaml` is a flat mapping. Any top-level entry with a `description` is loaded as a
tool. It is the contract both runtimes read, so a tool's shape is declared exactly once.

```yaml
compress_video:
  description: "Compress a video to a target size or quality."
  keywords: [compress, shrink, smaller, 压缩, сжать]
  required_args: [inputs]
  optional_args: [target_size_mb, quality]
  defaults:
    quality: balanced
  safety_category: destructive
```

## Fields

| Field | Meaning |
|---|---|
| `description` | The model-facing one-liner. **Required** — an entry without it is not a tool. |
| `keywords` | Trigger words for retrieval. May be shared; a keyword claimed by more than four tools errors as too generic. |
| `required_args` | Must appear in `args`. |
| `optional_args` | Accepted when present. |
| `defaults` | Filled in before validation when the model omits an argument. |
| `safety_category` | `safe` or `destructive`. Defaults to `safe`. See [Safety](/author/safety/). |
| `readonly` | Side-effect-free, so the optimizer may prune it. Defaults to `false`. |
| `internal` | Hidden from the model. Defaults to `false`. |
| `mock_args` | Template arguments used by mock inference. |

`clarify`, `reject`, `done` and `wait_for_confirmation` are injected by core. Declare them
yourself only to customise their description or mock args.

## Public and internal tools

`internal: true` hides a tool from the prompt. Internal tools are emitted **only by
expanders**, never chosen by the model.

This is what keeps the prompt small enough for a 4B model to be reliable. ffmpeg exposes
13 intent tools and hides 13 internal steps behind them — the model chooses among 13
things, not 26, and never has to know that `build_recipes` exists.

Both halves still get validated. Hidden does not mean unchecked.

## `defaults` — so the model never invents a filename

A default removes a decision the model would otherwise have to guess at or clarify about.
The obvious case is output names: `concat_video` defaults `output: combined.mp4`.

Declare a default only where the value is genuinely unambiguous. A missing required
argument **without** a default still fails, which is the correct outcome — better a
clarify than a silently invented value. An argument with a default belongs in
`optional_args`.

## Argument schemas

Beyond flat names, `arg_schemas` gives an argument a type, bounds, or an enum:

```yaml
resize_video:
  description: "Resize a video to a target resolution."
  required_args: [inputs]
  optional_args: [width, preset]
  arg_schemas:
    width:
      type: integer
      min: 16
      max: 7680
    preset:
      type: enum
      enum: [tiny, small, medium, large]
      aliases: { sm: small, lg: large }
```

An enum is the strongest instruction you can give: the model sees the allowed values, and
the planner rejects anything outside them before your handler runs. `aliases` map a
synonym the model might reasonably produce onto the canonical value, rather than failing.

Richer schemas make a better generated reference on knaif.org too — the catalog renders
whatever the schema declares.

## Keywords and retrieval

Only retrieved tools reach the prompt, so keywords decide what the model is even allowed to
consider. Two things worth knowing:

**Sharing is fine.** Retrieval down-weights a keyword by how many tools claim it. A word
claimed by more than four is rejected at load time as too generic to discriminate.

**CJK matches by character n-gram.** A query like `将clip.mp4裁剪为9:16` is one whitespace
token and would never equal the keyword `裁剪`. Retrieval emits character n-grams of length
1–4 from each CJK/kana/Hangul run, so CJK keywords up to four characters match by ordinary
set intersection — with no extra dependency, and Latin ranking unchanged by construction.

:::caution[Coverage is not the same as mechanism]
The tokenizer handles kana and Hangul, but only **Chinese** keywords are authored in the
shipped skills. Japanese and Korean utterances tokenize correctly and then find nothing.

Adding them is ordinary `tools.yaml` work needing no code change. Verify any script slice
before assuming it is covered:

```bash
uv run -m knaif.evalsuite retrieval
```
:::

## `prompt.yaml` — rules and examples

Alongside the registry, `prompt.yaml` carries skill-level rules and curated examples:

```yaml
examples:
  - request: "compress video.mp4 under 25 mb"
    output:
      plan:
        - tool: compress_video
          args: { inputs: ["video.mp4"], target_size_mb: 25 }
```

These are worth real care. They are what the model is shown, they are the truest statement
of what your skill understands — and knaif.org renders them as the "what you can say"
examples on your skill's page. Write them as sentences a person would actually type.
