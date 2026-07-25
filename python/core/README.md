<p align="center">
  <img src="https://raw.githubusercontent.com/blackdeep-tech/knaif/HEAD/media/knaif-logo-rect.svg" alt="knaif" width="260">
</p>

# knaif

**Turn natural language into validated, executable actions — without letting a language
model near your filesystem.**

knaif runs a **local** model. No API key, no token meter, nothing leaves the machine.

```python
import knaif.cli as nk

@nk.command(help="Add a task", keywords=["add", "create"])
def add(title: nk.Arg(help="task title")):
    ...

nk.App([add]).run()      # your CLI now accepts plain English
```

## Why knaif

Most ways of putting an LLM in front of a tool give the model the ability to *act* — it
emits a shell command, or calls tools in a loop, and you hope the guardrails hold.

knaif inverts that. The model is allowed to produce exactly one thing — a **plan**:

```json
{ "plan": [ { "tool": "convert", "args": { "input": "clip.mp4", "format": "webm" } } ] }
```

Everything that decides whether that plan runs is **ordinary deterministic code**: schema
validation, arguments checked against a tool registry, sandbox path checks, a safety
category per tool, and a confirmation gate. A hallucinated tool name is rejected before
execution. An argument your tool doesn't accept is rejected. A path outside the sandbox is
rejected — and re-checked after variable substitution, because substitution can introduce a
new path.

**What that buys you as a developer:**

- **Safety is a property of your registry, not the model's mood.** You classify a tool
  `destructive` once; the gate is code. Swap the model and the guarantee holds.
- **One inference call, not an agent loop.** The model proposes the whole plan; everything
  after is normal Python. No per-step round trip, no token meter, no nondeterminism in the
  middle of a workflow.
- **It runs on your user's hardware.** A small local model plus deterministic code means the
  marginal cost of the millionth run equals the first — electricity on a laptop.
- **A typed, testable surface.** Tools are declared in YAML and implemented as classes, so
  the whole thing is unit-testable without calling a model at all — mock inference is built
  in.

Scope is narrow on purpose: knaif is not a general problem solver. It targets tools that are
genuinely hard to drive by hand. **Free-form shell command generation is explicitly out of
scope** — knaif will not emit a command its own validator can't account for.

## Install

```console
pip install knaif
```

That's everything you need for mock inference and for Ollama. For in-process llama.cpp
there is an extra:

```console
pip install "knaif[llama]"
```

> **Heads-up:** `llama-cpp-python` publishes only an sdist to PyPI, so this compiles from
> source and needs CMake and a C++ toolchain. If you'd rather not build it, use the
> project's [prebuilt wheels][llama-wheels] —
> `pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu`
> — or just use Ollama, which needs no build step at all.

## Try it without writing anything

A complete example CLI ships inside the package, so you can see the whole pipeline work
before writing a line:

```console
pip install "knaif[clock]"
python -m knaif.examples.clock "list timezones in europe"
python -m knaif.examples.clock "what time is it in Tokyo"
```

With no model installed it uses mock inference and still runs end to end — parse, validate,
dispatch, result. Add Ollama or a GGUF (below) and the same command starts answering
free-form phrasing. The source is ~340 lines across three files
(`knaif/examples/clock/`): pure logic in `store.py`, then the same CLI built two ways —
`@nk.command` decorators and `from_click()`.

## Getting a model

**No model is bundled** — not in this wheel, not anywhere. knaif is the engine; you choose
what runs behind it. Three options, easiest first:

**1. Ollama** — it manages the download for you, and needs no build step:

```console
ollama pull qwen3:4b
```

```python
import knaif.cli as nk

app = nk.App([convert], orchestrator=nk.local_ollama("qwen3:4b"))
```

`local_ollama()` returns `None` and warns if Ollama isn't reachable, so your app degrades to
mock inference instead of crashing. Its defaults are also tuned for reasoning models like
Qwen3 — use the helper rather than building an orchestrator by hand, or you'll hit a
JSON-constraint deadlock. Details: [Reasoning models on Ollama][ollama-thinking].

**2. A GGUF through llama.cpp** — one call, no llama.cpp knowledge required:

```python
app = nk.App([convert], orchestrator=nk.local_llama_cpp("model.gguf"))
```

`local_llama_cpp()` picks working defaults (8k context, full GPU offload, JSON handling for
thinking-style models) so you don't have to learn llama.cpp's knobs. Override any of them —
`n_ctx`, `n_gpu_layers`, `n_threads` — only if you want to. Like `local_ollama()`, it warns
and returns `None` when the model or `llama-cpp-python` is missing, so your app falls back to
mock rather than crashing.

Any instruct-tuned GGUF that emits clean JSON works. Ready-made ones are at
**[huggingface.co/blackdeep/knaif][hf]** — `knaif-qwen3-4b-v1` (~2.5 GB) is the
general-purpose choice, `knaif-qwen3-1.7b-v1` is smaller and faster:

```console
curl -L -o model.gguf https://huggingface.co/blackdeep/knaif/resolve/main/knaif-qwen3-4b-v1-q4_k_m.gguf
```

A relative path resolves from your project root (the nearest directory containing `.git`,
else the current directory); an absolute path always works. The warning names the path it
actually tried, so a miss is easy to diagnose.

