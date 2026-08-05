# Website split — knaif.org (product) + knaif.dev (framework)

**Status:** Planning · **Created:** 2026-08-04 · **Completed:** —
**Owner:** site · **Ref:** `site/redesign`

> **Status note:** Revised 2026-08-05 after an audit found one repo-breaking defect (this
> header, which failed `test_plan_headers.py`) and six design gaps. Corrected since v1:
> the download data contract (was version-from-`Cargo.toml`, now a published-release
> snapshot), two factual errors in the extractor inputs, the developer quickstart path,
> and the Node/pnpm prerequisites. **All open design decisions were settled 2026-08-05
> and are recorded in §1** — nothing is blocked; this is ready to implement. Not started.
>
> **Launch is gated on operator review of both sites in full.** No incremental
> release: every page in §4 and every track in §5 is written and reviewed before either
> domain goes live.
>
> **Progress:** §11 steps 1–4 done 2026-08-05 — prerequisites, both new metadata sources
> (`display:` blocks, `contracts/release/platforms.yaml`), the extractor
> (`scripts/site_data.py` + a 21-test guard), and the pnpm/Astro/Starlight scaffolds with
> the §10 tokens wired into both sites. Both build green.
>
> **Both sites are written.** `.org` has `/skills`, `/skills/<name>`, `/vs`, `/about` and
> `/download` (7 pages); `.dev` has all five tracks and a real home page (24 pages). Every
> internal link and anchor resolves on both, `astro check` is clean, and `release.json` is
> live against v1.1.0.
>
> **Three things remain**, and none of them is page-writing:
> 1. The **asciinema cast** (§10a) — the only blocker on the `.org` home page, which is
>    still the scaffold placeholder.
> 2. The **two Amplify apps** (§7) — console work, then PR previews.
> 3. **Operator review** of both sites in full (§11 step 10), which is the launch gate.

**Goal:** Replace the single mkdocs page at `site/` with two Astro sites — knaif.org for
end users and knaif.dev for developers — sharing one design system and one generated
catalog, deployed from this repo by Amplify CI/CD.

---

## 1. Decisions

| Decision | Choice | Why |
|---|---|---|
| Audience split | `knaif.org` → end users, `knaif.dev` → developers | The current single page serves neither; it reads as a README doing a landing page's job |
| Engine | **Astro** for `.org`, **Starlight** for `.dev` | One toolchain, one design system across a sibling pair. Tens of skills in 6 months makes a data-driven catalog mandatory, which hand-written HTML would not survive |
| Package manager | **pnpm** | Operator preference. Note it uses `pnpm-workspace.yaml`, *not* `package.json` `workspaces` |
| mkdocs | **removed** | The existing site is one page and three assets — no investment to protect |
| `docs/*.md` | **not published** | Stays a repo-internal maintainer reference. `.dev` gets fresh content, links to GitHub for deep internals |
| Repo layout | both sites in this repo | Two Amplify apps over one repo; the extractor sits beside the skills it reads |
| Deploy | Amplify CI/CD, push-to-deploy | Replaces `just web-build` → zip → manual console upload |

**Why one engine won.** Two renderers means two design systems for two sites that must
look like one product, and two implementations of the skill-contract parsing.

### Settled 2026-08-05

| Question | Decision | Consequence |
|---|---|---|
| Catalog titles/taglines | **`display:` block in `skill.yaml`** | New optional field; skill-owned, native ignores it. Unblocks the extractor |
| System-requirements source | **New `contracts/release/platforms.yaml`** | Single source; RELEASE.md references it instead of restating the floors |
| SDK display name | **"knaif SDK"** | Nav says *knaif SDK*; code still shows `import knaif.cli as nk`. No API change |
| `release.json` refresh | **Manual now, automated when CI lands** | `just release-data` + a RELEASE.md §5 step; folds into `release.yml` later |
| Amplify config | **Committed root `amplify.yml`** | Both applications declared in-repo, reviewable in PRs |
| Thin `arg_schema`s | **Accept a thinner reference for now** | Generate what the schemas support; no change to a production skill |
| Hero visual | **Asciinema terminal cast** | New asset to record; adds a playback dependency to `.org` |
| `/vs` freshness | **Dated claim, no re-measure cadence** | Page must carry an explicit "not since re-verified" line |
| Launch scope | **Everything written and operator-reviewed first** | No incremental launch; §11 sequences to one cutover |
| Brand accent | **Coral `#ff5757` kept, indigo retired** | See §10 — needs a second darkened token for light-mode body text |
| Default theme | **`.org` light-first, `.dev` dark-first** | Both support both; only the default differs |
| Site pairing | **Same tokens, different density** | One design system, two pacings |
| Typefaces | **DM Sans + JetBrains Mono**, self-hosted via `@fontsource` | Two new OFL entries in `PROVENANCE.md` |
| Bracket motif | **Structural** — section eyebrows *and* the pipeline diagram | The motif argues the thesis rather than decorating |
| Catalog stage | **`stable` / `preview` / `hidden`, derived from the locked eval snapshot** | Preview skills are shown and badged; see §3 |

---

## 2. Prerequisites — this repo has never had a Node toolchain

None of these existed and all of them blocked the first `pnpm install`. **Done
2026-08-05** except the Amplify check, which needs the console.

