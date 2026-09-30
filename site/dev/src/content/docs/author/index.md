---
title: Author a skill
description: What a skill is, what the bundle contains, and the loop from empty folder to something worth shipping.
sidebar:
  order: 1
---

A **skill** teaches knaif a domain. It is a folder — declarative YAML at the top, a Python
package underneath, and optionally a Rust crate beside it.

The core stays completely domain-agnostic. It loads skills, validates plans, expands
intents, resolves variables, enforces safety and dispatches handlers. Everything specific
to video, or documents, or whatever you are adding, lives in your bundle.

:::note[Skills are not on PyPI]
Bundles are loaded by path, not imported, and are deliberately excluded from the wheel. To
author one, clone the repo and work inside `skills/`. `pip install knaif` gives you
[the SDK](/sdk/), which is a different thing for a different job.
:::

## The bundle

```
skills/<name>/
  skill.yaml        # manifest: name, display copy, deps, runtimes
  tools.yaml        # the tool registry the model reads
  prompt.yaml       # model-facing rules + curated examples
  SPEC.md           # human-facing spec, lead with System Requirements
  python/           # Step / Intent classes + a Skill subclass + tests
  native/           # Rust crate, if it ships in the native runtime
  data/             # eval, train and safety corpora + the locked snapshot
  eval/             # fixtures and skill-specific verifiers
```

Only `skill.yaml`, `tools.yaml`, `python/__init__.py` and `python/handlers.py` are
strictly required. The rest is what turns a working folder into a skill worth shipping.

The declarative half sits at the **top** of the bundle because both runtimes read it. A
tool's name, arguments, keywords and safety metadata are declared exactly once, in YAML,
so the Python and Rust implementations agree by construction rather than by discipline.

## The minimum

```yaml
# skills/my_skill/skill.yaml
name: my_skill
description: "One-line description."
tools: tools.yaml
skill_class: handlers.MySkill
prompt: prompt.yaml

display:
  title: My Skill
  tagline: "What it does, in one sentence a non-developer understands."
  category: media
```

```python
# skills/my_skill/python/handlers.py
from knaif.handler_api import HandlerContext
from knaif.skill_base import Skill
from knaif.tool import Step

class MyToolStep(Step):
    name = "my_tool"

    def handle(self, args: dict, ctx: HandlerContext) -> dict:
        return {"result": "..."}

class MySkill(Skill):
    tools = [MyToolStep]
```

`skill_class:` resolves module-relative — `handlers.MySkill` means class `MySkill` in your
`handlers.py`. The loader fails fast if a listed class is not a `Step`/`Intent`, has no
matching `tools.yaml` entry, or collides on `name`.

:::caution[`display:` is required to publish]
It is optional to the *runtime* and required to appear on knaif.org. A skill without it
fails the website build rather than rendering a card with placeholder copy — see
[Publishing](/author/publishing/).
:::

## Resolve paths from `ctx.skill_dir`, never `__file__`

`ctx.skill_dir` is always the **bundle root**, so handlers find `profiles/`, `vocab.yaml`
and `data/` from there. Resolving relative to a handler's own `__file__` breaks the moment
the bundle layout changes or the skill is loaded from a different root — and it has no
equivalent at all in the native runtime.

## The loop

Authoring the handlers is step one of five. A skill is not done when it works; it is done
when it is measured.

1. **Author** the tools and handlers — this track.
2. **Evaluate.** Write `data/eval.jsonl`, climb the verifier ladder, lock a snapshot.
   [Evaluate a skill](/evaluate/).
3. **Fine-tune**, if routing needs it. [Fine-tuning](/fine-tuning/).
4. **Port to native**, if it ships in the binary. [Python to native](/native/).
5. **Publish** — the catalog picks it up once it has a locked acceptance bar.

Steps 2 and 4 are where most of the real work is. Budget accordingly.

## Where to go next

| | |
|---|---|
| [Steps and Intents](/author/steps-and-intents/) | The tool contract, and when one tool should become a workflow |
| [The tool registry](/author/registry/) | `tools.yaml`, argument schemas, keywords, public vs internal |
| [Safety](/author/safety/) | How the confirmation gate works, and how to classify honestly |
| [Publishing](/author/publishing/) | `display:`, catalog stages, and what makes a skill appear |
