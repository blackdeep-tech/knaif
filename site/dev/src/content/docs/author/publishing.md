---
title: Publishing
description: What makes a skill appear on knaif.org, and why the catalog stage is derived rather than declared.
sidebar:
  order: 5
---

The skill catalog on [knaif.org](https://knaif.org/skills/) is generated from your bundle.
Nobody writes a page for your skill; the extractor reads `skill.yaml`, `tools.yaml` and
`prompt.yaml` and produces one.

That means the way to get a good page is to write good metadata.

## `display:` — the catalog copy

```yaml
display:
  title: FFmpeg
  tagline: "Convert, compress, and resize video without remembering a single flag."
  category: media
```

| Key | For |
|---|---|
| `title` | The human name. `FFmpeg`, not `ffmpeg`. |
| `tagline` | One sentence a non-developer understands. |
| `category` | Groups the skill in the catalog filter. |

It exists because `description:` is written for a **different reader** — that field feeds
retrieval and these docs, so reusing it on a landing page produces flat copy, and deriving
a title from `name` produces "Ffmpeg".

**A skill without `display:` fails the website build.** It is optional to the runtime and
required to publish. A broken card in a public catalog is worse than a failed build, and a
silent fallback is precisely how a skill ships with placeholder copy nobody noticed.

## Catalog stage

How finished your skill looks is **derived from evidence**, not declared:

| Stage | Catalog | Derived when |
|---|---|---|
| `stable` | Full card | `data/eval_snapshot.json` exists |
| `preview` | Shown, badged *in development* | No snapshot yet |
| `hidden` | Not published | Only by explicit `display.stage: hidden` |

The evidence is the **locked acceptance bar** — the same thing that makes a skill done
everywhere else in this project. Advertising a skill and locking its snapshot are therefore
the same act, and neither can be forgotten independently of the other.

The reason it is derived rather than declared: `status:` defaults to `active`, so a
half-finished skill dropped into `skills/` would advertise itself as production-ready
without anyone having made a wrong decision. Derivation closes that by default.

To move a skill to `stable`, [lock its snapshot](/evaluate/). That is the whole path.

### Overriding

```yaml
display:
  stage: hidden      # stable | preview | hidden
```

Use it when the derivation is genuinely wrong. It **cannot** publish a `status: stale`
skill — the website never resurfaces what the runtime hides from `list_skills()`.

## What else the page shows

| Section | Comes from |
|---|---|
| What you can say | `prompt.yaml` `examples:`, filtered to action-producing ones |
| What it can do | Your public tools and their descriptions |
| What it needs | `dependencies.external_tools`, split required vs optional |
| Confirmation behaviour | `safety_category` across your tools |

Two consequences worth designing for:

**Your `prompt.yaml` examples are public copy.** They are already the truest statement of
what your skill understands, and they are what a prospective user reads first. Write them
as sentences a person would actually type.

**Your tool descriptions are public too.** They are model-facing *and* reader-facing, so
"Compress a video to a target size or quality" beats "compress_video handler".

## Regenerating

```bash
just site-data      # rewrites site/data/site-data.json
```

The output is committed and drift-guarded — a test regenerates it and fails if it differs,
so a skill change that alters the catalog cannot merge without the catalog changing too.

The extractor also publishes only the **intersection** of your registry and your loaded
tool classes. A `tools.yaml` entry with no `Step`/`Intent` behind it raises rather than
reaching a page, so the site cannot advertise a tool the runtime would reject.