- [x] **`.gitignore`** — added `node_modules/`, `.astro/`, `site/*/dist/`.
      `pnpm-lock.yaml` is *not* caught by the existing `*.lock` rule (that matches a
      `.lock` suffix only), so it commits with no exception needed — verified with
      `git check-ignore`. Astro's `dist/` was already covered by an unanchored `dist/`
      rule in the Python section; it is named explicitly too so removing that rule cannot
      silently start publishing build output.
- [x] **Pinned Node and pnpm in [`mise.toml`](../../mise.toml)** — `node = "24"` (LTS)
      and `pnpm = "10"`. The existing `node = "latest"` was scoped to "future Tauri work"
      and retargeted; a floating major is not reproducible against Amplify.
- [x] **Added `just site-install` / `site-dev` / `site-build` / `site-check`**, plus
      `site-data` and `release-data` for §3 and §6. They fail until the workspace scaffold
      lands (§11 step 4) and are **deliberately not wired into `just check`** until then.
- [ ] Confirm Amplify's build image provides pnpm, or enable it via `corepack` in
      `preBuild`. Do not assume.
- [ ] Wire `site-check` into `just check` once the scaffold exists.

Verified: full suite green (1629 passed, 7 skipped), `just --list` parses.

### The workspace (scaffolded 2026-08-05)

```text
site/
  pnpm-workspace.yaml   # pnpm reads THIS, not package.json "workspaces"
  package.json          # workspace root (knaif-sites)
  pnpm-lock.yaml        # committed — Amplify runs --frozen-lockfile
  data/site-data.json   # GENERATED — committed, drift-guarded (§3)
  shared/               # knaif-shared: tokens.css, fonts.css, brand assets
  org/                  # knaif-org  — Astro 7, light by default
  dev/                  # knaif-dev  — Starlight 0.41, dark by default
```

Packages are named `knaif-org` / `knaif-dev` / `knaif-shared`, not bare `org` / `dev` — a
package literally called `dev` reads as "development" in every filter and log line.

**Node pinned to 22, not 24.** The dev box runs 22.14 and Astro 7 requires ≥22.12, so 22 is
what can actually be verified today. An aspirational 24 pin would mean local and Amplify
build on different majors — precisely the drift the pin exists to prevent. Bumping is a
deliberate act once verified on both.

Two failure modes found by building rather than by writing files and assuming:

- **Starlight ≥0.39 removed `label` alongside `autogenerate`.** A sidebar group now needs
  an `items` array wrapping the autogenerate config. The old form is a hard build error.
- **Astro 5+ requires `src/content.config.ts`.** Without it Starlight finds no `docs`
  collection, builds **only `404.html`, and still exits 0** — a silent empty site that CI
  would happily deploy. Called out in the file itself, and the reason §9 asserts a page
  count rather than an exit code.

Verified end to end: `just site-build` green with `--frozen-lockfile` (org 1 page, dev 8
pages + sitemap + Pagefind index), `astro check` 0 errors on both, brand tokens
(`#ff5757`, `#d32f2f`) and both font families present in the built CSS, the catalog
rendered from `site-data.json`, and `git add -n` staging 30 files with **zero** build
artifacts.

---

## 3. The shared data extractor

The real risk at tens of skills is not the renderer — it is **parsing the skill contracts
twice and letting them drift**. Extraction happens once, in Python, reusing the runtime's
own loaders.

```text
skills/*/skill.yaml   ─┐
skills/*/tools.yaml   ─┤
skills/*/prompt.yaml  ─┼─→ scripts/site_data.py ──→ site/data/site-data.json
contracts/models/      │   (knaif.skill + knaif.registry)
  model-manifest.yaml ─┘
                                    │
                     ┌──────────────┴──────────────┐
                  site/org                      site/dev
```

**Committed, not build-time generated.** A contributor touching only the site can build
without a Python environment, and Amplify needs no Python step. A drift-guard test
regenerates and compares — the pattern `just sync-runtime` already establishes for
`contracts/runtime/core_tools.yaml`.

### Corrections to v1 of this plan

- **`models.yaml` was the wrong input.** It is a *runtime backend config* (paths,
  `n_ctx`, `n_gpu_layers`) and deliberately omits the released 1.7B. Public `url`,
  `sha256`, and `size_bytes` live in
  [`contracts/models/model-manifest.yaml`](../../contracts/models/model-manifest.yaml).
- **Example utterances already exist.** v1 claimed they were absent; they are curated in
  [`skills/ffmpeg/prompt.yaml`](../../skills/ffmpeg/prompt.yaml) and
  [`skills/documents/prompt.yaml`](../../skills/documents/prompt.yaml) under `examples:`.
  That is the source — already maintained, and already what the model sees. No new
  curation, and no sampling from the deliberately-messy `data/eval.jsonl`.
- **`load_registry()` is not enough on its own.** It parses `tools.yaml` and raises on
  duplicate keywords, but does not validate `skill.yaml` or prove a model-visible tool is
  backed by an `Intent` — that happens while loading the implementation in
  [`skill.py`](../../python/core/knaif/skill.py). The extractor must go through the skill
  loader, not the registry alone, or it can publish a tool the runtime would reject.

### The two new metadata sources (decided 2026-08-05)

**`display:` in `skill.yaml`** — optional, skill-owned, ignored by the native runtime:

```yaml
display:
  title: FFmpeg
  tagline: Video and audio, without the 15-flag incantation
  category: media
```

- [x] Added `display:` to `skills/ffmpeg` and `skills/documents`
- [x] Documented in [`docs/TOOL_SCHEMA.md`](../TOOL_SCHEMA.md) → *Display metadata*
- [x] **Missing `display:` fails the site build**, rather than falling back to
      `name`/`description`. A broken card in a public catalog is a worse failure than a
      failed build, and a silent fallback is how a new skill ships with placeholder copy
      nobody noticed. Optional to the *runtime*, required to *publish*. Enforced by the
      extractor (step 3).

Verified both runtimes ignore the new block: Python 1629 passed / 7 skipped,
`cargo test --workspace` green. Neither reads `display:`, so it cannot affect behavior.

**`contracts/release/platforms.yaml`** — the support matrix, currently prose in
[RELEASE.md §1](../RELEASE.md) (artifact names) and §4 (the measured `GLIBC_2.34` /
`GLIBCXX_3.4.30` floors, and the RHEL 9 caveat).

- [x] Wrote [`contracts/release/platforms.yaml`](../../contracts/release/platforms.yaml)
      + a `README.md` for the new contract directory; RELEASE.md §1 now **references** the
      matrix instead of restating it
- [x] Encoded the measured-floors precedent: the Linux constraint is stated as
      `GLIBCXX_3.4.30` / `CXXABI_1.3.13` (**libstdc++**, not glibc — the artifact needs
      only `GLIBC_2.34`, *below* the build base), with distros as the derived convenience
      and RHEL 9 listed as `known_bad` with its reason

Found while writing it: the site's status table still said *"CUDA is a manual opt-in
build"* — stale since `knaif backend install cuda` shipped. That is exactly the
three-copies-of-one-fact problem the contract removes, and the reason the file carries the
Blackwell correctness note rather than letting "Vulkan works everywhere" stand.

**Thin `arg_schema`s accepted.** ffmpeg's tools carry sparse schemas, so the generated
`.dev` reference is correspondingly thin. Generate what exists; do not touch a production
skill's contract for the website's benefit.

- [x] **`scripts/site_data.py`** + `just site-data` + the drift guard
      (`python/core/tests/test_site_data.py`, 15 tests). Done 2026-08-05.

The extractor publishes the **intersection** of the registry and the loaded `tool_map`,
not either alone. The registry carries the metadata; the loader proves a `Step`/`Intent`
class exists. A `tools.yaml` entry with no class — which the runtime would reject at plan
time — raises rather than reaching a page. (`tool_map` holds Step *instances*, so the
metadata has to come from `load_registry`; going through the loader is still what makes
the check possible.)

Emitted per skill: `display:` copy, developer `description`, recommended model, runtimes,
external tools, tools with args/`arg_schemas`/`safety_category`, and example utterances
from `prompt.yaml` — each tagged with the tool its plan routes to, so `.org` can show only
action-producing utterances while `.dev` groups by tool. Plus the model table (no
`url`/`sha256` — the site serves no downloads, and a copied hash is a second thing that
can rot) and the platform matrix.

Verified by breaking it, not just by passing:

| Failure injected | Result |
|---|---|
| Edited the committed JSON | Drift test fails, tells you to run `just site-data` |
| Removed a `display:` key | Exits 1 with the YAML to paste and a doc pointer |
| `status: stale` skill | Asserted absent from the catalog — via `status:`, not by name |
| Internal / core tool | Asserted never published as a skill capability |

Output is `sort_keys` + LF-only (`newline="\n"`), so the guard cannot fail merely because
Windows wrote the file. The error text is ASCII-only — a cp1252 console mangles em dashes
and arrows into noise exactly where the instructions are.

Gate: full suite **1646 passed / 5 skipped**, ruff + black + mypy clean. The drift guard
runs inside `just check` already, via `check-py → test-py`.

`io` needed no special case: `list_skills()` filters `status: stale` itself, so reusing the
runtime's own function gave the rule for free.

### Catalog stage — publishing an unfinished skill (decided 2026-08-05)

`status:` is binary (`active` / `stale`) and checked in exactly two places, both testing
`== "stale"`. **Everything else is `active`, and `active` is what you get by writing
nothing** — so a half-finished skill dropped into `skills/` would advertise itself on
knaif.org as production-ready, with nobody having made a wrong decision. `io` was never
the risk; the default was.

| Stage | Catalog | Derived when |
|---|---|---|
| `stable` | Full card | `data/eval_snapshot.json` exists |
| `preview` | Shown, badged *in development* | No snapshot yet |
| `hidden` | Not published | Only by explicit `display.stage: hidden` |

**Derived from the locked acceptance bar, not self-declared.** That is already this repo's
definition of done ([EVAL_FRAMEWORK.md](../EVAL_FRAMEWORK.md)), so advertising a skill and
locking its snapshot become the same act — neither can be forgotten independently.
`display.stage:` overrides when the derivation is wrong, and **cannot publish a
`status: stale` skill**: the site must never resurface what the runtime hides.

Showing preview skills rather than hiding them keeps the catalog as alive as the project
actually is while tens of skills are in flight, and the badge is what keeps that honest.

- [ ] `.org` needs a **preview badge** treatment in the design system (§10) — a skill card
      state, not a decoration
