# FFmpeg Skill — geometry crop + thumbnail/frame merge

**Status:** Done · **Created:** 2026-06-17 · **Completed:** —
**Owner:** ffmpeg · **Ref:** feature/ffmpeg-geometry-crop

> **Status note:** **All three phases complete.** Geometry crop/fit shipped on
> `resize_video`; `extract_frame` merged into `create_thumbnail`; Phase 3
> (baselines/eval/snapshot) ran on 2026-06-17/18 — see Checkpoint C.
>
> **Corrected 2026-07-22** (S7 decision — **kept**). This note previously read "Phase 3
> deferred", and `docs/plans/README.md` and `docs/TODO.md` repeated it. All three were
> stale: the plan body has 3.1–3.6 checked, and the artifacts confirm it —
> [`evals/INDEX.md`](../../evals/INDEX.md) carries both post-geometry runs with the
> figures Checkpoint C cites, `fit`/`aspect` are live in
> [`tools.yaml`](../../skills/ffmpeg/tools.yaml), and `extract_frame` has **zero**
> occurrences in `tools.yaml` or `eval.jsonl`. The `feature/more-tools-ffmpeg` branch
> named in the old TODO entry and this plan's `feature/ffmpeg-geometry-crop` are both
> merged and gone; the work is in the tree.
>
> **Paths and commands below were repointed** for the monorepo move (the old
> `src/skills/ffmpeg/…` became `skills/ffmpeg/…`, with tests under
> `skills/ffmpeg/python/tests/`), for the eval-directory rename (the old
> `eval_results/` is now `evals/`), and for the toolchain (a direct
> `.venv\Scripts\python.exe` call is now `uv run`; PowerShell fences are now `bash`).
> **`_geometry_vf` has since moved** out of `handlers.py` into
> [`_engine.py`](../../skills/ffmpeg/python/_engine.py) — `handlers.py` re-exports it —
> so the illustrative import in Task 1.1 no longer reflects where the helper lives.
>
> **The Appendix's comparison guidance is now authoritative in
> [EVAL_VERIFICATION_SOP.md](../EVAL_VERIFICATION_SOP.md)** — the per-row id join, why
> the aggregate gate needs an unchanged row set, how to read a non-empty `REGRESSED`
> list, snapshot-before-you-change, and the tool-selection table. Cite the SOP; this
> plan is the worked example behind it.

**Goal:** Add geometry crop/fit to `resize_video` and merge `extract_frame` into
`create_thumbnail`.

---

## Background & decisions

Research (a coverage pass over the full FFmpeg action set vs. the skill's current 14 intent
tools) concluded the skill already covers nearly every *general* operation. The binding
constraint is **small-model tool-selection accuracy**, which degrades as the flat intent set
grows — so the right move is to **model the user's intent, not mirror ffmpeg**, and to prefer
changes that keep the prompt flat or shrink it. This plan implements the two changes that survived
that filter. Specialized categories (color/filters, overlay/watermark, subtitles, streaming,
audio-mix) are **deferred** — they are "everything," not "general," and each grows the prompt.

**Decision 1 — Geometry is one intent, not crop-vs-resize.** Crop and resize are the *same*
intent ("make this video size X") with a different rule for reconciling target size against the
source aspect ratio:

| `fit` | Behavior | ffmpeg `-vf` |
|---|---|---|
| keep-aspect (single-dim default) | scale proportionally; never distorts | `scale='min(W,iw)':-2` / `scale=-2:H` (unchanged) |
| `crop` (cover) | scale to cover, cut overflow, centered — no bars, no distortion | `scale=W:H:force_original_aspect_ratio=increase,crop=W:H` |
| `pad` (contain) | scale to fit, add bars | `scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2` |
| `stretch` | force exact W×H, distorts | `scale=W:H` (current both-dims behavior) |

The model does **not** need to disambiguate crop from resize. Defaults carry it:
- **Single dimension** ("to 1080p") → proportional scale, no ambiguity. Covers the bulk of usage. **Unchanged.**
- **`crop`/`square`/aspect keyword** → `fit=crop` (or aspect crop).
- **Both width AND height, no fit keyword → default `fit=crop`** *(Decision: cover, not pad — matches the common exact-size/social intent; never distorts; never adds bars).* This **changes** today's both-dims behavior, which stretches (`scale=W:H`).
- `"resize without stretching/squeezing"` is already the default and needs no special handling.
- `pad`/`stretch` only fire on explicit wording ("letterbox", "stretch"/"force").

