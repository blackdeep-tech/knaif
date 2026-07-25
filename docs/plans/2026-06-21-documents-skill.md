# Documents Skill (general local document toolkit)

**Status:** Done · **Created:** 2026-06-21 · **Completed:** —
**Owner:** documents · **Ref:** [documents-productionization](2026-06-22-documents-productionization.md)

> **Status note:** Implemented — knaif's **second real skill** (31/31 build
> checkboxes, green gate; see the productionization plan). It unblocks the
> monorepo/native-runtime plan
> ([2026-06-17-monorepo-dual-runtime.md](2026-06-17-monorepo-dual-runtime.md)) by
> exercising the skill contract before it is frozen across two languages. (This
> header previously read "Planning — ready to implement"; that was stale.)
>
> **Kept 2026-07-22** (S7 decision). Two things moved out:
> - The **corpus-search design** in *Out of scope* became its own plan,
>   [document-corpus-search](2026-07-22-document-corpus-search.md) — it was a real design
>   (north-star query, keyword-first sequencing, cache strategy, open questions) surviving
>   only as a bullet list inside a completed plan.
> - The **mixed scanned/native PDF OCR limitation** below is now in
>   [SPEC.md](../../skills/documents/SPEC.md) (*Known Limitations*), where a user will
>   actually find it — it fails silently, so it reads as "OCR is broken".
>
> Paths were repointed from the pre-monorepo `src/skills/` layout, and four bare
> memory-key references were replaced with plain language or a doc link.

**Goal:** Build knaif's second skill — a general local document toolkit (extract,
inspect, manipulate, convert, compress, OCR). Local vector/similarity search is
explicitly out of scope and gets its own later plan.

**Scope boundary:** this plan covers the *document toolkit* only — extract,
inspect, manipulate, convert, compress, and (Phase 1.5) OCR. **Local
vector/similarity search is explicitly OUT of this plan.** The vector store is a
cross-cutting capability (knaif core + every skill, including a replacement for
the broken whitespace `retrieve_tools`) and gets its own plan after a dedicated
research pass. This skill's `extract_text` is deliberately designed to be the
ingest primitive that the future search plan reuses.

### What this skill does and does not stabilize

The documents skill is **file-centric, like ffmpeg**, so it validates most of the
contract — `Step`/`Intent`, multi-step expansion, `arg_value_sets`, `profiles/`
reuse, destructive/readonly split, safety gates, shared `knaif.steps` — but it
does **not** force the `input_refs` media-vocab extraction
. That extraction is driven by *non-file*
query inputs and therefore belongs to the future vector/search plan.

## Why this skill (the "next ffmpeg")

Same winning shape as ffmpeg: a ubiquitous, painful, privacy-sensitive local task
behind arcane CLIs. People routinely upload confidential documents (contracts,
tax, medical, IDs) to free online "merge/compress/convert PDF" sites — a real,
widespread privacy problem. "Do it locally, offline, in plain English" is a
genuine differentiator. General documents (not PDF-only) because end users also
hold `.docx`, `.pptx`, `.xlsx`, `.txt`, `.md`, and images.

## Popular document tasks (what users get)

Demand-ranked from the catalogs of the market leaders (iLovePDF's ~30 tools,
Smallpdf, Adobe Acrobat online) — those catalogs are pure demand signal, since they
only build what people search for. knaif is natural-language-driven, so each task is
written as an utterance a user would actually type, and mapped to the tool that serves
it.

### Tier 1 — the "big four" (the reason these sites have ~1B users)

| Task | Example utterance | Tool | Covered |
|---|---|---|---|
| Merge / combine | "combine these three PDFs into one" | `merge_pdfs` | ✅ |
| Split / extract pages | "pull pages 5–10 out of report.pdf" | `split_pdf` | ✅ |
| Compress | "make this PDF small enough to email" | `compress_pdf` (Intent) | ✅ best-effort (no guaranteed target size — see "Compression semantics") |
| Convert | "turn these scans into one PDF" / "convert report.pdf to Word" | `convert_document` | ✅ (Office→PDF gated on LibreOffice; **PDF→Office is out of scope**) |