- [x] Implemented + 6 tests. Verified by injection: removing a snapshot flips `stable` →
      `preview`, `stage: hidden` drops the skill entirely, and an unknown stage exits 1
      listing the accepted values.

---

## 4. knaif.org — page inventory

End users. The model is ollama.com: the download is the point.

| Page | Contents |
|---|---|
| `/` | Hero + OS-detected download CTA; what it is in three lines; terminal demo; how it works in 3 steps; skills teaser grid; why-local; footer |
| `/download` | All platforms from `release.json`, link to the release's `SHA256SUMS`, per-OS install steps, system requirements, Vulkan/CUDA note, SmartScreen warning |
| `/skills` | **The catalog.** Grid with filter/search, from `site-data.json`. Cards carry a **preview badge** when `stage: preview` (§3) |
| `/skills/<name>` | Per skill: what it does, example utterances, required external tools. A preview skill says so above the fold, not in a footnote |
| `/vs` | **How knaif compares to the big LLMs** — see below |
| `/about` | The thesis and the sustainability argument — recovered from today's `index.md`, tightened, no longer competing with the CTA |

### `/vs` — the comparison page

Source: [`docs/experiments/2026-07-02-agent-vs-knaif-realworld.md`](../experiments/2026-07-02-agent-vs-knaif-realworld.md)
(358 lines — method, API-equivalent USD normalization, per-agent token detail, the safety
divergence, caveats, and a reproduce harness). Rewritten for the web, not lifted; `docs/`
is not published.

The page carries: the four-way headline table, the cost normalization (three agents meter
in three incompatible units), the **safety divergence** — two of three premium agents ran
a destructive delete under full permissions, and knaif's refusal is the only one enforced
in code — and a link to the reproduce harness.

Three rules this page must follow, because it makes competitive claims about named
commercial products in public:

- **Caveats travel with the numbers.** The experiment doc has a `## Caveats` section. A
  marketing page that drops it is not reporting the same result.
- **Every claim is dated and version-pinned.** "Measured 2026-07-02 against `opus-4-8`,
  `sonnet-5`, `gpt-5.5`." Those products change on a timescale of weeks; an undated claim
  becomes false without anyone editing it.
- **`## Reproduce` is linked prominently**, not buried. It is the strongest thing on the
  page — the claim is checkable.

**No re-measure cadence** (decided 2026-08-05). The page therefore **must** carry an
explicit *"measured 2026-07-02 against `opus-4-8` / `sonnet-5` / `gpt-5.5`; not since
re-verified"* line. That line is not a disclaimer to be quietly dropped in a later
copy-edit — it is the entire reason a fixed-date comparison stays honest, and without it
the page asserts a current result it does not have.

- [ ] Write the as-measured line into the page template, not just the copy

> **Latency copy must name hardware.** v1 of this plan wrote "~1s" as generic hero copy.
> [PERFORMANCE.md §6](../PERFORMANCE.md) measures a native CUDA `run` at **~5.2 s wall**,
> of which ~1.6 s is model compute, on `3070L-WSL`. The "~1.2 s" in today's `index.md` is
> an *intent-extraction* figure from the agent comparison, not wall clock. Project policy
> is that no latency number ships without its machine. Either qualify precisely or use no
> number.

- [x] Scaffold Astro + shared brand tokens
- [x] **Site chrome** — header (wordmark, nav, theme toggle), footer, skip link, base layout
- [x] **`/skills` + `/skills/<name>`** from `site-data.json`, 2026-08-05
- [x] **`/vs`** — the comparison, from the frozen 2026-07-02 experiment
- [x] **`/about`** — thesis, the three problems, sustainability
- [x] **`/download`** — from `release.json` + the platform contract, with UA detection as
      progressive enhancement (every platform stays listed with JS off)
- [ ] `/` — the real home page *(needs the asciinema cast, §10a)*

**Internal link check passes**: 7 pages, 0 broken. Worth keeping as a gate — `/download`
was linked from all six other pages before it existed, so the site was already incoherent
without it.

Two things the catalog work surfaced:

**The wordmark SVG could not be used as an `<img>`.** `media/knaif-logo-rect.svg` carries
its own `@media (prefers-color-scheme: dark)` block, which answers to the OS and cannot
see our `data-theme`. On a light-OS machine with the site toggled to dark, its letterforms
stayed near-black and the mark vanished into the header. It is now an inline Astro
component (`site/shared/Wordmark.astro`) using `currentColor` for the letterforms and the
fixed coral for `[AI]`, so it tracks the tokens in both themes and both toggle directions.

**A uniform safety column is noise.** All 13 ffmpeg tools are `destructive` (knaif's sense:
requires confirmation or dry-run), so an "asks first" column read *yes* thirteen times — a
third of the table width spent saying nothing, on the one axis where the product is
strongest. The table now adapts: uniform skills state it once in prose, where it reads as
the reassurance it is; mixed skills (documents: 12 destructive, 3 read-only) keep the
column. Both branches verified in the built HTML.

---

## 5. knaif.dev — page inventory

All fresh writing; `docs/` is not lifted. `.dev` serves **two different developers**, and
the top-level nav should say so rather than blending them:

- someone **extending knaif** with a new skill (tracks A–D below), and
- someone **putting a natural-language front end on their own CLI** (track E) — who may
  never write a knaif skill at all.

### Track A — Author a skill