**3. Mock inference** — the default when no orchestrator is supplied. It plans from registry
keywords rather than a model, which makes the whole pipeline testable in CI with no model,
no GPU, and no network.

> The `knaif models pull` command and the `models.yaml` resolver belong to the **native CLI
> and repo checkout**, not this package. From a pip install you supply the model explicitly,
> as above.

## Two ways to use it

### 1. Add a natural-language front end to your own CLI

The **self-contained** case: everything you need is in this package.

```python
import knaif.cli as nk

@nk.command(help="Convert a file", keywords=["convert", "transcode"])
def convert(src: nk.Arg(help="input file"), fmt: nk.Arg(help="target format")):
    ...

app = nk.App([convert])
app.run()                                          # sys.argv[1] as an utterance
app.invoke("turn report.docx into a pdf", dry_run=True)
```

Already using `click`? Wrap the existing group without touching your commands:

```python
from myapp.cli import cli
app = nk.from_click(cli)
```

You get typed validation, safety categories, dry-run, and variable binding for free — the
same pipeline the full engine uses.

### 2. Build a skill

When your domain outgrows a handful of commands, package it as a **skill** — a
self-contained bundle of a YAML tool registry plus Python handlers. Skills give you intent
expansion (one user request becoming a multi-step workflow), per-tool safety categories, and
a reusable unit you can ship or hand to someone else.

A minimal skill is a directory with `skill.yaml`, `tools.yaml`, and a handlers module:

```python
# my_skill/python/handlers.py
from knaif.handler_api import HandlerContext
from knaif.skill_base import Skill
from knaif.tool import Step

class GreetStep(Step):
    name = "greet"
    def handle(self, args: dict, ctx: HandlerContext) -> dict:
        return {"message": f"hello {args['name']}"}

class MySkill(Skill):
    tools = [GreetStep]
```

Then load and run it:

```python
from knaif import CommandAgent

agent = CommandAgent.from_skill("my_skill", sandbox="./sandbox")
results = agent.execute_plan(
    {"plan": [{"tool": "greet", "args": {"name": "world"}}]},
    dry_run=True,
)
```

Every tool is a `Step` (executes, returns a result) or an `Intent` (expands into a sub-plan
of Steps). `HandlerContext` carries the sandbox root, `dry_run`, `confirmed`, and a
confirmation callback. The full contract — registry schema, safety categories, intent
expansion, preflight, result formatting — is in [TOOL_SCHEMA.md][tool-schema].

Keeping several skills side by side? Point `create_agent()` at the directory holding them:

```python
from knaif import create_agent, list_skills

print(list_skills(skills_root="./skills"))
agent = create_agent("my_skill", sandbox="./sandbox", skills_root="./skills")
```

> **No skills ship in this wheel** — the published package is the engine plus the
> `knaif.cli` SDK, so nothing pulls in dependencies for domains you don't use. If you want
> worked examples rather than a blank page, the repository carries two production skills
> (media processing and document handling) you can read or copy from.

## Requirements

- Python 3.10–3.14
- A local model for real inference (llama.cpp via the `llama` extra, or Ollama). Mock
  inference needs nothing.
- Skills that shell out to external tools need those tools installed — external binaries are
  never bundled.

## Documentation

| | |
|---|---|
| [Skill authoring][tool-schema] | registry schema, `Step` / `Intent`, safety categories |
| [Developer SDK][sdk] | `@nk.command`, `nk.App`, `from_click` |
| [Architecture][architecture] | the plan pipeline end to end |
| [Inference backends][inference] | model resolution, llama.cpp and Ollama setup |
| [Released models][models] | which models, why those, how they were tuned |
| [Scope and safety][requirements] | what knaif deliberately will not do |

Source, issues, and releases: **[github.com/blackdeep-tech/knaif][repo]**

## License

Apache-2.0 — see [LICENSE][license] and [NOTICE][notice].

[repo]: https://github.com/blackdeep-tech/knaif
[tool-schema]: https://github.com/blackdeep-tech/knaif/blob/HEAD/docs/TOOL_SCHEMA.md
[sdk]: https://github.com/blackdeep-tech/knaif/blob/HEAD/docs/SDK.md
[architecture]: https://github.com/blackdeep-tech/knaif/blob/HEAD/docs/ARCHITECTURE.md
[inference]: https://github.com/blackdeep-tech/knaif/blob/HEAD/docs/INFERENCE.md
[requirements]: https://github.com/blackdeep-tech/knaif/blob/HEAD/docs/REQUIREMENTS.md
[license]: https://github.com/blackdeep-tech/knaif/blob/HEAD/LICENSE
[notice]: https://github.com/blackdeep-tech/knaif/blob/HEAD/NOTICE
[models]: https://github.com/blackdeep-tech/knaif/blob/HEAD/docs/MODELS.md
[hf]: https://huggingface.co/blackdeep/knaif
[ollama]: https://ollama.com
[ollama-thinking]: https://github.com/blackdeep-tech/knaif/blob/HEAD/docs/INFERENCE.md#reasoning-models-on-ollama--leave-thinking-on
[llama-wheels]: https://abetlen.github.io/llama-cpp-python/whl/cpu/