### Tier 2 — frequent organize & secure

| Task | Example utterance | Tool | Covered |
|---|---|---|---|
| Rotate pages | "rotate the sideways pages in scan.pdf" | `rotate_pages` | ✅ |
| Remove / delete pages | "delete the blank last page" | `remove_pages` | ✅ |
| Reorder / organize pages | "move page 1 to the end" | `reorder_pages` | ✅ (added) |
| Extract text | "get the text out of this contract" | `extract_text` | ✅ |
| Inspect | "how many pages / is it encrypted / does it have a text layer" | `inspect_document` | ✅ (no online equivalent; underpins everything) |
| Password-protect | "password-protect this contract" | `protect_pdf` | ✅ |
| Unlock / remove password | "remove the password from statement.pdf" | `unlock_pdf` | ✅ |
| Watermark | "stamp DRAFT across every page" | `watermark` | ✅ (added) |
| Add page numbers | "number the pages bottom-center" | `add_page_numbers` | ✅ (added) |

### Tier 3 — specialized but common

| Task | Example utterance | Tool | Covered |
|---|---|---|---|
| OCR | "make this scanned PDF searchable" | `ocr_document` | ✅ (Phase 1.5) |
| Crop margins | "crop the white margins off every page" | — | Deferred (low demand; easy later) |
| Repair | "this PDF won't open, try to recover it" | — | Deferred (qpdf recovery; niche) |
| Redact | "black out the SSNs in this PDF" | — | **Deferred — needs its own design** (true redaction removes the underlying bytes, not just a black box; doing it wrong leaks the data) |
| PDF/A archival | "convert to PDF/A for archiving" | — | Deferred (robust path wants Ghostscript/veraPDF) |
| Fill & sign forms | "fill in this form and sign it" | — | Out of scope |
| Compare | "what changed between v1 and v2" | — | Out of scope |

### Tier 4 — AI/cloud-era (deliberately out of scope)

Summarize, Translate, Chat-with-PDF, request e-signatures. These are LLM/cloud
features, not deterministic local execution — the wrong shape for this skill.
Summarize/translate may become a *future* skill once the vector/LLM layer lands.

## Dependency & licensing policy (gating constraint)

Per `project_dependency_license_policy`: every bundled dependency must be
**Apache-2.0-compatible, no paid tier, small, fully local**, with a Rust path for
the eventual native port. The deciding rule for copyleft tools:

- **Subprocess (separate binary) = mere aggregation** → knaif stays Apache. OK.
- **Linked in-process (FFI/static/dynamic lib) = combined work** → relicenses
  knaif. **Never do this** (specifically: never use the Rust `mupdf` crate or
  link `libgs`).

**Default path is fully permissive and bundleable. AGPL/GPL tools are optional,
runtime-detected, never-bundled subprocess upgrades — the ffmpeg model.**