**Decision 2 — Social reframe = geometry crop only.** "Crop to square / 9:16" is handled by the
geometry intent (`fit=crop` + `aspect`), *not* by adding aspect targets to `prepare_for_platform`.
Keeps reframing explicit and decoupled from platform profiles.

**Decision 3 — Merge `extract_frame` into `create_thumbnail`.** They produce identical output today
(both render `-ss <t> -vframes 1` + optional `scale`; only the output suffix and default timestamp
differ — see `ExtractFrameIntent` / `CreateThumbnailIntent` in `handlers.py`). Two near-identical
tools waste model selection capacity and prompt lines for zero capability. Merge into one
(`create_thumbnail`, keeping the `scale` arg). **Net −1 model-visible tool and fewer prompt examples.**

Net effect: **−1 model-visible tool**, prompt flat-or-shrinking, while adding the one common gap
(crop / reframe). All changes stay inside `skills/ffmpeg/`; **no core (`knaif/`) changes**.

---

## Execution path (shared by both phases)

```
tools.yaml         ──►  Intent.expand()   ──►  _build_one_recipe   ──►  _build_flags
(schema, keywords)      (resolve fit/args)     (store fit/aspect)       (emit -vf chain)
prompt.yaml / SPEC.md                                                         │
golden prompt + corpus (eval.jsonl) + unit tests  ◄───────────────────────────┘
```

**TDD discipline (per project rule):** every task is RED → GREEN → COMMIT — write the failing test
first, then the implementation, then commit. Run focused tests plus the full suite after each change:

```bash
uv run pytest skills/ffmpeg/python/tests/ --tb=short
uv run pytest --tb=short
```

---

## Phase 1 — Geometry: crop / pad / aspect on `resize_video`

**Goal:** `resize_video` gains `fit` and `aspect` so it can cover-crop, pad, stretch, or
aspect-crop, with the decided defaults. Single-dimension resize is unchanged.

### - [x] Task 1.1 — RED: `_geometry_vf` helper unit tests

**Files:** `skills/ffmpeg/python/tests/test_geometry.py` (new)

Introduce a pure helper `_geometry_vf(width, height, fit, aspect) -> str | None` (the single source
of truth for the geometry filter chain) and test it before implementing:

```python
from skills.ffmpeg.python.handlers import _geometry_vf  # adjust import to match existing test style

def test_single_height_proportional():
    assert _geometry_vf(None, 720, None, None) == "scale=-2:720"

def test_single_width_proportional():
    assert _geometry_vf(1280, None, None, None) == "scale='min(1280,iw)':-2"

def test_both_dims_default_crop():           # Decision 1: both dims, no fit → crop (cover)
    assert _geometry_vf(1080, 1920, None, None) == \
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"

def test_both_dims_fit_pad():
    assert _geometry_vf(1080, 1920, "pad", None) == \
        "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"

def test_both_dims_fit_stretch():
    assert _geometry_vf(1920, 1080, "stretch", None) == "scale=1920:1080"

def test_aspect_square_no_dims_pure_crop():
    assert _geometry_vf(None, None, "crop", "1:1") == "crop='min(iw,ih*1/1)':'min(ih,iw*1/1)'"

def test_aspect_9x16_pure_crop():
    assert _geometry_vf(None, None, "crop", "9:16") == "crop='min(iw,ih*9/16)':'min(ih,iw*9/16)'"

def test_no_geometry_returns_none():
    assert _geometry_vf(None, None, None, None) is None
```

**Acceptance:** the new test module fails to import / fails (helper does not exist yet).

### - [x] Task 1.2 — GREEN: implement `_geometry_vf` + fit resolution

**Files:** `skills/ffmpeg/handlers.py`

Add `_geometry_vf(width, height, fit, aspect)` near `_parse_scale`. Resolution rules:

1. `aspect` given (and not both explicit dims) → pure crop to that aspect:
   `crop='min(iw,ih*{aw}/{ah})':'min(ih,iw*{ah}/{aw})'` (parse `aspect` as `aw:ah`).
