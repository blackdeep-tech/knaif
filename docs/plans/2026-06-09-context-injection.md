# Context Injection — optional world-state prepended before inference

**Status:** Planning · **Created:** 2026-06-09 · **Updated:** 2026-07-22 · **Completed:** —
**Owner:** core / planner · **Ref:** —

> **Status note:** **Parked (2026-06-21)** — keep, do not retire. Deliberately
> deferred until the **ffmpeg UI** work, which is the natural trigger (a UI surfacing
> the visible files / current selection is exactly the app-state this injects);
> revisit then. Unblocked, not retired.
> **Depends on:** NL clarify gate (shipped, PR #14), descriptor analyzer (shipped).
> **Enables:** the "injection ON" world both of the above are written against.
> **Risk:** medium — adds a pre-inference step and changes the prompt the model sees.
>
> The date in the filename is the authoring date, per the `docs/plans/YYYY-MM-DD-` convention —
> it is **not** a staleness signal. It is deliberately shared with
> [nl-clarify-gate](2026-06-09-nl-clarify-gate.md) and
> [descriptor-mixed-intent-analyzer](2026-06-09-descriptor-mixed-intent-analyzer.md), which were
> designed alongside it; that is what makes this the "co-designed other half" the three refer to.
>
> ### Refresh 2026-07-22 — read before implementing
>
> The design still holds. Two things have moved underneath it since June:
>
> 1. **The hook mechanism below is stale.** It mirrors the `PREFLIGHTS` / `SUMMARIZERS`
>    module-level dicts, which **no longer exist** — the OOP skill refactor replaced them with
>    `Skill` methods. Read "The hook" and T1 as *intent*, not as a spec: the modern shape is a
>    `Skill.provide_context()` method (see the rewritten section below).
> 2. **Part of T6 is already wired.** `nl_clarify_gate()` in
>    [nl_clarify_gate.py](../../python/core/knaif/nl_clarify_gate.py) already takes an
>    `injected_files: set[str] | None` argument — `None` means injection OFF, a set means ON.
>    The gate half of the two-world contract exists; what is missing is anything that *produces*
>    that set.
> 3. **A second mechanism already ships for the producing half — reuse it or retire it, but do
>    not add a third.** (Found 2026-07-22; corrects points 1–2, which were written without it.)
>    [nl-clarify-gate](2026-06-09-nl-clarify-gate.md) T4 shipped
>    [`knaif/injectors.py`](../../python/core/knaif/injectors.py): a named-injector registry
>    plus a `pipeline: inject: [...]` key in `skill.yaml`, parsed by `Skill.load()` into
>    `skill.pipeline_inject`. It answers the same question this plan does — *what files are
>    available* — but returns a `set[str]` for the gate rather than prompt text for the model.
>    The two are complementary in principle (one changes what the model **sees**, the other what
>    the gate **accepts**), and the ideal build feeds one gathered listing to both.
>
>    It is only **half-wired**, so adopting it is cheap: `skill.pipeline_inject` is stored but
>    read by nothing, and `resolve_injected_files()` has no caller outside its tests. Deciding
>    between a `pipeline: inject:` declaration and a `Skill.provide_context()` method — or how
>    they compose — is **T0 of this plan**, ahead of T1.
>

> Also note [deterministic-clarify](2026-06-06-deterministic-clarify.md) T16, which recorded a
> 2026-06-06 decision **not** to build file-listing injection. This plan supersedes that half of
> it; the reasoning T16 gives (context-window cost, and that an attribute like "4K" cannot be
> derived from a filename) survives as the argument for Tier-1 caps and for Tier 2 existing at
> all.

**Goal:** Optionally prepend world-state (visible files, current selection) to the
model prompt before inference, so plans resolve against real app context.

> ## Parking note (2026-06-21)
>
> Discussion conclusions, recorded so the revisit starts warm:
> - **Not redundant with the clarify gate** — it's the explicitly co-designed *other
>   half*: the gate is the safety floor (downgrade blind guesses to `clarify`);
>   injection is what lets the model legitimately act by grounding it in the real
>   listing. Without injection the gate stays permanently over-conservative.
> - **Both dependencies have since shipped**, so it is now *unblocked* — parking is a
>   priority choice, not a blocker.
> - **Mechanism aligns with the shipped CLI SDK / a future ffmpeg UI:** the generic
>   context-provider seam is how an app shows the model its world-state (visible
>   files, current selection). Tier-1 file-listing is the ffmpeg case; the mechanism
>   generalizes to UI/app-state — which is why it belongs with the ffmpeg UI effort.
> - **Orthogonal to the chain-fidelity / fine-tuning work** — addresses blind
>   descriptor resolution, a different failure class.
> - **Before any build, run the cheap de-risk first:** manually hand-inject a listing
>   for the rows the descriptor analyzer predicted would flip OFF→ON and confirm qwen
>   actually flips, before committing to the 7-task build.

## Why

Today every inference is a **bare standalone line**: `agent.infer(utterance)` builds
the prompt from the user's words alone. The model therefore resolves descriptors
("the 4K video", "the audio file") *blind* — it has no idea what files exist, so it
either guesses a filename (the failure the NL clarify gate downgrades to `clarify`)
or clarifies when a human glancing at the folder would just act.

Both the descriptor analyzer
([2026-06-09-descriptor-mixed-intent-analyzer.md](2026-06-09-descriptor-mixed-intent-analyzer.md))
and the NL clarify gate
([2026-06-09-nl-clarify-gate.md](2026-06-09-nl-clarify-gate.md)) are written around a
two-world model — **injection OFF** (today) and **injection ON** (future) — and both
say the ON world "reads a flag; same code path". **This plan builds that flag and the
step behind it.** It is the thing that flips the gate from "downgrade to clarify"
to "let the model resolve against the real listing".

This is the defensible form of the multi-step-inference idea: the second piece of
work the model does is justified *only because it now sees world-state it could not
have seen blind*. It is not the model re-judging its own tokens.

## Mechanism vs. content (why this is core, not ffmpeg)

| Layer | Generic? | Home |
|---|---|---|
| **Mechanism** — run a read-only step before inference, prepend its text to the prompt | yes | `knaif/` core |
| **Default content** — list the sandbox (`ls`-equivalent) | yes (any sandboxed skill: `io`, `ffmpeg`) | core-provided provider |
| **Rich content** — ffprobe each file for resolution / codec / duration | no | ffmpeg skill |

Core owns the plumbing and a built-in sandbox-listing provider. Skills may register a
richer provider. This respects the CLAUDE.md rule: no skill-specific logic in core.

This is the *first-call analog* of what [`run()`](../../python/core/knaif/agent.py)
already does with execution **history** — it seeds the loop with world-state *before*
the first inference instead of after the first step.

## The hook

> **Rewritten 2026-07-22.** The original design registered a `CONTEXT_PROVIDERS` dict
> mirroring `PREFLIGHTS` / `SUMMARIZERS`. Those module-level dicts were removed by the OOP
> skill refactor — the current extension surface is the `Skill` class in
> [skill_base.py](../../python/core/knaif/skill_base.py), whose optional hooks (`preflight`,
> `format_results`, `run_artifact`) are **methods with default no-op implementations**.
> Context injection follows that same shape.

An optional `Skill` method, alongside `preflight` / `format_results` / `run_artifact`:

```python
class Skill:
    def provide_context(self, utterance: str, *, sandbox: Path | None, root: Path) -> str | None:
        """Return text to prepend to the model prompt, or None to inject nothing."""
        return None
```

- The base implementation returns `None`, so every existing skill is unaffected and no
  registration step is needed — a skill opts in purely by overriding the method. This is why
  the dict's `"*"` wildcard key is no longer needed: there is nothing to key.
- Per-intent providers are not offered. The original plan already noted they make little sense
  pre-inference (the intent is not yet known), and a method has no natural place to express
  them.
- `sandbox` is `Path | None` — open CLI mode runs with `sandbox=None` and resolves against
  `root`, so a provider must handle both (same base-directory rule as preflight and the
  deterministic-clarify gate).
- Returns a short block. Core prepends it to the **user** message (not the system
  header) so it reads as observed state, e.g.:

  ```
  Files in the working folder:
    clip_4k.mp4, intro.mov, voiceover.mp3

  convert the 4k video to the smallest file size
  ```

- Returning `None` / `""` is a no-op (injection contributes nothing that turn).

## Tiers (opt-in, increasing cost)

| Tier | Injected | Provider | Latency / tokens |
|---|---|---|---|
| **0** | nothing (today) | — | none |
| **1** | sandbox file listing | core built-in | one `os.scandir`, a few tokens |
| **2** | listing + media probe (resolution/codec/duration) | ffmpeg skill | N× ffprobe, more tokens |

Default **OFF (Tier 0)**. Tier is selected by the flag below. Tier 1 is enough to
flip the NL gate; Tier 2 is what makes "the 4K video" resolvable by *attribute*
(resolution) rather than just by filename.

## The flag

Mirror the `show_plan` / `require_approval` pattern
([agent.py](../../python/core/knaif/agent.py)): a constructor arg with a
per-call override.

```python
CommandAgent(..., inject_context=False)         # default OFF
agent.infer(utterance, inject_context=True)     # per-call override
```

`create_agent()` / `from_skill()` thread it through like the other hook flags.

## Where it runs

Inside [`infer()`](../../python/core/knaif/agent.py), **before**
[`build_prompt()`](../../python/core/knaif/agent.py):

1. If `inject_context` is on and a provider is registered, call it with
   `(utterance, sandbox=self.sandbox, root=self.root)`.
2. Prepend the returned block to the user utterance passed into `build_prompt`
   (or pass it as a dedicated `context_block` arg to `build_prompt` so the system
   header / examples are untouched — **decide in review**, see open questions).
3. Proceed exactly as today. The model now plans against real files.

The mock path (`_mock_response`) ignores injection (mock is keyword-driven and has
no model to feed) — but tests can still assert the provider was *called* and the
block *built*, so the plumbing is testable without a live model.

### Interaction with the NL clarify gate

These are the OFF and ON halves of one decision and must be ordered deliberately:

- **Injection OFF:** gate sees an utterance naming no file → `clarify`.
- **Injection ON:** the model, having seen the listing, emits a concrete filename →
  the gate's "utterance carries a concrete reference?" check must treat the
  **injected listing as part of the available set** (the gate plan's ON column). So
  the gate reads the *same* `inject_context` flag and widens its available-files set
  when it is on. One flag drives both.

Confirm in review: the gate's available-set builder and this provider should share
the listing so they cannot disagree (single source of truth — likely both go
through `knaif/input_refs.py` from the gate plan's "Code sharing" section).

## Interaction with `run()` history

`run()` already injects *execution results* as history into later inferences. Context
injection seeds the **first** call. They must not double-inject: when `run()` has
history, the listing is stale relative to executed steps, so inject the provider
block **only on the first iteration** (empty history). Later iterations rely on
history. Verify there's no duplication of the listing across turns.

## Worked example

`"convert the 4k video to the smallest file size"`, sandbox =
`{clip_4k.mp4, intro.mov, voiceover.mp3}`:

- **Tier 0 (today):** model guesses `clip_4k.mp4` (lucky) or clarifies. Gate
  downgrades the guess to `clarify`.
- **Tier 1:** listing injected; model sees one `*_4k.*` file, emits
  `clip_4k.mp4`; gate's available set includes it → **plan**.
- **Tier 2:** probe injected; even an utterance like "shrink the 4K one" with two
  videos resolves by the probed `3840x2160` attribute, not the filename.

## Test plan (TDD)

Unit (core):
- provider registered + flag ON → block is built and prepended; flag OFF → not called.
- provider returns `None` → prompt identical to Tier 0.
- core sandbox-listing provider returns the sorted basenames in the sandbox.
- `run()` injects only on the first (empty-history) iteration.

Integration (`python/core/tests/test_agent.py`, requires a provider):
- listing injected → `build_prompt` user message contains the filenames.
- gate + injection ON: descriptor + listing with one match → plan; ≥2 → clarify
  (the gate plan's ON-world table, now driven live).

Eval (regression, the real proof):
- Re-run qwen3-4b / gemma3-4b with `inject_context=True` (Tier 1) and compare the
  descriptor-analysis OFF→ON delta predictions against measured outcomes. The
  analyzer already *predicted* which rows flip RISK→SAFE and PRIZE→POLICY-CONFLICT;
  this run confirms the prediction. Guard that `plan` accuracy rises (descriptors
  now resolvable) without `clarify` rows regressing.

## Tasks

- [ ] T1 — `Skill.provide_context()` optional method on `skill_base.Skill` (default returns
      `None`), reached from `CommandAgent` via `self.skill_instance`. Test an overriding skill
      and a non-overriding one. *(Was: a `CONTEXT_PROVIDERS` dict mirroring `PREFLIGHTS` —
      re-scoped 2026-07-22, those dicts no longer exist.)*
- [ ] T2 — Core built-in sandbox-listing provider (Tier 1), pure + tested. Must handle
      `sandbox=None` (open CLI mode) by listing `root`.
- [ ] T3 — `inject_context` flag (constructor + per-call) and the prepend in
      `infer()` / `build_prompt()`. TDD the prompt-shape unit tests.
- [ ] T4 — First-iteration-only injection in `run()`; test no double-inject.
- [ ] T5 — ffmpeg Tier-2 provider (probe-backed listing) in the skill.
- [ ] T6 — Feed the produced listing into `nl_clarify_gate(..., injected_files=...)` and have
      the gate read the same flag. **Reduced scope:** the gate already accepts `injected_files`
      and implements the ON-world branch — only the producer side and the flag wiring remain.
- [ ] T7 — Eval run; confirm the analyzer's OFF→ON delta; record numbers.

## Open questions for the discussion

1. **Prompt placement.** Prepend to the user message, or a dedicated `context_block`
   arg into `build_prompt` rendered in its own labelled section? The latter is
   cleaner and lets the model distinguish "observed state" from "the request".
2. **Listing size cap.** A sandbox with 500 files blows the context. Cap at N and
   summarise ("…and 480 more"), or filter by the skill's media extensions before
   injecting? Tier 1 generic vs Tier 2 skill-filtered changes the answer.
3. **Provider failure.** If the provider raises (bad sandbox, permission), inject
   nothing and proceed (Tier 0 fallback), or surface an error? Lean: swallow +
   proceed, like the `intent_completed` callback's `except` today.
4. **Flag granularity.** One boolean (`inject_context`) selecting "best available
   tier the skill offers", or an explicit tier enum? A boolean is simpler; a skill
   either ships a provider or not.
5. **Caching.** Within a single `run()` the sandbox rarely changes mid-loop — but we
   only inject on iteration 1 anyway, so probably no cache needed. Confirm.

## Non-goals

- Conversation threading / cross-turn memory — out of scope (the analyzer plan is
  explicit: standalone line only). Injection augments a *single* call's prompt.
- Foreign-language descriptor comprehension — unchanged; injection surfaces
  filenames, which are language-agnostic.
- Letting the model *choose* to list files agentically. The provider is
  deterministic and flag-driven; we do not rely on a 4B model deciding to observe
  first.
- Substituting files for descriptors in code — that remains the model's job (now
  better-informed) or the stem resolver's. Injection only changes what the model
  *sees*.
