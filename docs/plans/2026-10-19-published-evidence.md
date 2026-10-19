# What a published model or skill brings with it

**Status:** Draft · **Created:** 2026-10-19 · **Completed:** —
**Owner:** evals / models · **Ref:** [faster-release-evidence](2026-10-14-faster-release-evidence.md)
(Part H, where this was decided) · [MODELS.md](../MODELS.md) · [PROVENANCE.md](../PROVENANCE.md)
**Release:** —
**Goal:** a model or skill published here arrives with the evidence the release gates grade it against.

> **Status note:** moved out of the faster-release-evidence plan on 2026-10-19 (owner): it is a
> topic of its own, not release speed. Decided there on 2026-10-15 (owner); not started. Opened when
> the next model or skill is to be published here.

## Why

Models are trained, and skills developed, before they appear in this repository; a release only
ever contains an established result. The release gates judge it against evidence, so a model or a
skill has to arrive with its evidence, not only its files. Today a release's native cells are graded
row by row against the Python run the skill's snapshot was locked from (`source_run`), which must
therefore be committed here with its per-row grades.

## What each brings (decided 2026-10-15, owner)

- **A model:** the GGUF on Hugging Face (public naming per `docs/MODELS.md`), its bytes
  hygiene-scanned; the manifest and `models.yaml` entries and each skill's `recommended_model`; the
  model card; its **full-corpus acceptance run** on the executing verifier, committed under
  `evals/runs/` (made committable by `evalsuite redact`, below); the snapshot re-lock in its own
  commit on the release branch (`just eval-lock`, which records `source_run`); the eval corpus it was
  accepted on, reviewed under the corpus rules (EVAL_FRAMEWORK.md, *Changing a corpus that has a
  locked baseline*). Training rows and training-script changes ship per model, as decided when it is
  published, and `docs/PROVENANCE.md` states per model what was published. What is already public
  stays public. The model then ships through the release's own gates (L3, and L4 on the release
  subset).
- **A skill:** the bundle plus its stage 1–3 evidence (acceptance run, safety run, snapshot locked
  with `source_run`); its first native release measures the full corpus once per platform.
- The model-acceptance instrument (full corpus, Python) therefore runs **before** publication and
  arrives with it. The result store is not filled from it: it holds the native binary's replies,
  recorded by the first release cell that plans them.

## Tasks

- [ ] **P1. `evalsuite redact`:** turn a run folder into one that may be committed: the boards with
      their per-row grades, every path made repo-relative or `~/`, nothing that names the machine or
      the person, verified by `scripts/check_no_local_paths.py`. (`evalsuite row-diff`, the other
      half of the old T8, already exists.)
- [ ] **P2. The docs:** `docs/FINE_TUNING.md` and `docs/MODELS.md` (publishing a model ends with the
      list above), `docs/PROVENANCE.md` (a per-model "what was published" table), `docs/RELEASE.md`
      and AGENTS.md (what a published model and skill bring with them).
- [ ] **P3. The enablers for skills made outside this tree** (forks, community skills), each its own
      public plan when the owner opens it: Python skills discovered from an external path like the
      native `KNAIF_SKILLS_ROOT`; native skill registration as a library crate rather than
      per-domain branches in `apps/cli` (audit F11); a stable interface a client can depend on.