| Page | Contents | Source |
|---|---|---|
| The skill bundle | Anatomy of `skills/<name>/`, what each file is for | `TOOL_SCHEMA.md` |
| `Step` and `Intent` | The tool contract, `HandlerContext`, when to use an `Intent` | `TOOL_SCHEMA.md`, `ARCHITECTURE.md` |
| Intent expansion | One model-visible intent → deterministic multi-step plan, `$variables` | `VARIABLE_BINDING.md` |
| Safety model | `safety_category`, sandbox, dry-run, confirm gates | `REQUIREMENTS.md` |
| Reference | `skill.yaml` + `tools.yaml` schema; per-skill tool reference from `site-data.json` | generated |

### Track B — Evaluate a skill

The part that makes a skill *finished* rather than merely written, and the biggest gap in
any public knaif documentation today.

| Page | Contents | Source |
|---|---|---|
| Why skills need evals | A skill is not done when the handlers work | `EVAL_FRAMEWORK.md` |
| Writing `eval.jsonl` | Corpus envelope, row schema, `success_criteria`, fixtures | `EVAL_FRAMEWORK.md` §Corpus envelope, `CORPUS_AUTHORING_STEPS.md` |
| Baselines | The pre-recorded freeform-LLM command each row carries, and what it is for | `EVAL_FRAMEWORK.md` |
| **The eval ladder** | `cheap` → `output_diff` → `success` as **phases, not alternatives**; why `cheap` is an iteration instrument and never an acceptance bar | `EVAL_FRAMEWORK.md` §The eval ladder |
| Snapshots + regression | Locking the acceptance bar, the regression gate, re-locking as a deliberate separate commit | `EVAL_FRAMEWORK.md` |
| Verifying honestly | Fixtures first — missing fixtures score correct plans ~0 | `EVAL_VERIFICATION_SOP.md` |

### Track C — Fine-tuning

| Page | Contents | Source |
|---|---|---|
| When it is worth it | A measured lever, not a ritual; the negative results that were kept | `FINE_TUNING.md` |
| Writing `train.jsonl` | Dataset generation, what good rows look like | `TRAINING_DATA_GENERATION.md` |
| The union dataset | One model serves every skill; every *other* skill's snapshot is the regression gate | `FINE_TUNING.md` |
| Train → merge → quantize → promote | The loop, and the `models.yaml` / manifest promotion step | `FINE_TUNING.md`, `MODELS.md` |

### Track D — Python → native

| Page | Contents | Source |
|---|---|---|
| Why a dual runtime | Python authors/evals/trains; Rust ships. Same YAML contracts both sides | `NATIVE.md` §1 |
| **A port, not a rewrite** | Same prompt, same validation, same expansion — the rule that governs the whole track | `NATIVE.md` |
| Writing the Rust crate | `knaif-skill-api`, the `Step`/`Intent` equivalents, workspace membership | `NATIVE.md` §7 |
| Declaring runtimes | The `runtimes:` block in `skill.yaml` | `TOOL_SCHEMA.md` |
| Parity | `just parity <skill>`, what it pins and diffs, `contracts/parity/` | `NATIVE.md` |

### Track E — the knaif SDK

A **separate product for a separate audience**: put natural language on your own CLI.
Decorator front door, `nk.App`, `nk.from_click` to wrap an existing click group.
Source: [`docs/SDK.md`](../SDK.md).

**Display name: "knaif SDK"** (decided 2026-08-05). Nav, headings, and prose say *knaif
SDK*; code samples still show `import knaif.cli as nk`, which is unchanged. No API break,
and no rename of the `knaif-cli` console script — the ambiguity is resolved by how the
site *speaks*, not by moving code.

> **This track solves the quickstart problem.** Skill bundles are deliberately excluded
> from the wheel ([`pyproject.toml`](../../python/core/pyproject.toml) `include =
> ["knaif*"]`), so `list_skills()` is empty on a bare install and "`pip install knaif`
> then run a skill" does not work. The **SDK does ship in the wheel** (`uv add knaif`, per
> SDK.md), and `knaif.examples.clock` is packaged. So track E is the only path that works
> from PyPI alone — it should be the site's quickstart, with tracks A–D framed as "clone
> the repo."

- [ ] Add a **naming note** early in track E, since three things still read as "knaif cli"
      in the wild: the native binary `knaif` (end user, `.org`), the Python console script
      `knaif-cli` (`knaif.app:main`, runs skills), and the SDK module `knaif.cli`. A reader
      arriving from a search result needs one paragraph telling them which they have.

- [x] Scaffold Starlight
- [x] **Track E + the quickstart**, 2026-08-05 — `/start/quickstart`, `/sdk`,
      `/sdk/reference`, `/sdk/from-click`, `/sdk/inference`, `/sdk/boundaries`.
      12 pages, 0 broken links, 0 type errors.
- [x] **Track A**, 2026-08-05 — `/author`, `/author/steps-and-intents`,
      `/author/registry`, `/author/safety`, `/author/publishing`. 16 pages total,
      0 broken links, 0 type errors.
- [x] **Track B**, 2026-08-05 — `/evaluate`, `/evaluate/corpus`, `/evaluate/ladder`,
      `/evaluate/snapshots`. 19 pages total, 0 broken links (cross-page anchors verified),
      0 type errors.