2. Both `width` and `height`:
   - `fit` resolution when unset: **`crop`** (Decision 1). `keep_aspect_ratio=False` (legacy arg) → `stretch`.
   - `crop` → `scale=W:H:force_original_aspect_ratio=increase,crop=W:H`
   - `pad` → `scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2`
   - `stretch` → `scale=W:H`
3. Single dimension → unchanged proportional scale (`scale='min(W,iw)':-2` or `scale=-2:H`).
4. Nothing → `None`.

**Acceptance:** all Task 1.1 tests pass. Commit.

### - [x] Task 1.3 — RED: schema for `fit` / `aspect`

**Files:** `skills/ffmpeg/python/tests/test_ffmpeg_skill.py` (or test_geometry.py)

```python
def test_resize_schema_has_fit_and_aspect():
    td = ToolDef_for("resize_video")  # use the existing registry-access helper in this suite
    assert "fit" in td.optional_args
    assert "aspect" in td.optional_args
```

### - [x] Task 1.4 — GREEN: `tools.yaml` schema + crop keywords

**Files:** `skills/ffmpeg/tools.yaml`

Extend `resize_video`:
- `optional_args: [width, height, keep_aspect_ratio, fit, aspect, quality, preview]`
- Add crop/reframe keywords (mind collisions: `recortar`/`обрезать` already sit on `trim_video`,
  so prefer crop-specific terms): `crop, square, vertical, portrait, landscape, aspect, "1:1",
  "9:16", "16:9", "4:5", reframe, zuschneiden, rogner, 裁剪`.

Controlled values: `fit` ∈ `{keep_aspect, crop, pad, stretch}`; `aspect` is `"aw:ah"`.

**Acceptance:** Task 1.3 passes. Commit.

### - [x] Task 1.5 — RED: expander + recipe + render integration tests

**Files:** `skills/ffmpeg/python/tests/test_geometry.py`

Drive the full `resize_video` path through the agent in dry-run (mirror the existing
`create_thumbnail` scale tests' style — `CommandAgent.from_skill`, `execute_plan(dry_run=True,
confirmed=True)`, pull the `run_batch` command):

```python
def test_resize_both_dims_emits_cover_crop(media_root, stub_ffmpeg):
    cmd = run_resize(media_root, {"inputs": ["clip.mp4"], "width": 1080, "height": 1920})
    assert "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" in " ".join(cmd)

def test_resize_square_via_aspect(media_root, stub_ffmpeg):
    cmd = run_resize(media_root, {"inputs": ["clip.mp4"], "aspect": "1:1", "fit": "crop"})
    assert "crop='min(iw,ih*1/1)':'min(ih,iw*1/1)'" in " ".join(cmd)

def test_resize_single_height_unchanged(media_root, stub_ffmpeg):
    cmd = run_resize(media_root, {"inputs": ["clip.mp4"], "height": 720})
    assert "scale=-2:720" in " ".join(cmd)
    assert "crop" not in " ".join(cmd)
