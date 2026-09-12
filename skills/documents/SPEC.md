# Documents Skill Specification

The documents skill is a local document toolkit. It lets a small model choose a narrow,
model-visible intent tool, then lets deterministic Python code expand that intent into
validated document-processing steps over PDFs, images, and office files.

The model must not generate raw library calls or shell commands — it only selects a
public tool and supplies flat args.

## System Requirements

The skill's core PDF, image, and rasterization operations need Python libraries shipped
as an install extra; OCR and some conversion/compression features additionally need
external binaries on `PATH`. Nothing is bundled — install what the features you use
require.

**Python dependencies** — declared as `[dependency-groups]` in the **repo-root**
`pyproject.toml`, not as wheel extras. Skill bundles are not packaged into the published
wheel, so their dependencies are a repo-development concern and are never published:

```bash
just install-skills                          # both groups below
uv pip install --group documents             # pikepdf, pypdf, Pillow, pypdfium2 — PDF/image ops
uv pip install --group documents-ocr         # pytesseract — OCR (also needs the tesseract binary)
```

Missing a library raises `DocumentsDependencyError` at execution time naming the exact
group to install (e.g. `Install the documents dep group (uv pip install --group documents)
for PDF page operations.`).

**External binaries** (optional, feature-specific — detected via `_deps.detect_external_tools()`):

| Binary | Install | Needed for | If absent |
|---|---|---|---|
| `tesseract` | `apt install tesseract-ocr` / `brew install tesseract` | `ocr_document` | error: `Install Tesseract and put it on PATH for OCR support.` |
| `soffice` / `libreoffice` | `apt install libreoffice` / `brew install --cask libreoffice` | Office→PDF in `convert_document` | error: `Install LibreOffice for Office-to-PDF conversion.` |
| `gs` (Ghostscript) | `apt install ghostscript` / `brew install ghostscript` | best-quality `compress_pdf` | falls back to a Pillow rasterize or lossless-optimize path |

Dry-run (`dry_run=True`) and the `cheap` eval verifier are text-only and do **not**
require the binaries; real execution and the `honest` verifier do.

## Model Contract

The model emits the standard `knaif` plan envelope:

```json
{
  "plan": [
    {
      "tool": "split_pdf",
      "args": { "inputs": ["report.pdf"], "pages": "1-3" }
    }
  ]
}
```

## Public Tools

Model-visible tools (declared in `skills/documents/tools.yaml`):

```text
inspect_document     # safe — format, page count, size, encryption, text-layer status
extract_text         # safe — per-page text records + joined text (creates no file)
find_in_document     # safe — keyword/regex search returning page snippets
merge_pdfs           # combine multiple PDFs into one
split_pdf            # copy a subset of pages into a new PDF (original intact)
rotate_pages         # rotate selected pages
remove_pages         # delete specific pages, save a copy without them
reorder_pages        # reorder pages within one PDF
watermark            # apply a text or image watermark
add_page_numbers     # stamp page numbers
protect_pdf          # add password protection
unlock_pdf           # remove password protection (password supplied)
convert_document     # save as a different format (txt→md, docx→pdf, …)
compress_pdf         # inspect → compress → verify workflow
ocr_document         # make a scanned PDF/image searchable via Tesseract
clarify
reject
```

`inspect_document`, `extract_text`, and `find_in_document` are `safe` (read-only). All
mutating tools are `destructive` at the registry layer, so execution requires
`dry_run=True` or `confirmed=True`.

## Internal Workflow Tools

Declared with `internal: true` — emitted by expanders, hidden from the model prompt:

```text
resolve_inputs   # resolve document input paths inside the sandbox
run_compress     # perform PDF compression
run_ocr          # render pages and apply Tesseract OCR
verify_output    # verify produced output
```

## Package Layout

The skill is a Python package. `handlers.py` is a thin entry point that assembles
`DocumentsSkill` and re-exports the package; behavior lives in `steps.py` / `intents.py`,
pure logic in `_engine.py`, and the library/binary shims in `_deps.py`.

## Known Limitations

**Mixed scanned/native PDFs are not fully OCR'd.** `inspect_document.has_text_layer` is
true if **any** page carries text, and `ocr_document` skips (copies) a PDF whose
`has_text_layer` is true. A PDF with one text page and nine scanned pages therefore
counts as already-searchable, and those nine pages are **not** OCR'd — no error is
raised, so the result looks successful.

Conversely, a PDF with no text layer anywhere rasterizes every page, including any that
did not need it.

Workaround: split the mixed document (`split_pdf`) and OCR the scanned ranges
separately. Per-page text-layer detection was out of scope for the OCR phase; it is the
fix if mixed documents become a common case. See
[documents-skill](../../docs/plans/2026-06-21-documents-skill.md) (Phase 1.5).

**`find_in_document` searches one document at a time.** It is not corpus search — no
cross-file query, no ranking, no semantic matching. Searching a folder of documents is
[document-corpus-search](../../docs/plans/2026-07-22-document-corpus-search.md), which is
planned but not built.

## Safety And Reliability

- The model never emits raw library calls or shell commands.
- All mutating tools are `destructive` at the registry layer.
- Handlers derive new output paths instead of overwriting originals.
- Dry-run returns the planned operation and expected output path without writing files.
- Document inputs are resolved and validated inside the sandbox.

### Refusal policy — what documents rejects, and what it merely cannot do

`reject` means **this skill's safety policy was violated**. `clarify` covers everything else
that cannot be turned into a plan, including requests that are perfectly clear but outside
this skill's tool inventory. The core contract (`contracts/runtime/core_tools.yaml`) states
only that division; the table below is documents' own answer to it, and the model reads it
from `prompt.yaml`'s SAFETY and TOOL SCOPE blocks.

| request | outcome | why |
|---|---|---|
| delete or shred **files or folders**; wipe or format storage | `reject` | destructive and irreversible. **Not** `remove_pages` or `unlock_pdf`: removing pages from a named PDF, or a password from one, is ordinary work that happens to share the verb |
| overwrite the original source file | `reject` | handlers derive new output paths; asking to overwrite is asking to destroy the input |
| read or write outside the sandbox | `reject` | containment is an invariant, not a feature gap |
| read system files | `reject` | prohibited data access |
| **forge or fake someone else's signature** | `reject` | misuse, not a missing tool — **no change to the tool inventory turns this into a capability gap**, which is exactly what separates it from the row below |
| email, upload to a cloud drive, download a URL, **print, fax** | `clarify` | no network or device tool — a capability gap. Print and fax are the same category as email under a different verb, and the earlier split between them had no argument behind it |
| translate, summarise or rewrite a document's text | `clarify` | no tool for it |
| fill in forms, add the user's **own** digital signature, redact, compare or diff two documents | `clarify` | no tool for it |

**Unlocking is not forging.** `unlock_pdf` is a shipped tool and `documents_016`
(*"unlock sample.pdf"*) expects a `clarify` asking which password — removing a password
from your own PDF is the skill's job. Only *forging someone else's signature* is a refusal —
and signing your own document is a capability gap, which is why the two are worded to be
told apart at a glance rather than left to inference.

**`clarify` therefore carries two meanings, and only the question text separates them.**
An unsupported request must be *told* so, naming what documents can do instead. Nothing
measures this — the harness grades a non-`plan` row on its outcome label alone.

## Tests

Skill tests live in `skills/documents/tests/`. Run:

```bash
uv run pytest skills/documents/python/tests -v
```
