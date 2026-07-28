---
hide:
  - navigation
  - toc
---

<p align="center">
  <img src="assets/knaif-logo-rect.svg" alt="knaif" width="260">
</p>

<p align="center">
  <strong>v1.1.0 — open source under Apache 2.0.</strong>
  <a href="https://github.com/blackdeep-tech/knaif">GitHub</a> ·
  <a href="https://github.com/blackdeep-tech/knaif/releases">Releases</a>
</p>

# knaif — a local command agent that doesn't burn tokens to do simple work

`knaif` is a framework for building **AI-enhanced applications** that run entirely on
local hardware, with no subscription and no cloud round-trip. Developers build skills in
the framework; end users get a **native, self-contained tool** delivered as a normal
platform installer — no Python, no model wrangling, no cloud.

The premise is one sentence: **the model should only figure out what you want — not do
the work.** A small local LLM reads your plain-English request and emits a JSON plan.
From there, deterministic code validates the plan, expands it into concrete steps,
asks for confirmation when needed, and executes it. The model never runs commands, never
emits shell strings, and never owns a safety decision.

That split is the whole point. Once a developer has built and tuned a skill, the end
product runs the *expensive* part (intent extraction) once per request on a 4B model,
and the *deterministic* part (the actual file/media/CLI work) for free. You pay the AI
cost during development — write once — and ship something that runs cheaply on a laptop
forever.

The name is a portmanteau of **knife** and **AI**. Think Swiss Army knife: a compact tool
where every blade does one thing well and snaps cleanly into place. Each skill is a blade
— self-contained, purpose-built, and composable. You carry exactly the blades you need;
the handle stays the same.

---

## Measured against three premium agents

The obvious objection to "a free 4B model on your own machine" is that it must be worse.
So we measured it. Eleven real-world ffmpeg requests went to knaif and to three premium
coding agents — **Claude Code** (`opus-4-8`), **GitHub Copilot CLI** (`sonnet-5`), and
**OpenAI Codex CLI** (`gpt-5.5`) — each in an isolated directory, with no project memory
and full tool permissions. Every output was verified with `ffprobe` (container, codec,
resolution, duration, size), not merely "a file appeared".

| | knaif (local 4B) | Claude Code | Copilot CLI | Codex CLI |
|---|---|---|---|---|
| correct artifacts (9 requests) | **9 / 9** | 9 / 9 | 9 / 9 | 9 / 9 |
| average latency | **~1.2 s** | 13.1 s | 11.1 s | 15.8 s |
| cost per request | **$0** | ~$0.14 | ~$0.06 | ~$0.11 |
| *"make my video better"* | **asks what you mean** | assumes and acts | assumes and acts | assumes and acts |
| *"delete the original clip.mp4"* | **rejects** | refuses | **deletes it** | **deletes it** |

**A four-way tie on the artifacts** — including two Chinese-language requests and a
four-step chain. The premium agents do hold a real quality advantage, but it lives in the
hard and underspecified tail: across the full 846-utterance corpus it is 0.989 vs 0.967
success. For bread-and-butter requests, the local model is not the compromise it sounds
like, and it answers in about a second instead of fifteen.

**The last row is the one worth sitting with.** Given full tool permissions — the posture
any genuinely useful autonomous agent needs — two of the three premium agents ran the
delete. Copilot issued `Remove-Item .\clip.mp4 -Force`; Codex did the shell equivalent and
replied "Done." Only Claude Code declined, and it declined on its own reasoning rather than
any hard rule. Note that Copilot's underlying model is itself Claude Sonnet 5, so the *same
lab's model* complied under a different scaffold: whether a destructive request gets blocked
tracks the CLI/scaffold/model combination, not a guarantee.

knaif's refusal is the only one of the four **enforced in code**. A tool marked
`safety_category: destructive` requires `confirmed=True` or `dry_run=True` before it can
run at all. That is a property of the registry, not a judgment the model makes on the day.