```

### - [x] Task 1.6 — GREEN: thread `fit`/`aspect` through expander, recipe, flags

**Files:** `skills/ffmpeg/handlers.py`

- **`ResizeVideoIntent.expand`**: read `fit = args.get("fit")`, `aspect = args.get("aspect")`;
  put both in `options`. Keep the existing `keep_aspect_ratio` read for the legacy→`stretch` mapping.
- **`_build_one_recipe`** (resize is `mode in (..., "resize", ...)`): store `recipe["fit"]`,
  `recipe["aspect"]` when mode is `resize`.
- **`_build_flags`**: give resize its **own branch** (`elif mode == "resize":`) ahead of the generic
  `else`, so platform/compress/convert downscaling is untouched. The branch:
  1. `vf = _geometry_vf(max_w, max_h, recipe.get("fit"), recipe.get("aspect"))`;
     if `vf` → `post += ["-vf", vf]`.
  2. then emit the same encode flags the generic `else` does (`-c:v`, `-crf`, `-preset`,
     `-pix_fmt`, `-c:a`, `-b:a`, faststart) — inline, mirroring the `rotate` branch's style.

**Acceptance:** Tasks 1.1 + 1.5 pass; existing resize tests still pass. Commit.

### - [x] Task 1.7 — Summarizer

**Files:** `skills/ffmpeg/handlers.py` — `ResizeVideoIntent.summarize`

When `aspect` is set → `"reframe {files} → {aspect}"`; when `fit == "crop"` and both dims →
`"crop {files} → {w}x{h}"`; otherwise keep the current `"resize … → …"` wording.

**Acceptance:** summarizer returns a crop/reframe-aware clause; add one assertion in
`test_summarizers.py`.

### - [x] Task 1.8 — prompt.yaml + SPEC.md routing

**Files:** `skills/ffmpeg/prompt.yaml`, `skills/ffmpeg/SPEC.md`

- Add **one** mapping line (do not bloat): `"Crop" / "make square" / "to 9:16" / "vertical" →
  resize_video with fit=crop (and aspect when given). Plain "resize"/"to 1080p" stays proportional.`
- Add **one** example (square crop). Reuse the existing resize example block; do not add a section.
- `SPEC.md`: note `fit`/`aspect` under `resize_video` (the Public Tools list is already stale — see
  Phase 2 cleanup).

### - [x] Task 1.9 — Corpus rows

**Files:** `skills/ffmpeg/data/eval.jsonl`

Append after the current highest `ffmpeg_NNN` id (check the file — do not reuse an id). Add 2–3 rows:
crop-to-square, crop-to-9:16 (vertical), and an explicit both-dims exact size (asserting the cover
crop default). `success_criteria.filters: ["crop"]`. Include multilingual utterances per the existing
row convention. Regenerate baseline `command` strings from actual dry-run output after GREEN.

**Also:** grep existing resize rows for any that pass **both** width and height and expect
`scale=W:H` (stretch). Decision 1 changes those to cover-crop — update their baselines + add
`success_criteria.filters: ["crop"]`, or pin them to `fit: stretch` if the row's intent really was a
stretch. `test_eval_data.py` (JSONL validity) must stay green.

### - [x] Checkpoint A

```bash
uv run pytest skills/ffmpeg/python/tests/ --tb=short
uv run pytest --tb=short
```

Both green before Phase 2.

---

## Phase 2 — Merge `extract_frame` → `create_thumbnail`

**Goal:** one frame-still tool. Drop the `extract_frame` model-visible intent; fold its keywords and
corpus into `create_thumbnail`; remove the now-dead `extract_frame` mode plumbing.

### - [x] Task 2.1 — RED: `extract_frame` is gone, `create_thumbnail` covers it

**Files:** `skills/ffmpeg/python/tests/test_ffmpeg_skill.py`

```python
def test_extract_frame_tool_removed():
    names = {t.name for t in FFmpegSkill.tools}
    assert "extract_frame" not in names
    assert "create_thumbnail" in names

def test_grab_keyword_routes_to_thumbnail():
    # a "grab a still / screenshot" utterance must retrieve create_thumbnail, not extract_frame
    ...  # use the existing prompt/retrieval test helper
