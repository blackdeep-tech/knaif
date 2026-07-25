# Document Corpus Search — find the right file in a folder, in plain English

**Status:** Planning · **Created:** 2026-07-22 · **Completed:** —
**Owner:** documents · **Ref:** [documents-skill](2026-06-21-documents-skill.md) (*Out of scope*)

> **Status note:** Not started. This plan **extracts** a design that had been sitting in the
> `documents-skill` plan's *Out of scope* section since 2026-06-21, where it survived only
> as a bullet list inside a completed plan. Nothing here has been built; the decisions below
> were taken deliberately during that skill's design and are recorded so the eventual build
> starts from them rather than re-deriving them.
>
> **Scope corrected on extraction (2026-07-22).** The original framing bundled this with
> "replace the broken whitespace `retrieve_tools`". That is no longer part of it — see
> *What this is not* below.

**Goal:** Answer "which of my documents is the one I want?" over a folder the user points
at — locally, with no pre-indexing step required.

## The north-star query

> *"find me the invoice to company X from august"*

This single utterance is the design driver, because it is **hybrid** and breaks the obvious
implementation:

- **content match** — "invoice" (a document *type*, inferable from text)
- **entity** — "company X" (a party named inside the document)
- **date filter** — "august", meaning the date **printed on the document**, not the file's
  mtime

Pure vector similarity handles this badly: embeddings blur exactly the distinctions that
matter here (which party, which month), and a nearest-neighbour list over document chunks
will happily return September's invoice to a similarly-named company.

So the design must include **ingest-time structured extraction** — doc-type, party/entity,
date, total — layered with keyword search and *optional* embeddings. Not a vector index
with a query box.

## What this is not

- **Not a replacement for `retrieve_tools`.** The original note in `documents-skill` said
  this capability would replace "the broken whitespace `retrieve_tools`". That problem was
  solved differently and is closed:
  [retrieval-overhaul](2026-07-02-retrieval-overhaul.md) shipped df-weighted lexical
  scoring plus CJK n-gram tokenization (recall@5 ffmpeg 0.853 → 0.954, documents
  0.914 → 0.947, with a CI gate), and **deliberately did not pursue** its embedding phase —
  lexical proved sufficient against the on-device budget. **Tool retrieval and document
  search are separate problems** and should not be re-merged: one ranks a few dozen tool
  descriptions at prompt-build time, the other ranks thousands of user files at query time.
- **Not `find_in_document`.** That tool searches *within one* document and ships today. This
  plan is its cross-corpus cousin.
- **Not a cloud service.** Same privacy premise as the rest of knaif — the documents people
  most want to search are the ones they least want to upload.

## Decisions carried over from the documents-skill design

These were settled in 2026-06-21 discussion. Re-open them only with reason.

1. **Keyword first, embeddings second.** Build FTS5 (SQLite full-text) as the retrieval
   spine; add an optional embedding/hybrid layer only if keyword search plateaus on real
   queries. Default embedding model TBD — `bge-small-en-v1.5` for English-only, or
   `multilingual-e5-small` if BG/ZH matter. Embeddings are skippable for structured queries
   like the north-star and only earn their cost on vague semantic ones.
2. **On-demand by default; no required pre-scan.** The user points at a fresh folder and
   asks. A `search` Intent fans out and runs a **cheap-filters-first cascade** — file-type →
   `extract_text` → keyword pre-screen → date filter → LLM structured extraction only on
   survivors — streaming progress as it goes.
3. **The cache is a transparent lazy artifact,** keyed by `path + content hash`. The first
   query on a folder pays the full scan; repeat queries only process new or changed files.
4. **An eager `index` Intent stays opt-in,** for large (10k+ files) or frequently-queried
   corpora. It is an optimization, never a prerequisite.
5. **Bound the work.** OCR is the per-file bottleneck, so OCR lazily and only on candidates;
   show progress; support cancellation; and put a confirm gate before heavy OCR across a
   large folder, consistent with knaif's existing confirm model.
6. **Reuse the documents skill's ingest primitives.** `extract_text` (with its OCR fallback)
   was deliberately designed as the ingest primitive for this plan. Do not build a second
   extraction path.

## Open questions (settle before building)

- **Where does the cache live?** `.knaif/` inside the searched folder, or a central
  per-user cache. This is a genuine privacy/portability tradeoff: in-folder travels with
  the documents and is easy to delete, but scatters state and may land in shared or synced
  directories; central is tidier but means a background store of extracted text from
  documents the user may consider sensitive. **Decide before any code.**
- **What does structured extraction run on?** An LLM pass over survivors is the obvious
  route, but doc-type/date/total are often regex-tractable. Measure how far deterministic
  extraction gets before paying for inference per file.
- **How is "august" resolved?** Printed dates need parsing across formats and locales, and
  a bare month implies a year the user did not state (most recent? current? ambiguous →
  clarify?). This is a clarify-gate question as much as a parsing one.
- **Does this belong in the `documents` skill or its own skill?** It reuses documents'
  ingest but adds a store, a cache, and a different interaction shape (streaming, long-
  running, cancellable). A long-running Intent is not a shape knaif has yet.
- **Native-runtime story.** The documents skill ships a Rust crate. Is corpus search
  Python-only (like `knaif.cli`), or does it need a native port? Answering this early
  affects whether the store is SQLite (portable) or something Python-specific.

## Prerequisites

- `extract_text` + OCR fallback — **shipped** (documents skill, Phase 1/1.5).
- A research pass on the store and the embedding question — **not done**. This plan is the
  design record; the research pass is its first task.

## Risks

1. **Scope inflation into "local RAG".** The north-star is a *retrieval* task with a
   structured filter, not a question-answering system. Resist adding summarization or
   answer synthesis; the deliverable is *which file*, and where in it.
2. **The cache becomes a liability.** Extracted text from sensitive documents, persisted by
   default, is a privacy surface knaif does not currently have. The cache-location question
   above is the mitigation point.
3. **Long-running Intents have no precedent here.** Every existing Intent expands to a
   bounded plan that executes quickly. Streaming progress, cancellation, and a mid-run
   confirm gate are new machinery — budget for core work, not just skill work.
4. **Embedding cost on the mobile lane.** The 1.7B lane exists for footprint; a per-query
   embedding model is a real on-device cost. This is the same reasoning that stopped
   retrieval-overhaul's Phase 2, and it applies here.