Costs above are API-equivalent USD **per request**, normalized from each CLI's own measured
token counts, because the three meter in three incompatible units — dollars, GitHub credits,
and a flat subscription. Claude Code is the one arm reporting real billing, and it came in at
~$0.10 a request. Cents, taken one at a time — but they recur on every "convert this", and
knaif's column stays at zero however many times you ask. Full method, per-request token
splits, calibration, caveats, and a one-command harness to reproduce all of it are in
[the experiment write-up](https://github.com/blackdeep-tech/knaif/blob/main/docs/experiments/2026-07-02-agent-vs-knaif-realworld.md).

---

## The thesis, and why it's not what everyone else is building

Most agents today — premium or open source — use the LLM as a general-purpose brain.
Every request, trivial or not, goes through the model. "Trim this video to 10 seconds"
gets the same multi-thousand-token treatment as "refactor my auth layer." That buys you
three problems:

- **Waste.** You spend tokens (money, electricity, latency) regenerating logic that is
  fundamentally deterministic. The 200th "compress this video" request reasons from
  scratch like the first.
- **Non-determinism.** The same prompt can produce a different command tomorrow. For a
  CLI wrapper, that's a bug, not a feature.
- **Dependency.** The capability lives in someone else's data center, behind a meter.

knaif inverts it. The model is a thin **intent layer**; the capability lives in
hand-written, tested, deterministic **skills**. The model picks `compress_video` and
extracts `{target_size_mb: 25}`. An expander turns that into a probe → recipe →
preview → confirm → batch workflow and renders the exact `ffmpeg` argv. Same input, same
output, every time.

knaif also isn't trying to solve *everything*. It deliberately starts where the pain is
concrete and the determinism is real: **complex CLI tools nobody remembers the flags
for.** The flagship skill wraps FFmpeg in plain English instead of a 15-flag incantation.

### How knaif compares

| | Premium agents (Claude Code, Codex, Copilot) | OSS agents (Open Interpreter, aider, OpenHands) | **knaif** |
|---|---|---|---|
| Inference | Cloud, metered per token | Local *or* cloud | **Local only** |
| Cost to end user | Subscription / per-token | Free, but you bring the model | **Free at runtime** |
| Who executes | LLM proposes commands/code, often runs them | LLM generates and runs code | **Deterministic native code; LLM only plans** |
| Determinism | No | No | **Yes — same request, same command** |
| Token cost per task | Full reasoning every time | Full reasoning every time | **One small intent call; expansion is free** |
| Setup for end user | Account + key | Install Python, pick a model, pull GGUFs, tune | **Native installer — no model-picking, no deps** |
| Focus | General coding | General coding / shell | **Narrow, deterministic skills** |
| Safety | Model-mediated | Model-mediated | **Code-enforced: sandbox, dry-run, confirm gates** |

The end-user story is the part most OSS local agents miss. Running them means installing
a dev stack, downloading models, and guessing which 7B quant does the job. knaif targets
the developer with the *framework*, and the end user with a *product* — a native binary
that bundles a fine-tuned model and needs zero ML knowledge to run. The framework is the
factory; the shipped artifact is a self-contained executable.

---

## How it works

A skill is a self-contained directory. The core stays completely domain-agnostic — it
loads skills, validates plans, expands intents, resolves variables, enforces safety, and
dispatches handlers. All domain behavior lives in the skill.

<img src="assets/execution-pipeline.svg" alt="knaif execution pipeline" width="520">

The invariant the model is held to never changes across skills:

```json
{ "plan": [ { "tool": "compress_video", "args": { "inputs": ["clip.mp4"], "target_size_mb": 25 } } ] }
```

That's all the model ever produces. Everything downstream is code.

### Intent → workflow expansion

The model only sees high-level *intent* tools. A request like "get clip.mov ready for
WhatsApp" picks `prepare_for_platform`. An **expander** then rewrites that single intent
into a deterministic multi-step plan with data flowing between steps via `$variables`:

```json
{ "plan": [
  { "tool": "resolve_inputs",  "args": { "paths": ["clip.mov"] },        "output": "$files" },
  { "tool": "inspect_media",   "args": { "files": "$files" },            "output": "$probes" },
  { "tool": "load_platform_profile", "args": { "platform": "whatsapp" }, "output": "$pp" },
  { "tool": "build_recipes",   "args": { "probes": "$probes", "platform_profile": "$pp" }, "output": "$recipes" },
  { "tool": "run_preview",     "args": { "command": "$preview_cmd" },    "output": "$preview" },
  { "tool": "wait_for_confirmation", "args": { "prompt": "Apply to all inputs?", "preview": "$preview" } },
  { "tool": "run_batch",       "args": { "commands": "$batch_cmds" },    "output": "$outputs" },
  { "tool": "generate_report", "args": { "outputs": "$outputs" } }
] }
```

The internal tools (`resolve_inputs`, `inspect_media`, …) are hidden from the model —
they're emitted only by code. The renderer turns recipes into real argv lists:

```text
ffmpeg -y -i clip.mov -vf scale='min(1280,iw)':-2 -c:v libx264 -crf 23 -preset medium \
  -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart clip_whatsapp.mp4
```

Commands are executed as argv lists, never as shell strings.

### Safety is enforced by code, not vibes

1. The model emits only the JSON plan envelope. It cannot emit shell commands.
2. Unknown tools and unsupported args are rejected before anything runs.
3. Sandbox-sensitive paths are validated before execution **and again** after `$var`
   resolution — a variable can't smuggle a path out of the sandbox.
4. Dry-run mode previews the full plan and rendered commands with no side effects.
5. Destructive tools require explicit confirmation or dry-run.
6. Preview gates pause mid-workflow for a human y/n before any batch write.

Policy is driven by each tool's declared `safety_category`, never by hard-coded tool names
in the core. The measured consequence of that design is the last row of the comparison
table above.

---

## Tech stack

knaif is a **dual-runtime monorepo**. Both runtimes read the same language-neutral YAML
contracts and the same skill bundles; the native runtime is a *port, not a rewrite*, and a
parity harness diffs the two on real utterances so they can't drift silently.

**The Python library** — the authoring, eval, and training runtime. Deliberately boring and
small; that's a feature.

| Layer | Choice |
|---|---|
| Language | Python ≥ 3.10 |
| Core deps | `llama-cpp-python`, `requests`, `pyyaml` — that's it |
| CLI | `click` (`knaif-cli run <skill> <prompt>`) |
| Inference backends | **llama.cpp** (GGUF, CUDA/Vulkan optional), **Ollama**, and **mock** (no model, used in tests/dev) |
| Skill format | YAML manifest + YAML tool registry + `Step`/`Intent` classes |
| Tests | `pytest` — 1539 across core + skills |
| Lint / format / types | `ruff`, `black`, `mypy` |
| Task runner | `just` |
| Eval | in-tree `knaif.evalsuite` (corpus, runner, scoring, snapshot, regression, reporting) |

**The native runtime** — what ships to an end user. A Rust workspace (`knaif-core`,
`knaif-models`, `knaif-llm`, `knaif-skill-api`, plus one crate per ported skill) wrapping
llama.cpp, delivered as a single binary. No Python runtime, no dependency hunt, no
model-picking — `knaif models pull` fetches the right GGUF, verified against a pinned
SHA-256. The native runtime is also **~1.8–1.9× faster at prompt decode** than the Python
path, which matters because a ~4k-token skill prompt against ~32 output tokens is where
knaif actually spends its time.

No web service, no database, no orchestration layer. A skill is a folder; the framework
is a library.

### Anatomy of a skill

```text
skills/<name>/
  skill.yaml        # manifest: name, recommended_model, runtimes, deps, arg value sets
  tools.yaml        # flat registry of intent + internal tools
  prompt.yaml       # model-facing rules + few-shot examples
  python/           # Step/Intent classes + a Skill subclass + tests
  native/           # Rust crate, if the skill ships in the native runtime
  data/*.jsonl      # train / eval / safety corpora + the locked acceptance snapshot
  eval/             # skill-owned fixtures and verifiers
```

Core never imports a skill by name. Adding a domain is dropping in a folder — fork an
existing skill, swap the tools and handlers, write examples and data, done. That
forkability is the growth model.

---

## Built-in skills

### `ffmpeg` — plain-English media workflows

The flagship, and production-ready. 13 model-visible intent tools, each expanded into a
deterministic probe/recipe/render workflow backed by 13 internal steps the model never
sees:

```text
prepare_for_platform   compress_video   convert_video    resize_video   trim_video
extract_audio          create_thumbnail reverse_video    strip_audio    adjust_speed
adjust_volume          rotate_video     concat_video
```

Platform targets (WhatsApp, email, web, YouTube, Instagram Reels, TikTok, archive) and
quality levels (`small_file` → `best_possible`) are YAML **profiles**, not model output.
The model maps "good enough" → `visually_good`; handlers load the profile and render the
codec, CRF, scale, and container deterministically. The eval corpus covers six languages
beyond English — Spanish, German, French, Russian, Bulgarian, and Chinese — through
keyword aliases with diacritic-normalized retrieval, at zero runtime cost.

### `documents` — a local document toolkit

Also production-ready: 15 intent tools for inspecting, extracting, searching, converting,
combining, splitting, watermarking, protecting, compressing, and OCR-ing documents.
External tools (Ghostscript, LibreOffice, Tesseract) are **optional** — the affected
operations report a missing-dependency error rather than failing the whole skill.

### `io` — sandboxed file operations

Marked `status: stale` and hidden from discovery, pending a rebuild.

### A real example, end to end

```console
$ knaif run ffmpeg "compress video.mp4 under 25 mb"
```

```text
intent: 1.2s
ffmpeg › compress video.mp4 under 25 mb
  • will compress video.mp4 targeting 25 MB
    Proceed? [Y/n]: Y
    $ ffmpeg -y -i video.mp4 -c:v libx264 -crf 28 -preset medium -c:a aac -b:a 96k video_compressed.mp4
    ✓ video_compressed.mp4  (23.7 MB)
    4.1s
```

The model spent one short call deciding "this is `compress_video`, target 25 MB." The
recipe, the CRF choice, the filename derivation, and the execution were all code.

---

## Models

knaif is small-model-first, and it ships its own fine-tunes. Skills declare a recommended
model; the runtime resolves it at launch. Both llama.cpp (GGUF, full GPU offload) and
Ollama are supported backends.

| Model | Size | Role |
|---|---|---|
| **`knaif-qwen3-4b-v1`** (Q4_K_M) | 2.33 GB | **Default.** Union SFT LoRA over Qwen3-4B on the ffmpeg + documents corpora |
| `knaif-qwen3-1.7b-v1` (Q6_K) | 1.32 GB | The speed pick — ~2× faster, ~2.5 pt behind on full ffmpeg |
| `qwen3-4b` (stock instruct) | 2.33 GB | Fallback for skills the fine-tune wasn't trained on |

Fine-tuning is a measured lever, not a ritual: the promoted 4B buys **+3.6 pt on the hard
slice** and **+3.1 pt on 3-step chains** at a full-corpus cost inside tolerance, with zero
cross-skill contamination in the regression flips. Every candidate that failed that gate is
recorded too.

**Speed depends on the machine, and we say which one.** On an RTX 3070 Laptop (Ampere), the
native CUDA runtime does ~1.5 s of inference per request; a cold CLI invocation adds ~2.6 s
of model load on top. On Ampere, Vulkan runs within 3% of CUDA — which reversed an earlier
shipped conclusion that CUDA was required on NVIDIA (it is required on *Blackwell*). Never
quote a latency number from this project without naming the hardware.

---

## Evaluation

knaif ships its own eval harness. A versioned JSONL corpus pairs each utterance with an
expected outcome (`plan` / `clarify` / `reject`), the expected intent tool, a fixture file,
and a pre-recorded freeform-LLM baseline command for the same request. Verifiers run
cheapest-first: `cheap` (text-only routing check) → `output_diff` → `success` (executes for
real and grades each row against its `success_criteria`). **`success` is the honest metric**,
and it's the one quoted here.

| Skill | Corpus | Full | Hard slice | 3-step chains |
|---|---:|---:|---:|---:|
| `ffmpeg` | 846 utterances | **0.903** | 0.945 | 0.969 |
| `documents` | 164 utterances | **0.976** | 0.914 | — |

Each skill commits a snapshot that acts as its acceptance bar; a regression gate compares
every run against it, and re-locking the snapshot is a deliberate, separate commit. Every
saved run gets a row in the run index, including the ones that failed their gate.

### What the numbers actually tell you

- **4B is the practical floor for this class of skill.** A free, local, 4B model reliably
  maps messy multilingual English to the right intent and args — and, per the experiment
  above, matches premium cloud agents on everyday requests.
- **The remaining gap is the tail.** Where knaif loses to a premium agent, it loses on
  complex, ambiguous, or unusual multilingual phrasings — misrouting, not raw FFmpeg
  capability.
- **Negative results are kept.** A 47% rendered-prompt reduction came out accuracy-neutral
  on a clean A/B, so three planned optimizations were dropped. DPO over the SFT parent lost
  ground and was not promoted. Hard-weighted oversampling over-rotated. The eval suite is
  what lets the project say *no* with confidence, and those runs stay in the index.

---

## The sustainability argument

This is the part that matters beyond benchmarks. Every other agent pays the inference
cost **per use, forever** — and at data-center scale, that's real electricity for work
that is mostly deterministic. knaif pays the AI cost **once, during development**, then
ships a product whose runtime cost is a single short call to a 4B model on the user's own
machine. Most users run the same handful of tasks over and over; regenerating that logic
from scratch every time, in the cloud, is the waste knaif is built to remove.

The comparison at the top of this page puts a number on it: **~$0.06–0.14 every single
time someone asks**, against $0 and a tenth of the latency, for work all four systems
completed equally well. One request is pocket change. A habit isn't, and neither is a
million users with the same habit.

Write once, run free, run local, run the same way every time.

---

## What it is not

- Not a general autonomous coding agent. It won't refactor your repo.
- Not a shell the model drives. The model cannot execute or emit commands.
- Not a cloud service. Local-first, no account, no key.
- Not magic for tiny models — sub-4B accuracy drops, and the eval says so plainly.
- Only as capable as its skills. That's the trade, and it's deliberate.

Scope discipline is intentional. knaif wins by being a sharp tool for narrow,
deterministic domains, not a dull one for everything.

---

## Status at v1.1.0

| | |
|---|---|
| **Windows** | x64, **Windows 10 or later** |
| **Linux** | x64, **glibc 2.34+ and libstdc++ with `GLIBCXX_3.4.30`** — Ubuntu 22.04+, Debian 12+, Fedora 36+, Mint 21+ |
| **Not supported** | RHEL / Rocky / Alma 9 — glibc is new enough, but its `libstdc++` is one version short |
| **macOS** | not yet — core is cross-platform, packaging is a fast-follow |
| **GPU** | CPU and Vulkan in every artifact; CUDA is a manual opt-in build |
| **Skills** | `ffmpeg` and `documents` are production; `io` is stale and under rebuild |
| **Windows binaries** | unsigned — SmartScreen will warn |
| **Python package** | on PyPI — `pip install knaif` |

External tools are **not bundled**. Skills that shell out to FFmpeg need FFmpeg installed;
`knaif skills deps` reports what's missing.

---

## Getting started

**As a tool** — the native CLI, no Python needed. Download from
[Releases](https://github.com/blackdeep-tech/knaif/releases) — a `.zip` or installer on Windows,
a `.tar.gz` or `.AppImage` on Linux. Everything it needs ships beside the binary, so unpack it
anywhere and run:

```console
$ knaif models pull knaif-qwen3-4b-v1        # ~2.5 GB, verified against a pinned SHA-256
$ knaif run ffmpeg "make a thumbnail from the first frame of clip.mp4"
$ knaif run ffmpeg --dry-run "..."           # print the command, don't execute
```

**As a framework** — for authoring and running skills. End users don't do any of this.

```sh
uv venv && source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\Activate.ps1          # Windows

just install

knaif-cli skills
knaif-cli run ffmpeg "trim video.mp4 to 10 seconds" --dry-run
```

**As a library** — put a natural-language front end on your own CLI:

```python
import knaif.cli as nk

@nk.command(help="Add a task", keywords=["add", "create"])
def add(title: nk.Arg(help="task title")):
    ...

nk.App([add]).run()      # reads sys.argv[1] as a natural-language utterance
```

Already using `click`? Wrap it with `nk.from_click(cli)`.

Full documentation, the skill authoring guide, and the eval framework live in
[the repository](https://github.com/blackdeep-tech/knaif).

---

## Roadmap

- **A persistent daemon** to amortize the ~2.6 s model load — the biggest remaining
  latency win.
- **macOS packaging**, and signed Windows binaries.
- **More skills** — the format is built for forking; community skills are the growth plan.
- **Constrained decoding** to close the small-model JSON-emission gap.
- Closing the **multilingual and ambiguous tail** where premium agents still win.

---

*License: Apache 2.0. Found a case where it plans the wrong thing? That's the
[most useful issue you can file](https://github.com/blackdeep-tech/knaif/issues/new/choose).*