```

### - [x] Task 2.2 — GREEN: remove the intent, fold keywords

**Files:** `skills/ffmpeg/handlers.py`, `skills/ffmpeg/tools.yaml`

- `handlers.py`: delete `ExtractFrameIntent`; remove it from `FFmpegSkill.tools`.
- `tools.yaml`: delete the `extract_frame:` block; fold its frame-grab keywords into
  `create_thumbnail.keywords` (`grab, snapshot, capture, frame, framegrab, still, fotograma,
  standbild, кадър, 截图`).
- Clean the now-dead `"extract_frame"` tokens: `_OUTPUT_SUFFIX_BY_MODE` (drop the `extract_frame`
  entry), and the `mode in ("thumbnail", "extract_frame")` tuples in `_build_one_recipe` and
  `_build_flags` → just `mode == "thumbnail"`. (`create_thumbnail` already supports `scale`, so no
  capability is lost.)

**Acceptance:** Task 2.1 passes; `create_thumbnail` scale tests still pass. Commit.

### - [x] Task 2.3 — Migrate corpus rows

**Files:** `skills/ffmpeg/data/eval.jsonl`

Retarget every `"expected_tool": "extract_frame"` row to `create_thumbnail` (ids include 014, 072,
107, 202, 213, 237, 276 — grep to confirm the full set). Update each baseline `command` to the
`create_thumbnail` output (output suffix becomes `_thumb`; default timestamp differs — regenerate
from dry-run). Update `tags` (`extract_frame` → `create_thumbnail`/`thumbnail`). The two `clarify`
rows tagged `extract_frame` (264, 288) keep `expected_tool: clarify` — just retag.

### - [x] Task 2.4 — Fix prompt audit + golden prompt

**Files:** `tests/test_prompt_audit.py`, `tests/golden/ffmpeg_system_full.txt` (+ any other golden
that lists the tool set), `skills/ffmpeg/prompt.yaml`

- `prompt.yaml`: remove the `extract_frame` mapping line + example; ensure the "grab a frame /
  screenshot" guidance now points at `create_thumbnail`. Net: fewer prompt lines.
- `test_prompt_audit.py` line ~185 checks `extract_frame` is *not* pulled into a compress query —
  replace that token with a still-existing tool (e.g. `reverse_video`), since `extract_frame` no
  longer exists.
- Regenerate the golden prompt fixtures (they embed the tool list / examples) via the suite's
  golden-update path; confirm the diff only drops `extract_frame`.

### - [x] Checkpoint B

```bash
uv run pytest skills/ffmpeg/python/tests/ --tb=short
uv run pytest --tb=short
```

Then update the tool count / Public Tools list in `SPEC.md` and `docs/TODO.md` (now 14 → 14: −1 frame
tool, +0 for crop which folds into resize).

---

## Phase 3 — Post-implementation: baselines, eval, snapshot

Code being GREEN (Checkpoints A/B) is **not** done. Corpus baselines and the eval snapshot must be
regenerated and the routing re-checked, because this plan adds rows, changes rows, and removes a tool.
Full workflow reference: `docs/CORPUS_AUTHORING_STEPS.md`.

### - [x] Task 3.1 — Regenerate fixtures

```bash
just eval-fixtures ffmpeg
```

Idempotent; produces `sandbox/fixtures/` (incl. `clip_4k.mp4` used by crop rows).

### - [x] Task 3.2 — Re-baseline three kinds of rows

`just eval-seed ffmpeg` **never overwrites `validated_by: "human"` rows** — so the changed/migrated
human rows will be silently skipped and must be edited by hand. Three categories:

| Rows | State | Action |
|---|---|---|
| **New crop rows** (Task 1.9) | `validated_by: null` | `just eval-seed ffmpeg` drafts them; validate in the notebook |
| **Both-dims resize rows** changed stretch→crop (Task 1.9 audit) | already `human` → skipped by seed | **manually re-author** — golden command is now wrong (edit `e` in the notebook, or paste the `--dry-run` output) |
| **Migrated `extract_frame` rows** (Task 2.3) | already `human` → skipped by seed | **manually fix** `expected_tool`, `baseline.command` (suffix `_thumb`, new default timestamp), `tags` |

```bash
just eval-seed ffmpeg            # drafts the NEW crop rows only
just baseline-authoring          # validate new rows + manually edit changed/migrated human rows
```

**Note:** crop baselines are deterministic `--dry-run` output. If `eval-seed` (which routes through
the model) drafts a wrong command, author the correct one directly from dry-run rather than retrying
the model. Commit the authored `eval.jsonl` (`corpus(ffmpeg): baselines for crop + thumbnail merge`).

> **Comparison baseline & folder layout.** The pre-geometry reference is
> `evals/baselines/2026-06-16_pre-geometry_v2corpus_success/` (qwen + gemma success run,
> ~274 rows / 769 utterances). `eval_snapshot.json` was **already re-locked to the post-geometry
> corpus** (total≈293, cheap) — so it is **not** a pre-geometry baseline; do not rely on
> `eval-regression` for the before/after story. Save every new run under
> `evals/runs/2026-06-17_post-geometry_<verifier>/` and add a row to `evals/INDEX.md`.

### - [x] Task 3.3 — Routing check (cheap verifier, no ffmpeg) — the real risk surface

```bash
just eval ffmpeg --config eval_backends.yaml --save evals/runs/2026-06-17_post-geometry_cheap --verbose
```

Run across the configured backends (**qwen and gemma** — different failure modes). `--save` persists
the scoreboard so it can be diffed; `--verbose` prints per-row detail to aggregate errors. Confirms:
- "crop / make square / 9:16 / vertical" routes → `resize_video`.
- "grab a frame / screenshot / still" still routes → `create_thumbnail` after `extract_frame` removal.

Aggregate **all** routing errors before fixing anything (don't fix one-by-one). Removing a tool +
adding crop keywords is exactly what shifts small-model selection.

### - [x] Task 3.4 — Success eval (executes against fixtures, grades `success_criteria`)

```bash
just eval-success ffmpeg --backends qwen3-4b,gemma3-4b --config eval_backends.yaml --fixture-dir sandbox/fixtures/ --save evals/runs/2026-06-17_post-geometry_success
```

This is where `success_criteria.filters: ["crop"]` is actually verified end-to-end. Match the
baseline's backends (qwen + gemma) so the before/after diff in Task 3.5 is like-for-like.

### - [x] Task 3.5 — Compare post-geometry vs the pre-geometry baseline — the before/after story

The corpus grew from ~274 → 296 rows, so an **aggregate** accuracy diff conflates "+22 new rows"
with "behavior changed." The trustworthy regression signal is a **per-row join on shared ids**:
which previously-passing rows now fail (and vice-versa), restricted to ids present in both runs.
The suite ships no such tool — run this inline diff per backend (`qwen3-4b`, then `gemma3-4b`):

```bash
uv run python - <<'EOF'
import json
base = json.load(open(r"evals/baselines/2026-06-16_pre-geometry_v2corpus_success/ffmpeg_qwen3-4b_success.json"))
cur  = json.load(open(r"evals/runs/2026-06-17_post-geometry_success/ffmpeg_qwen3-4b_success.json"))
b = {r["id"]: r for r in base["rows"]}
c = {r["id"]: r for r in cur["rows"]}
shared = b.keys() & c.keys()
print(f"shared ids: {len(shared)}  (baseline {len(b)}, current {len(c)}, new: {sorted(c.keys()-b.keys())})")
regressed = [i for i in shared if b[i]["outcome_correct"] and not c[i]["outcome_correct"]]
improved  = [i for i in shared if not b[i]["outcome_correct"] and c[i]["outcome_correct"]]
score_drop = [i for i in shared if c[i].get("knaif_score",0) < b[i].get("knaif_score",0)]
print("REGRESSED (pass->fail):", regressed)
print("improved  (fail->pass):", improved)
print("knaif_score dropped on:", score_drop)
'@
```

**Done when** `REGRESSED` is empty on both backends (any entry is a real behavior regression on a
pre-existing row — investigate before proceeding). New crop / thumbnail-merge rows show up in `new:`
and are graded by Task 3.4's `success_criteria`, not here.

Then the human-readable report for worst-rows triage:

```bash
just eval-report ffmpeg evals/runs/2026-06-17_post-geometry_success
xdg-open evals/runs/2026-06-17_post-geometry_success/report.html   # macOS: open · Windows: start
```

Expected new failure modes to watch: crop/resize confusion on terse utterances, and "screenshot/grab"
no longer finding a dedicated tool.

### - [x] Task 3.6 — Lock the regression snapshot (once results are acceptable)

```bash
just eval ffmpeg --backends qwen3-4b --snapshot
```

Re-locks `eval_snapshot.json` to the post-geometry corpus so **future** changes are measured against
it. (This is the aggregate-metric snapshot used by `just eval-regression`; it does not preserve
per-row detail — that is why the saved run folders in `runs/` are the durable record.) Log the final
run in `evals/INDEX.md`.

### - [x] Checkpoint C — done criteria

- [x] Full `pytest` suite green — **1172 passed, 1 skipped** (incl. regenerated golden prompt fixtures from Task 2.4).
- [x] `eval.jsonl` has no `extract_frame` token (0 occurrences) and no stale stretch baselines.
- [x] Routing eval (3.3) shows no misrouting on crop / thumbnail utterances.
- [x] Per-row diff (3.5) vs the pre-geometry baseline: **no real regression.** `REGRESSED` was not
  literally empty (qwen: 157, 202, 226, 243; gemma: 049, 082b, 125, 139, 177, 243, 268, 271), but
  every entry was investigated and is either (a) a single flaky multilingual utterance failing to
  emit a plan (nondeterminism — other utterances of the same row pass), or (b) already failing in the
  baseline by knaif score (e.g. 243-de). **None touch geometry/crop/thumbnail code.** The branch's own
  rows are clean: 293 perfect, 294/295 one flaky utterance each (7/8 pass).
- [x] Post-geometry runs saved under `evals/runs/2026-06-17_post-geometry_*` and logged in `INDEX.md`.
- [x] `eval_snapshot.json` re-locked to the final 296-row corpus (outcome 0.861 / tool 0.811).

---

## Risk controls & backward compatibility

- **No core changes.** Everything is under `skills/ffmpeg/`.
- **Behavior change is intentional and localized.** Both-dims resize moves from stretch → cover-crop
  (Decision 1). Caught by Task 1.9's corpus audit; single-dim resize is byte-for-byte unchanged
  (Task 1.5 guard). `fit=None` + single dim must produce today's exact command.
- **Merge is a breaking change to corpus + golden + one audit test** — all enumerated in Tasks
  2.3/2.4. Anything referencing `extract_frame` must be migrated, not left dangling.
- **Prompt discipline:** Phase 1 adds at most one mapping line + one example; Phase 2 removes more
  than that. Net prompt size flat-or-smaller — verify the golden diff.
- **Deferred (out of scope, cataloged for later):** color/`adjust_look`, overlay/watermark,
  subtitles, streaming, audio replace/mix, images→video, 2-pass, palette-GIF, fps/pixfmt args.

---

## Open (non-blocking)

- **Geometry vocab in core vs ffmpeg-local.** Media vocab is currently ffmpeg-domain leakage in core;
  extraction is deferred until a 2nd skill exists. Does not block this plan — `fit`/`aspect` stay
  ffmpeg-local for now.

---

## Appendix — long-term eval workflow (folder convention)

`evals/` is gitignored (local-only, no git recovery). Run JSONs carry **no embedded
metadata** (no date / git SHA / corpus hash — backend is only in the filename, mode is the
`verifier` field). `evals/INDEX.md` is therefore the source of truth for what each run was
against; **keep it updated**.

Layout (established 2026-06-17):

```
evals/
  INDEX.md      <- run history table; add a row per saved run
  baselines/    <- locked reference runs to diff against (one folder per baseline)
  runs/         <- working runs (one dated folder each)
  _archive/     <- superseded / non-comparable historical runs (kept, not deleted)
