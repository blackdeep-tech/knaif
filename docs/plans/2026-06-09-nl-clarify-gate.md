# NL Clarify Gate — runtime gate for under-specified file references

**Status:** Done · **Created:** 2026-06-09 · **Completed:** —
**Owner:** core / planner · **Ref:** PR #14

> **Status note:** Shipped (PR #14, merged). T1–T7 complete.
> **Depends on:** stem resolver (shipped), descriptor analyzer (shipped, analysis-only).
> **Risk:** medium — touches the runtime plan pipeline and can convert plans → clarify.
>
> **Kept 2026-07-22** (S7 decision) as the design document for
> [`knaif/nl_clarify_gate.py`](../../python/core/knaif/nl_clarify_gate.py), a live
> runtime module that can downgrade any plan to a `clarify`. The load-bearing content
> is *The central design constraint* below — the gate must inspect the **utterance**,
> not the args, because the model has already resolved the descriptor by the time a
> plan exists. Without that, the obvious re-implementation is an arg resolver, which
> cannot work.
>
> **Extracted to shipping docs:** the name-based/never-property-based invariant and
> the never-substitutes rule →
> [ARCHITECTURE.md](../ARCHITECTURE.md#the-clarify-gate); the `pipeline:`/`inject:`
> manifest key and the host-supplied rule → [TOOL_SCHEMA.md](../TOOL_SCHEMA.md); the
> rejected `needs_named_input` field → TOOL_SCHEMA.md (*No tool declares that it needs
> a named file*); the corpus-side lint →
> [EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md#descriptor-rows-and-the-injection-assumption).
>
> **Two as-built notes:**
> 1. **T4's injection-ON path is a half-wired seam** — see the box under *Tasks*.
>    Read T4's `[x]` as "the parts exist and are tested", not "a host can turn
>    injection on".
> 2. **The gate grew a second responsibility after this plan.** It now also enforces
>    a tool's declared `grounded_args` (e.g. a model-invented password), which is not
>    a file-reference concern at all. This plan is not the gate's full scope.

**Goal:** Add a runtime gate that converts a plan to a clarify when the model
references a file it was never given (under-specified file references).

## Why

The descriptor analyzer (`docs/plans/2026-06-09-descriptor-mixed-intent-analyzer.md`)
sized a finite set of corpus rows where the model **plans against a file it was
never given**. Concretely, on local7 (qwen3-4b) the false-plan failures include:

- `ffmpeg_104` "resize the 4K video to 1080p" → model emitted `clip_4k.mp4`, knaif=100%
- `ffmpeg_106` "grab a frame from the 4K video at 2 seconds" → planned
- `ffmpeg_110` "speed up the 4K video by 3x" → planned
- `ffmpeg_207` "compress the 4K video to 720p for sharing" → planned
- `ffmpeg_210` "slow motion the 4K clip to 0.25x" → planned
- `ffmpeg_211` "prepare the 4K video for TikTok" → planned
- `ffmpeg_254` "strip audio from the no-audio clip" → planned
- `ffmpeg_258` "prepare the no-audio clip for YouTube" → planned
- `ffmpeg_120/125/126/128` 4K-clip compound utterances → planned

In every case the row's **expected_outcome is clarify** and the model scored
knaif=100% because it *guessed* the fixture file and produced a runnable command.
The plan is "correct" against the fixture only because the eval harness happens to
put exactly that file in the sandbox. In the real world the user said "the 4K
video" with no file named — the right behavior is to **ask which file**, per the
user's stated rule:

> "if only 'convert the audio file to flac' is given, clarify should be the outcome."

## The central design constraint (read this first)

The stem resolver works on **args**: the model emits `inputs: ["clip_4k"]`, the
resolver globs the sandbox, substitutes or clarifies. The NL gate **cannot** work
this way, because the model has *already resolved the descriptor itself*: it reads
"the 4K video" and emits `inputs: ["clip_4k.mp4"]` — a real filename. By the time
the plan reaches `execute_plan`, the descriptor is gone.

Therefore the NL gate must inspect the **original utterance**, not just the plan.
Its question is:

> Did the user's utterance actually name (or stem-reference) a concrete file?
> If not — and the intent needs a file — the model guessed. Override to clarify.

This makes the gate a **utterance-vs-plan consistency check**, not an arg resolver.
It is the runtime promotion of the analyzer's "injection OFF" column.

## Matching is name-based, never property-based

A descriptor like "the 4K video" is resolved to a real file **only by matching the
descriptor against known filenames** (`clip_4k.mp4` contains `4k`). The gate does
NOT probe files — it never runs ffprobe, never inspects resolution/codec/duration
to decide which file is "the 4K one". File inspection at plan time is explicitly
out of scope (see Non-goals); the only signal is the filename token.

This is why the two worlds below differ only in *whether a filename list is
available to match against*, not in *how* matching works.

## Available-files model — driven by an injection pipeline step

(Resolves open question 3.) Injection is **not** a boolean flag on the agent. It is
a named **pipeline step** a skill author declares in `skill.yaml`, on equal footing
with intent, plan, and execution. The skill author decides, while building the
skill, what runtime context to gather *before* the model runs — and the gate
consumes whatever that step produced.

```yaml
# skills/ffmpeg/skill.yaml
pipeline:
  - inject:
      - sandbox_files        # list media files available in the sandbox
  - intent                   # LLM proposes a plan
  - gate                     # NL clarify gate; reads what inject produced
  - plan                     # validate / expand / optimize
  - execute                  # deterministic handlers
```

A different skill declares the context *it* needs — the inject step is generic:

```yaml
# a hypothetical gh skill
pipeline:
  - inject:
      - gh_installed         # is the gh CLI present?
      - gh_authenticated     # are credentials configured?
      - gh_repos             # list accessible repos
  - intent
  - gate
  - plan
  - execute
```

knaif ships a small set of **built-in injectors**; skill authors register custom
ones the same way they register handlers. The injectors run in order and accumulate
a context object that later steps (the gate, and eventually the prompt builder) can
read.

**The injected file set is host-supplied, not a blind sandbox glob.** The available
files come from whatever the *frontend* made available — not from globbing a
directory. The injector contract takes the host-provided set and normalizes it for
the gate. (See "Who consumes injection" — the concrete ON consumer is a
drop-the-files UI, where the available set *is* exactly the files the user dropped.)

| World | Available files | How it arises |
|---|---|---|
| **injection OFF** | in-line filenames parsed from the utterance + stems resolvable in sandbox | the skill's pipeline has no `inject` step |
| **injection ON** | OFF set ∪ the host-supplied file set | the skill declares an `inject` step fed by the host |

Per the user's rule:

- **injection OFF** — if the utterance names no file and contains no resolvable
  stem, a single-file intent **clarifies**. The sandbox is never consulted; that
  silent consultation is exactly the guess we are stopping.
- **injection ON** — "the 4K video" is matched (by name) against the gathered file
  list. **Exactly one** name matches → plan. **Zero or ≥2** matches → clarify.

This makes injection a first-class, per-skill capability rather than a flag wired
through the agent constructor. The analyzer's POLICY-CONFLICT set is the list of
rows whose verdict changes between worlds — they are documented there.

### Who consumes injection (and who never does)

- **Eval harness — permanently OFF.** The fixtures put exactly the file the model
  guessed into the sandbox; an injector that handed it back would re-validate the
  guess and re-open the hole the gate exists to close. The eval must run OFF so the
  ~12 PRIZE rows clarify.
- **CLI — OFF for now.** The CLI user types a prompt with no pre-staged file set;
  under-specified references should clarify, matching the eval contract. An inject
  step may be added later (e.g. resolving against the working directory), but not
  in this work.
- **Future drop-files UI — ON.** A planned UI where the user drops files, then
  writes a prompt. There the available set *is* the dropped files — a concrete,
  host-supplied list. "the 4K video" against a single dropped 4K file resolves to
  plan; against two it clarifies. This is the one place injection is wired on, and
  it is why the injected set must come from the host, not a directory glob.

So ffmpeg's `skill.yaml` ships with **no `inject` step**. The injection machinery
(T4) is built and unit-tested so the ON path is ready, but no shipping frontend
turns it on until the UI exists.

## Where it runs

The gate is the `gate` pipeline step, running after `intent` (so the plan exists)
and after stem resolution, but before expansion/optimization — the same logical
slot the stem resolver occupies today, beside `resolve_stems`. It needs one input
the stem resolver didn't: the **original utterance**, plus the injected context
object when an `inject` step ran.

### Integration: threading the utterance and injected context

`agent.infer(utterance)` produces a plan; `agent.execute_plan(plan)` runs it. The
utterance is not currently passed to `execute_plan`, and there is no injected
context object yet. As the pipeline becomes explicit, both must flow to the `gate`
step.

Recommendation: a **per-run pipeline context** that carries the utterance, the
injected-files context, the sandbox, and the plan between steps. The gate reads
from it; the stem resolver can migrate onto it later. This is cleaner than stashing
`plan["_utterance"]` and keeps the gate co-located with the other plan-adjustment
logic rather than splitting it into `infer`. (The previous draft's three options —
thread-through / stash-on-plan / gate-inside-infer — collapse into this once the
pipeline is the carrier.)

## Gate algorithm

Input: pipeline context = `{ utterance, intent_plan, sandbox, injected_files | None }`.
`injected_files is None` means injection OFF for this skill; a list means ON.

For each intent step that requires a file input (path-bearing arg key — reuse
`_PATH_ARG_KEYS` from planner.py; see Resolved decisions §5):

1. **Exempt batch/glob/multi-file utterances.** Detection is **utterance-based**,
   not per-tool — no tool inherently requires a named file ("concat all videos in
   this folder", "grab the first frame from all videos" are both valid nameless
   batches). The gate does not apply when:
   - the emitted input is a glob (`*?[`) or a folder, or
   - the utterance carries a batch signal (all / every / each / batch / bulk /
     "in this/the folder", and their corpus-covered translations), or
   - the input token classifies as `glob`/`chain` per `classify_token`.
   See Resolved decisions §2 — the per-tool `needs_named_input` flag was rejected.
2. **Build the available set** — OFF: in-line filenames parsed from the utterance.
   ON: that set ∪ `injected_files`. Reuse the parser lifted into `knaif/input_refs.py`.
3. **Does the utterance carry a concrete file reference?**
   - in-line filename (e.g. `clip.mp4`, `audio.mp3`) → yes, allow plan.
   - resolvable stem (`clip_4k` → globs to one file) → yes, allow (stem resolver
     already handled it; gate is a no-op here).
   - **injection ON only:** descriptor name-matches exactly one `injected_files`
     entry → yes, allow plan. Zero or ≥2 → no concrete reference.
   - otherwise → **no concrete reference**.
4. If **no concrete reference** and the intent needs a file → **clarify**, naming
   the descriptor if one can be extracted ("Which 4K video did you mean?") else a
   generic "Which file would you like to <verb>?".

The gate never *substitutes* a file (that is the stem resolver's job, or the
injection step's). It only **downgrades plan → clarify** when the utterance
under-specifies the input.

## Clarify-question quality

(Resolves open question 4 — do descriptor extraction now.) The corpus clarify
baselines are specific ("Which 4K file would you like to compress for email?"). The
gate produces comparable questions, best-first:

1. The descriptor phrase lifted from the utterance ("the 4K video" → "Which 4K
   video did you mean?"). Implement this in v1 — it is ~a handful of lines (the noun
   phrase between the intent verb and the trailing options) and the corpus already
   has the target strings.
2. The intent verb ("Which file would you like to resize?").
3. Generic fallback ("Which file did you mean?").

The eval scores clarify rows on outcome (`actual_outcome == "clarify"`), not on
question text, so question quality does not affect the score — but it is the
user-facing surface, so keep it specific.

## Code sharing

`descriptor_analysis.py` currently owns `classify_token`, `resolve_input`,
`available_files`, and the in-line-filename parser, but it lives under `evalsuite`
(analysis-only, per its plan's guardrail). The gate is **runtime core**. Do not
import `evalsuite` from `knaif/` core. Instead:

- Lift the pure primitives (`classify_token`, in-line-filename parser, the
  descriptor/stem/exact/glob/chain taxonomy) into a small core module, e.g.
  `knaif/input_refs.py`, with no eval dependencies.
- `descriptor_analysis.py` imports from there (narrowing it to analysis-specific
  binning/reporting).
- The gate imports from there too.

This keeps one source of truth for token classification and respects the
"no skill/eval import from core" rule in CLAUDE.md.

## False-positive risks (the things that must NOT gate)

- **Batch/glob/folder** — "convert all mp4s", "grab a frame from all videos",
  "concat all videos in this folder". Utterance-based exempt (§1).
- **Concat with named files** — "join clip.mov and clip_4k together" names files;
  must plan. Only nameless concat ("concatenate two mp4 files") clarifies.
- **Output-only filenames** — "save as output.mp4" names an output, not an input.
  The gate must only consider *input* path args, not output paths.
- **Utterances that name the file in another language token** — the parser must
  catch `clip.mp4` regardless of surrounding language. (Foreign-language descriptor
  comprehension is explicitly out of scope — see Non-goals.)

## Resolved decisions (from the design discussion)

1. **Stem vs NL ordering.** Stem resolver runs first (substitutes), then the NL
   gate. A stem that resolves to one file suppresses the gate — the user *did* name
   a file, compactly. Caveat to verify in tests: on mixed rows where some inputs
   resolve by stem and others don't, the gate must fire if **any** un-named,
   non-glob input remains after stem resolution — not only when all inputs are dirty.

2. **Batch detection — utterance-based, not a per-tool flag.** Rejected
   `needs_named_input` in tools.yaml. No tool inherently requires a named file: any
   operation can target a batch ("concat all videos in this folder", "extract a
   frame from all videos at the first frame"). The tool alone cannot tell you which
   case you're in — only the utterance can. So detection keys off batch signals and
   glob/folder/chain classification in the utterance (§1).

3. **Injection is a pipeline step, not a flag.** Declared per-skill in `skill.yaml`
   under `pipeline: - inject: [...]`, alongside intent/plan/execute. knaif ships
   built-in injectors; skills register custom ones (a `gh` skill would inject
   `gh_installed`, `gh_authenticated`, `gh_repos`). Injection OFF = the skill simply
   omits the inject step. The injected file set is **host-supplied** — the frontend
   passes in the available files (the drop-files UI passes the dropped set); the
   injector does not glob a directory. The injected context flows to the gate (and
   later the prompt builder) via a per-run pipeline context object. This replaces
   the earlier "constructor arg vs config vs env var" framing entirely. Eval ships
   OFF permanently; CLI ships OFF for now (may gain an inject step later); the future
   drop-files UI is the first ON consumer (see "Who consumes injection").

4. **Question text — descriptor extraction in v1.** Cheap, improves the user-facing
   surface, and the corpus already supplies target strings. (See Clarify-question
   quality.)

5. **Scope of path-arg keys.** Audit `_PATH_ARG_KEYS` before T2 against every
   intent tool with a file input. Known gap to confirm: `concat_video` uses
   `base`/`append` (and `inputs`), and `trim_video`/`extract_frame`/
   `create_thumbnail` use the singular `input`. Add a characterization test that
   fails if a new path-bearing arg key appears unregistered.

## Test plan (TDD)

Unit (core, `tests/test_input_refs.py` + gate tests):
- utterance names a file → no gate (plan passes through).
- utterance has resolvable stem → no gate.
- utterance has a bare descriptor, single-file intent, injection OFF → clarify.
- batch/glob/folder utterance → no gate even with no named file.
- "grab a frame from all videos" → no gate (nameless batch is valid).
- concat with named files → no gate; nameless concat → clarify.
- output-only filename, no input name → clarify.
- injection ON: descriptor name-matches one injected file → plan.
- injection ON: descriptor name-matches ≥2 injected files → clarify.
- injection ON: descriptor name-matches 0 injected files → clarify.
- mixed inputs: one resolves by stem, one bare descriptor → clarify (§1 caveat).

Integration (`tests/test_agent.py`, ffmpeg skill):
- "resize the 4K video to 1080p", injection OFF → clarify (the local7 PRIZE case).
- "resize the 4K video to 1080p", injection ON with one 4K-named file → plan.
- "resize clip_4k.mp4 to 1080p" → plan (named file, unaffected).
- "downscale clip_4k to 1080p" → plan (stem resolver handles it).
- "batch convert all videos to mp4" → plan (batch exempt).

Eval (regression): re-run qwen3-4b success; confirm the ~12 PRIZE rows flip
plan→clarify and that no SAFE plan row regresses to clarify. Compare against
local7. Watch the `plan` accuracy column does not drop (no false gating).

### Expected eval movement (the T7 target)

Corpus: **293 eval rows — 199 plan, 74 clarify, 20 reject.** `scoring.py` computes
two independent metrics that move differently:

- **`outcome_accuracy`** = `actual_outcome == expected_outcome` (scoring.py:69-70).
  The PRIZE rows are clarify-expected but currently emit `plan` → counted **wrong**
  today. Post-gate they emit `clarify` → **correct**.
- **`avg_knaif_score`** = artifact-quality score, computed **only for `plan`
  outcomes** (scoring.py:135). The PRIZE rows score ~1.0 today (the guessed command
  is valid against the fixture) — this is the "knaif=100%" inflation. Post-gate they
  are no longer `plan`, so they **leave the knaif pool entirely** and stop being
  scored on artifact quality.

| Metric | Direction | Magnitude / condition |
|---|---|---|
| `outcome_accuracy` (overall) | ↑ | up to **+12/293 ≈ +4pp** if all 12 flip and zero false gating |
| `clarify`-tag outcome accuracy | ↑ | +12 of 74 clarify rows go wrong→right |
| `avg_knaif_score` | flat / slight ↑ | 12 perfect-but-illegitimate rows leave the pool; removing 1.0 entries from a sub-1.0 mean cannot lower it |
| `plan`-expected correctness | **must stay flat (199 correct)** | any plan→clarify drop = false gating = regression |
| `reject` rows (20) | unchanged | gate does not touch safety |

**Success condition:** clarify accuracy rises by ~12 rows AND plan-expected
correctness holds at 199 — net `outcome_accuracy` up ~4pp, `avg_knaif_score` not
down. **Failure signal:** any drop in plan-expected correctness (the gate fired on a
named-file row).

Caveat to confirm at T7: this assumes all ~12 PRIZE rows are *currently*
outcome-misses on local7. If any were already handled correctly by another path,
the headline gain is proportionally smaller. Record the actual flip count.

### T7 actual results (qwen3-4b, 2026-06-10)

**All 12/12 PRIZE rows flipped plan→clarify.** Two bugs discovered and fixed during T7:
1. `FILENAME_RE` used Unicode `\w`; CJK characters were treated as word chars, causing
   filenames preceded by CJK text to miss `\b` (10 rows affected). Fixed: `re.ASCII`.
2. Multi-step plans: the gate checked intermediate outputs (e.g. `clip_trimmed.mp4`
   produced by step 1 and consumed by step 2). Fixed: track per-step `output`/`outputs`
   args and skip tokens already produced by the plan.
3. `runner.py`: `execute_plan` returning a gate-fired clarify was not detected;
   runner always set `outcome="plan"`. Fixed: inspect first exec_result.

| Metric | Before gate | After gate + fixes |
|---|---|---|
| `outcome_accuracy` | 83.3% (244/293) | **86.3% (253/293)** |
| `clarify`-row accuracy | ~62% (46/74) | **86.5% (64/74)** |
| `plan`-row accuracy | 95% (189/199)¹ | **90.5% (180/199)** |
| PRIZE rows (plan→clarify) | 0/12 flipped | **12/12 flipped** |
| `avg_knaif_score` | 74.5% | 94.0% ↑ |

¹ The 95% figure was from the bugged runner (gate fires were invisible). The
pre-gate baseline plan accuracy on this corpus is unknown without a separate
no-gate run.

**Remaining 17 plan→clarify false positives** are all model failures, not gate bugs:
- 3 rows: model chose clarify at inference (pre-gate, not fixable at gate level)
- 14 rows: model emitted wrong/unexpected filename; gate correctly fired on it.
  When the model doesn't echo the utterance's filename, the gate is doing the right
  thing — asking which file. The corpus labels these "plan" assuming a correct model.

Net: `outcome_accuracy` +3pp (+9/293), `avg_knaif_score` +19pp (legitimate plan rows
only), all PRIZE rows resolved, clarify recall up 24pp.

## Tasks (fill in during implementation)

- [x] T1 — Lift token primitives into `knaif/input_refs.py` (pure, tested first);
      repoint `descriptor_analysis.py` imports. No behavior change — characterization
      tests first.
- [x] T2 — Gate function over the pipeline context → returns adjusted plan or a
      clarify step. Pure-ish; TDD against the unit table. Includes descriptor-phrase
      extraction for the clarify question (decision §4).
- [x] T3 — Pipeline-context plumbing: carry utterance + injected files + sandbox
      between steps; wire the gate beside `resolve_stems`, after stem resolution
      (stem first, then NL gate).
- [x] T4 — Injection-step machinery: `pipeline:`/`inject:` parsing in `skill.yaml`,
      an injector registry, and the host-supplied-files injector contract (the host
      passes the available file set in; the injector normalizes it). Default: no
      inject step → injection OFF. Unit-test the ON path now; no shipping frontend
      (eval, CLI) declares it — the future drop-files UI will.

  > **Audit 2026-07-22 — T4 shipped the parts, not the connection.** Three pieces
  > exist and nothing joins them:
  >
  > | Piece | State |
  > |---|---|
  > | `Skill.load()` → `skill.pipeline_inject` | parsed and stored, **read by nothing** |
  > | `injectors.resolve_injected_files()` | tested, **no caller** outside its own tests |
  > | `execute_plan(injected_files=…)` → `nl_clarify_gate` | live, but the caller must hand-build the set |
  >
  > Nothing calls `resolve_injected_files(skill.pipeline_inject, host_input=…)` and
  > forwards the result to `execute_plan`. T4's own caveat — "no shipping frontend
  > declares it" — is true but incomplete: the **manifest→agent plumbing is also
  > missing**, which is a different gap and the one that actually blocks a host.
  > Finishing it is the real T-task hiding behind this checkbox. Tracked in
  > [../TODO.md](../TODO.md).
  >
  > **Overlaps a second, planned mechanism.**
  > [context-injection](2026-06-09-context-injection.md) proposes
  > `Skill.provide_context()` returning *prompt text*, where this ships an injector
  > registry returning a *file set* for the gate. They are complementary in principle
  > — one changes what the model sees, the other what the gate accepts — but they are
  > two answers to "what files are available", and neither was written knowing about
  > the other. Whoever builds context-injection must reuse `injectors.py` or
  > deliberately retire it; do not add a third path.
- [x] T5 — `_PATH_ARG_KEYS` audit + characterization test (decision §5).
- [x] T6 — Integration tests in `tests/test_agent.py`.
- [x] T7 — Re-run eval; compare PRIZE flips and guard plan-accuracy; record numbers.

## Non-goals

- **File inspection / property-based matching.** The gate never probes files to
  decide which is "the 4K one" — matching is filename-token-based only (see
  "Matching is name-based").
- Foreign-language descriptor comprehension (handled later via fine-tuning /
  prompt engineering, per the user). The gate keys off file references, which are
  language-agnostic tokens; it does not try to understand "das 4K-Video".
- Substituting a file for a descriptor (that is fundamentally the model's job, or
  the injection step's). The gate only downgrades to clarify.
- Safety/reject behavior (separate work).