- [x] **Track C**, 2026-08-05 — `/fine-tuning` + `data`, `methodology`, `outcomes`,
      `promotion`.
- [x] **Track D**, 2026-08-05 — `/native` + `parity`.
- [x] **`.dev` home page** rewritten from the scaffold. **All five tracks written; no
      scaffold banners remain on `.dev`.** 24 pages, every internal link *and anchor*
      resolves, 0 type errors.

Track C is the most unusual content on either site, and the reason to publish it is that
almost nobody writes this down: the **negative results**. Weighted SFT, tiny eval-derived
DPO, bulk synthetic distillation, single-skill scope, and planner-diversity-via-a-third-skill
are all recorded as *run and failed*, with the numbers. The planner-diversity entry is
flagged explicitly as "the refuted theory is persuasive and keeps re-suggesting itself",
because that is exactly how it gets re-run.

Two methodology rules carried over verbatim in spirit, both of which read as genuinely
hard-won:

- **"A slice you selected on can no longer measure what you selected."** Best-of-N
  inflation on a 55-row slice, and re-running does not fix it. Published *with* the honest
  admission that the promoted model's hard-slice margin has never had an independent probe.
- **The snapshot gate answers "may I promote?", not "did I regress?"** — an experimental
  build under a committed snapshot can be a lineage gap, and reading that as catastrophic
  forgetting is a mistake already made here once.

Track D states what "a port, not a rewrite" *forbids* rather than only what it means, and
separates the two parity layers: golden fixtures on inline registries (so a case cannot
start passing because someone edited ffmpeg) versus the live model-pinned diff. It also
names the trap that parity proves the runtimes *agree*, not that either is right — two
runtimes can agree perfectly on the wrong command.

Track B was the largest gap and is now the site's densest material. Four things it makes
public for the first time, each carrying the concrete number that makes it stick:

- **`cheap` is never an acceptance bar**, with the false-regression that proves it: 11
  ffmpeg chain rows gained validated `outputs`, `verifier_kind` flipped `plan → output`,
  and the cheap aggregate fell **0.973 → 0.928 with no behaviour change**. "A bar that
  moves when you annotate the corpus is not a bar."
- **Fixtures first, or an executing run silently scores near-zero** — documents at outcome
  ≈0.55 with a knaif score of 1.000, because 58 of 129 rows errored on a missing fixtures
  directory. Framed as "check fixtures before the model, the prompt, or the corpus".
- **The utterance-equivalence contract**, as a `danger` callout: bundling utterances
  asserts they are true paraphrases, and breaking it manufactures permanent false
  negatives that read as a model problem.
- **Command-shaped vs plan-shaped skills**, including that the failure is *silent* —
  destructive rows are understated and nothing in the scoreboard says why.

Also stated: a 100% pass rate does not mean 100% correct (verifier scores only run on
`plan` rows — read `outcome_accuracy`), and runs that failed their gate stay in the index,
because they are what lets the project decline a plausible change with evidence.

Track A states three things plainly that were previously only implicit in `docs/`:

- **`expand` is pure and the plan is frozen before anything runs** — so runtime branching
  belongs in a Step's output, not in an expander. That is the constraint that makes plans
  previewable and identical every time, and it is the mistake a new author will make.
- **Honouring `ctx.dry_run` is the handler's job, and core cannot enforce it.** A handler
  that ignores it makes `--dry-run` a lie — which matters because dry-run is one of only
  two ways a destructive tool may run at all.
- **The `exit 0 ≠ goal achieved` gap**, carried across from `ARCHITECTURE.md` as a
  `danger` callout rather than buried. It is also the honest motivation for the eval
  ladder's executing verifiers: they check the artifact, which the runtime currently
  cannot.

Publishing is documented as a *consequence of evaluating*, not a separate chore — the
stage derives from the locked snapshot, so "how do I get on the catalog" resolves to "lock
your acceptance bar", which is the behaviour worth encouraging anyway.

**"Dark by default" was not actually implemented until it was checked.** Starlight's
`ThemeProvider` falls back to the OS —
`matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'` — so every
visitor with a light-mode laptop got a light `.dev`, while the built HTML *looked* correct
because its SSR default is `dark`. A `head:` script does not fix it: Starlight injects its
provider **after** custom head tags, so upstream always wins. Fixed by overriding the
`ThemeProvider` component with a one-line diff (`storedTheme || "dark"`), keeping
`updatePickers` verbatim so a Starlight upgrade stays a readable diff. A stored preference
still wins, so the picker works in both directions.

Two pieces of track E content worth keeping prominent, because both are afternoon-savers:
the **`from_click` callback-validation gap** (click callbacks do *not* run on the plan
path, so anything load-bearing must move into the function body), and the
**reasoning-model hang** — `InferenceOrchestrator`'s raw defaults (`json_mode=True`, 256
tokens) hang then time out on Qwen3/DeepSeek-R1, because `think: false` stops Ollama
*separating* the reasoning rather than stopping it, so it lands in `message.content` and
destroys the JSON.

---

## 6. Downloads — a published-release contract

**Never derive download URLs from `Cargo.toml`.** The version bump merges to `main` before
the release is published ([RELEASE.md §5](../RELEASE.md), steps 2→5), so a version-derived
URL advertises assets that do not exist yet. All releases are on GitHub; the site tracks
the **latest published release**, not the version the repo declares.