| Capability | Default (permissive, in-process lib) | License | Optional upgrade (detected, subprocess) | Rust later |
|---|---|---|---|---|
| PDF structural ops (merge/split/rotate/remove/encrypt/optimize) | **pikepdf** (wraps qpdf) | MPL-2.0 / Apache-2.0 | — | `lopdf` |
| PDF text extraction | **pypdf** + **pdfminer.six** | BSD-3 / MIT | poppler `pdftotext` (GPL, subprocess only) for hard layouts | `pdf-extract` |
| PDF render / page→image | **pypdfium2** (PDFium) | BSD-3 + Apache-2.0 | — | `pdfium-render` |
| Office text (docx/pptx/xlsx) | **python-docx / python-pptx / openpyxl** | MIT | — | `docx-rs`/`calamine` |
| Images / images→pdf | **Pillow** (reimplement img2pdf's tiny logic to avoid its LGPL) | MIT-CMU | — | `image` crate |
| Text overlays (watermark, page numbers) | **reportlab** (generate overlay PDF) + **pypdf**/pikepdf stamp-merge | BSD-3 / BSD-3 | — | `printpdf` |
| Lossy PDF compression | best-effort (PDFium rasterize + Pillow re-encode) | permissive | **Ghostscript** `gs` (AGPL — detected, not bundled) for superior downsampling | shell out to `gs` |
| High-fidelity Office→PDF | not in default | — | **LibreOffice** `soffice` (MPL, too big to bundle — detected only) | shell out |
| OCR (Phase 1.5) | **not in default bundle** — `tesseract` binary is detected on PATH like `gs`/`soffice` | Apache-2.0 (engine) | **Tesseract** (detected) + Python wrapper `pytesseract` in a separate `documents-ocr` extra; PDF pages are rasterized with **pypdfium2 (not Ghostscript)** | FFI (`leptess`/`ocrs`) |

The permissive Python libraries ship as an optional extra
(`pip install knaif[documents]`). OCR ships as a **separate** extra
(`knaif[documents-ocr]` → `pytesseract`) so the base install stays small.
External binaries
(`tesseract`, `gs`, `soffice`) are **detected at runtime exactly like `ffmpeg`** —
declared as skill dependencies, **never bundled**, with a clear "install X for this
feature" message when absent. **OCR is therefore an optional detected capability, not
part of the default bundle.**

### Bundle sizes (why the defaults are bundleable and the upgrades are not)

The full permissive default stack — qpdf/pikepdf (~3 MB), pypdf+pdfminer (~1 MB),
PDFium prebuilt (~6–10 MB), python-docx/pptx/openpyxl (<1 MB), Pillow (~3 MB),
reportlab (~3 MB) — totals roughly **15–20 MB**. The installer stays **100%
Apache-compatible and AGPL-free**, and every Tier-1/Tier-2 task works with zero
external installs. **OCR is not counted here** — the Tesseract engine (~30 MB +
`tessdata_fast` ~2–4 MB/lang) is a *detected external binary*, installed by the user,
exactly like `gs`/`soffice`. The detected-only upgrades are excluded on size *or*
license:
Ghostscript (~30–60 MB, **AGPL**), poppler `pdftotext` (~15 MB, **GPL**), and
LibreOffice (**300–700 MB** installed — ~10× the entire rest of the bundle).

### Supporting Word/Office documents — and why LibreOffice stays optional

"Support Word docs" is three distinct operations, and **only one needs LibreOffice**:

| What the user wants | Needs LibreOffice? | How |
|---|---|---|
| Read / extract text from docx/pptx/xlsx | **No** | `python-docx` / `python-pptx` / `openpyxl` (MIT, tiny, local) — serves `extract_text` + `inspect_document` |
| Use Office files as inputs to other ops | **No** | same permissive libs read the content |
| **Faithful Office→PDF** (preserve layout, fonts, images, tables) | **Yes** — no good small alternative | a real layout/rendering engine |

The common Word tasks (read/extract/inspect) never touch LibreOffice. LibreOffice is
*only* for faithful Office→PDF, where the alternatives are all worse:
**Pandoc+LaTeX** (GPL, ~150 MB + a LaTeX engine, poorer Word fidelity);
**docx2pdf** (just shells out to Word/LibreOffice, not standalone);
**MS Word COM automation** (gold standard but Windows/Mac-only, not bundleable);
**roll-our-own** (reimplementing a word-processor layout engine — infeasible);
**low-fidelity** (read text via python-docx, re-emit a plain PDF — loses all formatting,
risks a mangled-looking output).

**Decision:** bundle nothing for Office→PDF. Keep it **detected-only** — if `soffice`
is on PATH, do the faithful conversion; otherwise return *"install LibreOffice for
Office→PDF, or use extract_text to get the content."* Do **not** ship the low-fidelity
fallback in Phase 1; a clear "not available" beats a silently-broken PDF.

## Skill surface

Skill name: **`documents`**. Directory: `skills/documents/`.

### Model-visible tools (Phase 1)

| Tool | Kind | safety | Backend | Notes |
|---|---|---|---|---|
| `inspect_document` | Step | safe (readonly) | pikepdf / pypdf / office libs | pages, format, size, encrypted?, has_text_layer? |
| `extract_text` | Step | safe (readonly) | pypdf/pdfminer + office libs | returns **per-page** text (`pages: [{page, text}]`) plus a joined `text` — per-page is required so `find_in_document` can cite page numbers; **the future-search ingest primitive**; OCR fallback added in 1.5 |
| `find_in_document` | Step | safe (readonly) | reuses `extract_text` | keyword/regex search **within one document**; returns matching pages + snippets; case-insensitive/regex flags. Distinct from corpus search (deferred). |
| `merge_pdfs` | Step | destructive | pikepdf | |
| `split_pdf` | Step | destructive | pikepdf | by page range(s) |
| `rotate_pages` | Step | destructive | pikepdf | |
| `remove_pages` | Step | destructive | pikepdf | |
| `reorder_pages` | Step | destructive | pikepdf | move/reorder pages (e.g. "page 1 to the end"); reuses range parsing |
| `watermark` | Step | destructive | reportlab overlay + pypdf stamp | text/image stamp over every page |
| `add_page_numbers` | Step | destructive | reportlab overlay + pypdf stamp | position arg (top/bottom, left/center/right) |
| `protect_pdf` | Step | destructive | pikepdf | set password |
| `unlock_pdf` | Step | destructive | pikepdf | remove password (requires the password) |
| `convert_document` | Step | destructive | Pillow / pypdfium2 / office libs | images→pdf, →txt, →md; office→pdf only if LibreOffice detected |
| `compress_pdf` | **Intent** | destructive | pikepdf (lossless) → `gs` if detected | expands `inspect_document` → `run_compress` (named internal leaf) → `verify_output`; exercises the Intent path + `profiles/` |

### Internal tools

All of these are `internal: true` leaf steps declared in `tools.yaml` (the
`compress_pdf` Intent emits them, and every emitted tool **must** be declared — see
[`docs/TOOL_SCHEMA.md`](../TOOL_SCHEMA.md) "Intents", line 237).

- `knaif.steps.ResolveInputs` (shared — exercises the shared-step path)
- `run_compress` (internal leaf; performs the actual compression — the step the
  `compress_pdf` Intent expands to. Lossless qpdf/pikepdf by default; routes to the
  `gs` subprocess preset when `compress_quality` demands lossy and `gs` is detected;
  PDFium-rasterize + Pillow lossy fallback otherwise. See "Compression semantics".)
- `verify_output` (internal; checks output exists, page count preserved, and reports
  achieved size / size-delta after a write) — used by the `compress_pdf` Intent.

### Tool contracts (Phase 1)

Compact summary of args + result shape per tool. The authoritative `required_args` /
`optional_args` / `arg_schemas` / defaults live in `tools.yaml` at implementation
(per [`docs/TOOL_SCHEMA.md`](../TOOL_SCHEMA.md)); this table fixes the *contracts* that
shape design — page-range parsing, the `extract_text`→`find_in_document` coupling, and
password handling — so they aren't decided ad hoc.

| Tool | Required args | Optional args | Result shape |
|---|---|---|---|
| `inspect_document` | `input` (path) | — | `{format, pages, size_bytes, encrypted, has_text_layer}` |
| `extract_text` | `input` | `pages` (range), `ocr` (1.5) | `{pages: [{page, text}], text}` |
| `find_in_document` | `input`, `query` | `regex` (bool), `ignore_case` (bool, default true) | `{matches: [{page, snippet, span}], count}` |
| `merge_pdfs` | `inputs` (paths), `output` | — | `{output, pages}` |
| `split_pdf` | `input`, `ranges` (e.g. `"1-3,7,9-12"`) | `output` (single named file; single range only), `output_dir` | `{outputs: [path], count}` |
| `rotate_pages` | `input`, `degrees` | `pages` (range; default all), `output` | `{output, rotated_pages}` |
| `remove_pages` | `input`, `pages` (range) | `output` | `{output, pages}` |
| `reorder_pages` | `input`, `order` (page sequence) | `output` | `{output, pages}` |
| `watermark` | `input`, `text` **or** `image` | `opacity`, `position`, `output` | `{output}` |
| `add_page_numbers` | `input` | `position` (default bottom-center), `start_at`, `output` | `{output}` |
| `protect_pdf` | `input`, `password` | `output` | `{output}` |
| `unlock_pdf` | `input`, `password` | `output` | `{output}` |
| `convert_document` | `input`(s), `to_format` | `output` | `{output}` (office→pdf errors clearly if `soffice` absent) |
| `compress_pdf` (Intent) | `input` | `compress_quality` (`small`/`balanced`/`high`), `output` | `{output, original_size, new_size, percent, method, text_preserved}` |

Page-range parsing (`"14-40"`, multi-range `"1-3,7,9-12"`) is **one shared helper**
reused by `split_pdf`/`rotate_pages`/`remove_pages`/`reorder_pages`/`extract_text`.

### Phase 1.5 additions

| Tool | Kind | Notes |
|---|---|---|
| `ocr_document` | **Intent** | inspect → rasterize if no text layer → Tesseract → rebuild searchable PDF (`pytesseract` + pypdfium2 + pypdf, not Ghostscript) |
| `extract_text` | (updated) | gains OCR fallback for image/scanned PDFs and image files |

> **Python-version gate resolution:** skip `ocrmypdf` entirely and build the
> searchable-PDF rebuild with `pytesseract` + `pypdfium2` + `pypdf`. This keeps OCR
> on the same Python baseline as the base skill and avoids installing an unused
> Ghostscript-adjacent package.

> **Known Phase 1.5 limitation:** `inspect_document.has_text_layer` is currently
> whole-document, not per-page. A mixed PDF with one text page and several scanned
> pages is treated as already searchable by `ocr_document`, so it is copied rather
> than partially OCR'd. Per-page OCR planning is deferred.

### `profiles/` and `arg_value_sets`

Mirrors ffmpeg's profile pattern (validates that `profiles/` generalizes beyond
media):