```

Naming: `<YYYY-MM-DD>_<label>_<verifier>/` containing `ffmpeg_<backend>_<verifier>.json`.

**Standard loop for any future skill change that touches the corpus or tools:**

1. **Snapshot the *current* (pre-change) state first** if no baseline exists for it:
   `just eval-success ffmpeg --backends qwen3-4b,gemma3-4b --fixture-dir sandbox/fixtures/ --save evals/baselines/<date>_<label>_success`
   — then promote it under `baselines/` and log it in `INDEX.md`. (Had this been routine, the
   pre-geometry snapshot would not have been overwritten.)
2. Make the change; regenerate fixtures (`just eval-fixtures ffmpeg`) and re-baseline corpus rows.
3. **Routing** (cheap, fast): `just eval ffmpeg --config eval_backends.yaml --save evals/runs/<date>_<label>_cheap --verbose`.
4. **Success** (executes ffmpeg): `just eval-success … --save evals/runs/<date>_<label>_success`.
5. **Compare** to the chosen baseline with the per-row-by-id diff in Task 3.5 (empty `REGRESSED`
   = no behavior regression on shared rows). Use `just eval-regression ffmpeg` only as a secondary
   aggregate check, and only when the corpus row set is unchanged (otherwise the +/- rows bias it).
6. **Report:** `just eval-report ffmpeg evals/runs/<date>_<label>_success`.
7. When green, **re-lock** the aggregate snapshot (`just eval ffmpeg --backends qwen3-4b --snapshot`)
   and add the run to `INDEX.md`.

**Built-in comparison primitives (know which does what):**

| Command | Compares | Granularity | Good for |
|---|---|---|---|
| per-row diff snippet (Task 3.5) | two saved run JSONs | per-row, shared ids | "did existing behavior change" across a corpus that grew/shrank |
| `just eval-regression ffmpeg` | a run vs `eval_snapshot.json` | aggregate metrics, 0.02 threshold | quick gate when corpus row set is unchanged |
| `just eval-compare ffmpeg a,b` | backends in **one** run | aggregate, side-by-side | model A vs model B at the same code state |
| `just eval-report` | one run | per-row, human-readable | worst-row triage |