```text
site/data/release.json     # committed: tag, published_at, asset name→URL, SHA256SUMS URL
```

- Direct OS buttons link to GitHub release assets, e.g.
  `…/releases/download/v1.1.0/knaif-1.1.0-windows-x64-setup.exe`
- `/download` links to that release's `SHA256SUMS` asset. **Checksum values are not
  copied into the site** — they exist only in the published asset, and duplicating them
  creates a second truth that can rot.
- Missing or stale metadata → buttons fall back to the Releases page.

**Refresh: manual now, automated when CI lands** (decided 2026-08-05). The file is
refreshed from the GitHub Releases API *after* publishing. This repo has **no CI**
(`.github/` holds only issue and PR templates; RELEASE.md is explicit that releases gate
on local green), so today it is a `just release-data` recipe plus a step in RELEASE.md §5
— matching how every other release task already works. When
[post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md) ships `release.yml`,
fold it in there rather than adding this repo's first workflow from a website plan.

A forgotten manual step produces exactly the stale-button failure this contract exists to
prevent, so the step goes in RELEASE.md, not only in this plan. Until it is automated,
`/download` returning 200 on every asset (§9) is the check that catches a missed refresh.

- [x] **Implemented 2026-08-05** — `scripts/release_data.py`, `just release-data`,
      `site/data/release.json`, and **RELEASE.md §5 step 7**, plus a 12-test offline guard
      (`test_release_data.py`).

Snapshot verified against the live v1.1.0 release: all four assets plus `SHA256SUMS`
resolved, and every URL returns **200** (checked with HEAD requests).

The guard is deliberately **offline**. Asserting 200s belongs in the §9 acceptance gates
against a built site, not in the unit suite — a test that reaches GitHub on every run
fails on a plane and gates releases on someone else's uptime. What it does check without
network: the tag matches every asset URL, the contract's `<ver>` templates still match the
published names, `ASSET_MATCHERS` covers every artifact the contract declares, and drafts,
prereleases, missing `SHA256SUMS` and renamed assets all raise rather than emitting a page
with no buttons.

- [ ] Add a note to [post-v1-ci-and-cuda-opt-in](2026-07-17-post-v1-ci-and-cuda-opt-in.md)
      so `release.yml` picks this up rather than rediscovering it

---

## 7. Amplify

Two apps, one repo, each with its own domain and app root; build from `main`, PR previews
per site.

**Config lives in a committed root `amplify.yml`** (decided 2026-08-05) declaring both
applications — reviewable in PRs and versioned with the code.

- [ ] Declare for each app: exact `appRoot`, `buildPath`, artifact `baseDirectory`
      (relative to `buildPath`), and a matching `AMPLIFY_MONOREPO_APP_ROOT` env var —
      AWS requires `appRoot` and that variable to agree.
- [ ] Build command is `pnpm install --frozen-lockfile` + `pnpm --filter <app> build`.
- [ ] Create both apps, point domains, configure apex/www redirects and cross-domain nav.

---

## 8. Migration fallout

- [ ] **Move `execution-pipeline.svg` before deleting `site/docs/`** — that is its
      *only* location in the repo, and it is the diagram `ARCHITECTURE.md` refers to.
- [ ] **Update [PROVENANCE.md](../PROVENANCE.md)** — its "Site and documentation assets"
      table names `site/docs/assets/` paths that will not exist.
- [ ] Remove `web-build` / `_web-build-mkdocs` / `_web-zip` from the justfile. **Note:**
      "drop mkdocs-material from dependencies" is a no-op — it is never declared, only
      `uv pip install`ed inside the recipe. Deleting the recipe is the whole removal.
- [ ] Update the PyPI `Documentation` URL in `pyproject.toml`, README website links,
      sitemaps/canonicals for both domains.
- [ ] Add this plan to [plans/README.md](README.md) and to TODO.md *Open / Next*.

---

## 9. Acceptance gates

- [ ] `just check` green (includes `test_plan_headers.py` — the gate v1 of this plan failed)
- [ ] Both production builds succeed from a clean checkout with `--frozen-lockfile`
- [ ] **Assert a minimum page count, not just exit 0.** A Starlight build with no content
      collection emits only `404.html` and exits 0 (§2) — an exit code alone would deploy
      an empty site.
- [ ] Generated-data drift check green
- [ ] Internal + external link check on both sites
- [ ] Every download URL in `release.json` returns 200
- [ ] Responsive + a11y smoke pass (contrast, keyboard nav, reduced motion)
- [ ] PR-preview deploys verified on both apps before DNS cutover
- [ ] **Operator sign-off on both sites in full** — the launch gate (§11 step 10)
- [ ] Post-cutover: apex/www redirects, cross-domain nav, documented rollback

---

## 10. Design system

Settled 2026-08-05. Specimen (palette, contrast proof, type scale, density comparison):
<https://claude.ai/code/artifact/bcf7b8a7-0b5d-46b4-b834-f757a6a0f0c6>

**The mark already encodes the thesis.** The wordmark reads `kn[AI]f` — the AI bracketed.
That is not decoration: *the AI is contained* is exactly what the product argues, so the
bracket becomes the system's structural device rather than a logo quirk.

### Tokens