```yaml
# skill.yaml
arg_value_sets:
  compress_quality: [small, balanced, high]      # → profiles/compress/*.yaml
  to_format: [pdf, txt, md, png, jpg]
safety:
  unsafe_phrases: ["delete all documents", "wipe all files", "rm -rf", "nuke", "format drive"]
```

`profiles/compress/{small,balanced,high}.yaml` map a controlled quality word to
both a Ghostscript `-dPDFSETTINGS` preset (when `gs` is present) and the
permissive lossless fallback options — the model only ever chooses the word.

### Compression semantics (the `run_compress` contract)

`compress_pdf` is **best-effort, never a guaranteed target size.** It has three
behaviors, chosen by `compress_quality` and what's detected — they make *materially
different* promises, so the result must say which one ran:

| Behavior | When | Effect | Preserves text/vectors? |
|---|---|---|---|
| **Lossless optimize** (default) | `gs` absent, or `compress_quality: high` | qpdf/pikepdf object-stream + stream re-compression. Safe, often modest reduction. | ✅ yes |
| **Ghostscript downsample** | `gs` detected + `small`/`balanced` | `-dPDFSETTINGS` image downsampling — the good lossy path. | ✅ text kept; images degraded |
| **PDFium-rasterize + Pillow** (fallback lossy) | `small` requested but `gs` absent | rasterize each page → re-encode as image-only PDF. Last resort. | ❌ **destroys selectable text and vectors** |

