# Requirements

## Purpose

Build a local, AI-powered command agent library that translates natural-language requests into structured, safe, executable action plans. The current implementation is skill-based: new domains are added as skill packages, while `knaif/` remains domain-agnostic.

## Target Users

- Developers building narrow local command agents
- Power users who want natural language over repeatable local workflows
- Privacy-conscious users who prefer local inference and local execution
- Researchers evaluating small local models for constrained JSON planning

## Current Scope

The current library ships with two active built-in skills (plus `io`, which is
`status: stale` and hidden from discovery pending a rebuild):

- `documents`: local document toolkit — inspecting, extracting, finding, converting, combining, and compressing files
- `ffmpeg`: media workflow intent tools expanded into deterministic FFmpeg workflows
- `io`: sandboxed file listing, finding, moving, and deletion (**stale** — under rebuild)

Primary interface is the Python library API. A CLI may be added later on top of `create_agent()` and `CommandAgent`.

## Out Of Scope

- General-purpose autonomous agent behavior
- Free-form shell command generation by the model
- Background autonomous execution
- Cloud-hosted service behavior
- Replacing domain tools such as FFmpeg with model-generated command strings

## Functional Requirements

1. Accept natural-language input through `CommandAgent`.
2. Produce a structured JSON action plan referencing only tools in the active skill registry.
3. Support multi-step plans and variable binding between steps.
4. Expand high-level skill tools into deterministic internal workflows when `Intent` tools are defined.
5. Detect ambiguity and ask clarifying questions instead of guessing.
6. Reject unsafe or out-of-scope requests.
7. Enforce safety categories before execution.
8. Require explicit confirmation or dry-run mode for destructive tools.
9. Support dry-run previews for handlers that perform side effects.
10. Allow new domains through skill packages under `skills/<name>/`, with a manifest, tool definitions, handlers, prompt examples, data files, and tests.
11. Allow local model backends to be swapped through `InferenceOrchestrator`.

## Non-Functional Requirements

- Local-first by default.
- Deterministic execution after model planning.
- Small-model friendly prompts and output contracts.
- Skill-local domain behavior.
- Tests for core behavior and each built-in skill.
- Safe failure for invalid plans, unknown tools, bad args, and missing handlers.

## Safety Requirements

- The model must never execute directly.
- The model must output only the JSON plan envelope.
- Every tool call must validate against the active registry.
- Destructive tools must require `dry_run=True` or `confirmed=True`.
- Preview gates may use `wait_for_confirmation`.
- Handlers must respect `ctx.dry_run`.
- Sandbox-sensitive file operations must reject paths outside the intended sandbox.

### Refusal routing is a metric, not a guardrail

A small model routes many safety-sensitive requests to `clarify` rather than `reject`
(measured on qwen3-4b: `reject` 35%, `safety` 25%, `exfiltration` 12.5%,
`sandbox_escape` 0%). That is a **quality** number, not a security hole — the model is
not executing those requests, it is asking a question, and every listed requirement
above is enforced by deterministic sandbox-path validation and handler preflight
regardless of which label the model picked.

So do not treat low refusal-routing accuracy as a security regression, and do not chase
it with prompt tuning: the failing patterns are highly varied (FTP upload, shell exec,
overwrite-in-place, system-root access, impossible upscales), tuning a 4B model to
refuse all of them is demonstrably unreliable, and it buys the refusals back as
over-refusal on legitimate `plan` rows. Reliable multi-shot refusal behavior needs a
larger model; until then the deterministic layer is the guardrail and the routing
number is scoreboard-only.

## Dataset And Evaluation Requirements

Skill datasets should use JSONL rows containing at least:

```json
{"utterance": "list text files", "plan": {"plan": [{"tool": "list_files", "args": {"path": ".", "file_type": "text"}}]}}
```

Evaluation should track:

- tool selection accuracy
- argument accuracy
- schema validity
- clarify precision and recall
- reject precision and recall
- unsafe-action prevention
- workflow dry-run success for expander-heavy skills

## Success Criteria

- Built-in skills can be loaded through `create_agent()` and `CommandAgent.from_skill()`.
- All plan validation and safety gates are deterministic.
- New skills can be created by adding skill package files without importing that skill from core code.
- Core and skill tests pass with `uv run pytest --tb=short`.

## Glossary

- **Skill**: A self-contained domain package under `skills/<name>/`.
- **Tool**: A declared operation in a skill's `tools.yaml`.
- **Action Plan**: The JSON object `{ "plan": [...] }` emitted by the model or an expander.
- **Handler**: A `Step` subclass whose `handle()` executes a tool.
- **Expander**: An `Intent` subclass whose `expand()` converts a high-level tool into internal plan steps.
- **Safety Category**: `safe` or `destructive`, attached to a tool definition.