| Token | Light | Dark | Notes |
|---|---|---|---|
| Coral — display | `#ff5757` | `#ff5757` | Logo, large type, borders, buttons |
| Coral — text | `#d32f2f` | `#ff7a7a` | Links and small text. **Not optional** — see below |
| Ink | `#1f232b` | `#e8eaed` | |
| Ground | `#ffffff` | `#14171c` | |
| Surface | `#f7f8fa` | `#1b1f26` | |
| Border | `#dce0e7` | `#333a45` | |
| Muted | `#656d7b` | `#98a1b0` | |

**Coral needs two tokens because one cannot do both jobs.** `#ff5757` on white measures
**3.11:1** — under the 4.5:1 WCAG AA requires for body text. It clears 3:1 for large type
and UI borders, and reaches **5.06:1** on the dark ink. Choosing light-first for `.org`
therefore makes `#d32f2f` (4.98:1) mandatory for links and small text there. `.dev` being
dark-first lands on the easier side and can use the brand coral directly.

**Neutrals are cool, biased toward the ink** (`#1f232b` is measurably blue), not toward the
coral. Warm-harmonising everything would flatten the accent; the warm/cool tension is what
keeps it working.

**Indigo is retired.** [`site/mkdocs.yml`](../../site/mkdocs.yml) sets `accent: indigo`,
which appears in no brand asset — a theme default, never a brand decision.

### Type

**DM Sans + JetBrains Mono**, both from the Google Fonts catalog but shipped via
**`@fontsource`** (npm, self-hosted) — *not* the Google CDN. Self-hosting is faster (no
third-party DNS + TLS before text renders) and avoids the GDPR exposure of transmitting
visitor IPs to Google, which a Munich court ruled unlawful without consent in 2022.

Mono is load-bearing, not ornamental — this is a CLI product, so rendered argv, terminal
casts, tool names, eyebrows and labels all live in it. That is what ties the bracket motif
into the type system.

- [x] Added via `@fontsource-variable/dm-sans` + `@fontsource-variable/jetbrains-mono`
      (`site/shared/fonts.css`). **Verified self-hosted**: 7 `.woff2` in the build output
      and **zero** references to `fonts.googleapis`/`fonts.gstatic` in either site.
- [ ] Add two OFL entries to [PROVENANCE.md](../PROVENANCE.md)

### Bracket motif — structural

- Section eyebrows: `[ HOW IT WORKS ]`
- **The pipeline diagram brackets the model step**, fencing the one non-deterministic
  stage between deterministic code. This is the motif doing explanatory work, and it is
  the reason "structural" was chosen over "eyebrows only".
- Not on buttons, tags, or callouts — that was the maximal option, rejected as fatiguing
  across a docs site.

### Logo assets

- [ ] **Fix `media/logo.png`.** Its letterforms are white, so on a light background it
      renders as a floating `[AI]`. It currently serves as *both* logo and favicon, so the
      favicon is invisible in a light browser tab today.
- [ ] Use the SVG (it self-adapts via `prefers-color-scheme`); generate real favicons from
      the square `[AI]` mark.

---

## 10a. Content dependencies — not engineering

**Hero: an asciinema cast** (decided 2026-08-05) of
`knaif run ffmpeg "compress video.mp4 under 25 mb"` — the worked example already in
`index.md`. It shows the plan, the confirm gate, and the rendered argv, which is the
product's actual differentiator rather than a claim about it.

- [ ] Record it against a **real run**, not a hand-typed reconstruction
- [ ] Self-host the player — a strict-CSP-friendly, no-external-CDN embed. It is the only
      moving asset on the site
- [ ] Provide a static poster frame and respect `prefers-reduced-motion`
- [ ] Any timing visible in the cast is a latency claim and inherits the §4 hardware rule

**`io` stays off the catalog** — it is `status: stale` and already hidden from discovery;
the site must not resurface what the runtime hides. The extractor should filter on
`status`, not on a hardcoded name.

Brand direction is settled — see §10.

---

## 11. Sequence

1. **Prerequisites** (§2) — Node/pnpm, `.gitignore`, `just site-*`. Nothing else can start
2. **New metadata**: `display:` blocks + `contracts/release/platforms.yaml` (§3)
3. **Extractor** + `just site-data` + drift guard (§3)
4. **Design system** (§10) as shared tokens + `@fontsource` fonts, then the Astro and
   Starlight scaffolds consuming them
5. `.org` pages: `/`, `/download`, `/skills`, `/vs`, `/about` (§4)
6. `.dev` tracks, in order: E (quickstart) → A → B → C → D (§5)
7. `release.json` + `just release-data` + RELEASE.md step (§6)
8. Asciinema cast recorded and embedded (§10)
9. Amplify apps + committed `amplify.yml`, migration fallout (§7, §8)
10. **Operator review of both sites in full** on PR previews
11. DNS cutover

**Launch is one event, gated on operator review** (decided 2026-08-05). Every page in §4
and every track in §5 is written and reviewed before either domain goes live — no
incremental publishing. Tracks B–D are the largest writing project here and are on the
critical path; step 6 dominates the schedule and should be planned as writing time, not
as implementation time.

Amplify PR previews (step 10) are what make a single cutover reviewable — both sites are
fully inspectable on preview URLs before any DNS record changes.

v1 of this plan claimed steps 1–2 were safe to land before the content questions settled.
That was wrong: titles and platforms have no source today (§3), so the extractor's output
schema is not determined until those are decided.