Rules:
- The rasterize fallback is **only** used for an explicit `small` request with no `gs`,
  and `verify_output`/`format_results` must **warn** that selectable text was lost.
- "make this PDF small enough to email" → best-effort; we do **not** silently rasterize
  to hit an arbitrary target. If lossless can't shrink it and `gs` is absent, return a
  clear message ("lossless got it to N MB; install Ghostscript for stronger
  compression, or allow lossy rasterization") rather than degrading quietly.
- `verify_output` checks: output exists, page count unchanged, and reports
  original→new size + percent reduction (it does **not** assert a target was met).

## Phases

Inline `- [ ]` checkboxes track progress. TDD throughout
(RED → GREEN → COMMIT — failing test before implementation).

### Phase 0 — Skeleton, deps, fixtures

- [x] Add the optional dependency extra `documents` to `pyproject.toml`: `pikepdf`, `pypdf`, `pdfminer.six`, `pypdfium2`, `python-docx`, `python-pptx`, `openpyxl`, `Pillow`, `reportlab`.
- [x] Create `skills/documents/` with `skill.yaml`, `tools.yaml`, `handlers.py`, `prompt.yaml` skeletons (copy the `skills/ffmpeg` structure per `docs/TOOL_SCHEMA.md` forking workflow).
- [x] Skill skeleton loads: `documents` name/description, `skill_class: handlers.DocumentsSkill`, empty-ish tool set + core control tools.
- [x] Add a tiny external-tool detector helper (mirror ffmpeg's `FFmpegNotAvailable` pattern) for `tesseract`/`gs`/`soffice`; default-permissive code never calls them.
- [x] Fixture generator (`eval/fixtures.py`): produce small deterministic sample files — a 3-page text PDF, a 1-page scanned-style image PDF, a `.docx`, a `.pptx`, an `.xlsx`, a `.png`, a `.txt`, a `.md`. Keep tiny; commit or generate idempotently.

**Verify:** `uv run pytest skills/documents/python/tests -v` (skeleton load test green); `just check` green.

### Phase 1 — Text-native documents skill (no OCR)

Author each tool test-first. Group commits per tool (RED→GREEN→COMMIT).

- [x] `inspect_document` (readonly): pages, format, size, encrypted?, has_text_layer? Tests over each fixture format.
- [x] `extract_text` (readonly): PDF text-layer (pypdf/pdfminer), docx/pptx/xlsx (office libs), txt/md (trivial). Returns **per-page** text (`pages: [{page, text}]`) + joined `text`. Tests assert known substrings per fixture and correct page attribution.
- [x] `find_in_document` (readonly): reuse `extract_text` per page, match a query (case-insensitive default, optional regex); return matching page numbers + surrounding snippets. **Within one document only** — corpus/semantic search is the deferred search plan.
- [x] `merge_pdfs` (destructive): pikepdf; dry-run renders intended output path + total pages; honors `ctx.dry_run`/`ctx.confirmed`.
- [x] `split_pdf` (destructive): page-range parsing (`"14-40"`, multi-range), per-range output files.
- [x] `rotate_pages` / `remove_pages` / `reorder_pages` (destructive): pikepdf page ops; range parsing reused.
- [x] `watermark` / `add_page_numbers` (destructive): generate an overlay PDF with reportlab, stamp-merge onto each page via pypdf; `watermark` takes text or image + opacity, `add_page_numbers` takes a position arg.
- [x] `protect_pdf` / `unlock_pdf` (destructive): set/remove password via pikepdf; `unlock` requires the password arg.
- [x] `convert_document` (destructive): images→pdf (Pillow, reimplemented wrapping logic — no img2pdf/LGPL), →txt, →md; office→pdf path gated on detected `soffice` with a clear "install LibreOffice" message when absent.
- [x] `compress_pdf` (**Intent**): `expand` → `inspect_document` → **`run_compress`** → `verify_output`. Per "Compression semantics": lossless qpdf/pikepdf default; `gs` `profiles/compress/*` preset when detected + lossy quality; rasterize fallback only for explicit `small` without `gs` (warn on text loss). `summarize()` for `show_plan`.
- [x] `profiles/compress/{small,balanced,high}.yaml` + `arg_value_sets.compress_quality` wired and validated.
- [x] Declare internal leaf steps in `tools.yaml` as `internal: true`: `run_compress` and `verify_output`; reuse `knaif.steps.ResolveInputs`. (Every tool the Intent emits must be declared — TOOL_SCHEMA line 237.)
- [x] `prompt.yaml`: model-visible rules + short concrete examples; qualitative-phrase mapping ("make it small" → `compress_quality: small`).
- [x] `data/train.jsonl` seed + `data/safety_test.jsonl` (unsafe phrases → `reject`); smoke-test JSONL validity.
- [x] Skill-level `format_results` for clean CLI output (`command`/`output`/`error` kinds).
- [x] `list_skills()` now returns `["documents", "ffmpeg", "io"]`; update **every** doc that enumerates skills: `CLAUDE.md`, `README.md` (line ~42 + the skill bullets ~71), `AGENTS.md` (line ~44), and `docs/TODO.md` (mark the second-skill item done + add a documents section). Grep `["ffmpeg", "io"]` to catch all occurrences.

**Verify:** `uv run pytest skills/documents/python/tests --tb=short`; full suite green
(`uv run pytest --tb=short`); `CommandAgent.from_skill("skills/documents", sandbox=...)`
dispatches each tool; destructive tools blocked without `dry_run`/`confirmed`.

### Phase 1.5 — OCR

- [x] **Resolve the Python-version gate first** (see the note above the checklist): chose (c), skip `ocrmypdf` and build the searchable-PDF rebuild with `pytesseract` + `pypdfium2` + `pypdf`.
- [x] Add a **separate** `documents-ocr` optional extra (`pytesseract`, with PDF rasterization through **pypdfium2, not Ghostscript**); the `tesseract` binary is detected on PATH (never bundled). Document the `tessdata_fast` (~4 MB/lang, faster/smaller, default recommendation) vs `tessdata_best` (~15 MB/lang, slower/larger, higher accuracy) tradeoff; default to `eng`, allow language arg.
- [x] `ocr_document` (**Intent**): inspect → (rasterize if no text layer) → Tesseract → rebuild searchable PDF. Implementation note: the first runtime path rebuilds directly with `pytesseract` + `pypdfium2` + `pypdf`, so it does not require Ghostscript.
- [x] `extract_text` gains OCR fallback: when a PDF has no text layer or the input is an image, route through Tesseract.

> **Known limitation (whole-document OCR granularity):** `inspect_document.has_text_layer`
> is true if *any* page carries text, and `run_ocr` skips (copies) a PDF whose
> `has_text_layer` is true. So a **mixed** PDF (e.g. one text page + nine scanned
> pages) is treated as already-searchable and its scanned pages are **not** OCR'd;
> conversely a fully text-free PDF rasterizes every page. Per-page text-layer
> detection is out of Phase-1.5 scope — revisit if mixed scanned/native PDFs become a
> real use case.
- [x] Tests use the scanned-style image PDF + a `.png` fixture; skip cleanly (not fail) when `tesseract` is absent, mirroring the ffmpeg-binary-optional test pattern.

**Verify:** OCR tests pass when `tesseract` present, skip with a clear reason when
absent; `extract_text` returns text for the scanned fixture only under OCR.

### Phase 2 — Eval plugin + corpus (mirror ffmpeg)

- [x] `skills/documents/eval/verifiers.py`: `cheap` (plan routing) + `honest` (`inspect_document`/pdfinfo-style ground truth: page count, encryption, output exists, extracted text contains expected substring).
- [x] `skills/documents/eval/fixtures.py` (reuse Phase 0 generator).
- [x] `Skill.run_artifact` for fixture re-execution.
- [x] `data/eval.jsonl` seed corpus (start ~20–30 rows: plan + clarify + reject + multi-step); grow later in owner-reviewed batches (see `docs/CORPUS_AUTHORING_STEPS.md`).
- [x] One mock-mode cheap run as a sanity check (routes correctly: 100% tool accuracy + schema validity). Note: `eval_backends.yaml` is **skill-agnostic** (it configures model backends shared across skills; the skill is chosen via `--skill`, and `ffmpeg` has no entry there either), so there is no per-skill "documents arm" to add — the originally-planned edit is N/A by the file's design.

**Verify:** `uv run -m knaif.evalsuite run --skill documents --verifier cheap --limit N`
(mock) routes correctly; honest verifier grades a real dry-run/exec against fixtures.

**Verified 2026-06-22:** `uv run -m knaif.evalsuite fixtures regen --skill documents --force`;
`uv run -m knaif.evalsuite run --skill documents --backends mock --verifier cheap --limit 1`;
`uv run -m knaif.evalsuite run --skill documents --backends mock --verifier honest --limit 1`.
The one-row mock limit is intentional until the future `input_refs`/document-extension
core work lets `.pdf`/`.docx` inline filenames pass the NL clarify gate.

## Out of scope (this plan)

- **Local corpus search** — **now its own plan:
  [document-corpus-search](2026-07-22-document-corpus-search.md)** (extracted 2026-07-22;
  the design that had been living in this section moved there verbatim, and the summary
  below is kept for context). Two corrections were made on extraction: the
  `retrieve_tools` half of the original framing is **closed** — fixed lexically by
  [retrieval-overhaul](2026-07-02-retrieval-overhaul.md), which also declined its embedding
  phase — so corpus search is a **skill-level** capability, not a core retriever
  replacement.
  Decision recorded that search will likely be built **FTS5 keyword first, then an
  optional embedding/hybrid layer** (default embedding model TBD —
  `bge-small-en-v1.5` for English or `multilingual-e5-small` if BG/ZH matter).
  - **North-star use case for that plan:** *"find me the invoice to company X from
    august."* This is a **hybrid corpus** query — content match ("invoice") + entity
    ("company X") + a date filter ("august") where the date is the one **printed on
    the document**, not the file's mtime. Pure vector similarity handles it badly.
    The search plan must therefore include **ingest-time structured extraction**
    (doc-type, party/entity, date, total) layered with keyword + optional embeddings,
    not just a vector index. The documents skill provides the ingest groundwork
    (`extract_text` + OCR fallback, and optionally a structured-metadata extractor);
    `find_in_document` is the *within-one-document* cousin and does **not** attempt this.
  - **Default-mode decision (search UX):** **on-demand by default** — the user points at
    a fresh folder and asks; the `search` Intent fans out, runs a cheap-filters-first
    cascade (file-type → `extract_text` → keyword pre-screen → date filter → LLM
    structured extraction only on survivors), streams progress, and returns the result
    with **no required pre-scan step**. Extracted text/metadata/embeddings are persisted
    as a **transparent lazy cache keyed by `path + content hash`** — the first query on a
    folder pays the full scan, repeat queries only process new/changed files. An explicit
    eager `index` Intent stays **opt-in** for large (10k+ files) or frequently-queried
    corpora. Design constraints carried to that plan: OCR is the per-file bottleneck (OCR
    lazily, candidates only); **bound the work** (progress + cancellation + a confirm gate
    before heavy OCR over a large folder, per knaif's confirm model); decide **where the
    cache lives** (`.knaif/` in the folder vs. a central cache — affects privacy and
    portability); embeddings are skippable for structured queries like the invoice
    north-star and only earn their cost on vague semantic queries.
- **Lossy compression as a bundled default** — best-effort only without `gs`.
- **High-fidelity Office→PDF** — optional, detected LibreOffice only (no low-fidelity fallback in Phase 1).
- **PDF→Office (PDF to Word/Excel/PowerPoint)** — high-demand but a layout-reconstruction problem with no good small permissive engine; out of scope.
- **Redact, crop, repair, PDF/A, fill-and-sign, compare** — deferred (redact needs its own design; PDF/A wants Ghostscript/veraPDF).
- **`input_refs` extraction** — moves to the vector/search plan (query inputs are
  what force it).

## Risks

1. **`extract_text` quality on messy PDFs.** pypdf/pdfminer are good for clean
   text-layer PDFs; complex multi-column layouts are weaker than poppler. Mitigation:
   poppler `pdftotext` as an optional detected subprocess upgrade (GPL, never linked).
2. **OCR footprint.** Tesseract binary + language data is a real bundle cost; keep
   it Phase 1.5 and `tessdata_fast` by default.
3. **Contract surprises.** This is the whole point — log any `skill.yaml`/`tools.yaml`/
   `Step`/`Intent` friction the documents skill exposes; feed it back into the
   monorepo plan's Phase 0 before the contract is frozen.
