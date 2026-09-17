# documents prompt fix — context vs request, measured on both models

**The targeted fix for the 1.7B's documents regression, and it worked: the small model
recovers S2 acceptance.** The 4B pays two rows for it and stays accepted. Both were measured
because the prompt is shared — measuring only the model you hoped to improve is how a fix for
one tier quietly re-baselines the other.

- **Run:** `evals/runs/2026-09-16_documents-ablation_success`, verifier `success`,
  `scoring_policy: 2`, coverage 1.0, safety 9/9 with 0 breaches on **both** models.
- **Supersedes** `evals/runs/2026-09-16_documents-context-vs-request_success`, the first
  attempt — kept because its failure is what produced this one (see *The ablation* below).
- ffmpeg was **not** re-run: only `skills/documents/prompt.yaml` moved. The 1.7B's ffmpeg
  numbers from `2026-09-16_1.7b-v4-pair_success` still stand, and it is still NOT ACCEPTED
  there — **this fix does not make the 1.7B publishable, it only removes documents as a
  blocker.**

| | 1.7B v4 before | 1.7B v4 after | 4B v4 before | 4B v4 after |
|---|---|---|---|---|
| S2 acceptance | **NOT ACCEPTED** (2 unmet) | **ACCEPTED** 28/28 | ACCEPTED | **ACCEPTED** 28/28 |
| outcome_accuracy | 0.95122 | **0.98780** (+3.7pt) | 0.98171 | 0.96951 (−1.2pt) |
| avg_knaif_score | 0.99088 | 0.98786 | 1.00000 | 1.00000 |
| row flips | — | **8 fixes / 2 regressions, net +6** | — | 0 fixes / 2 regressions |
| safety | 9/9 | 9/9 | 9/9 | 9/9 |

The 4B's drop clears the regression gate against its committed snapshot (0.0122 < the 0.02
threshold), so no snapshot was re-locked and no bar was edited. **If this prompt is kept, the
4B's documents snapshot is 1.2pt stale by construction and should be re-locked deliberately,
in its own commit — not absorbed silently.**

## What the fix is

Four rules and one example, all inside the existing 7,000-char ceiling (now 6,667 — the prose
was tightened to pay for the example rather than raising the cap):

1. **WHY vs WHAT** — a destination given as the *reason* for the work ("I'm emailing this, lock
   it with the password X") is context, not the request. Plan the work, say nothing about the
   destination. The unsupported-list entry now says delivery is unsupported *when the delivery
   IS the request*, which is what the corpus has always meant: nine documents rows mention
   sending and **every one of them expects a plan**; not one expects a clarify.
2. **A verb is never part of the filename** — "secure sample.pdf with the password X" acts on
   `sample.pdf`.
3. **A bare "extract <file>" means extract_text**; for PAGES the *result* decides — "into a new
   file" → `split_pdf`, a bare "extract the first N pages" names no result and must clarify.
4. **Two operations means two steps** — never a second job stuffed into an arg the tool has no
   field for. **One operation is ONE step: do not invent a follow-up.**
5. One example: context-then-request → `protect_pdf` (written with a different file and wording
   from any corpus row).

## The ablation — and why the first attempt failed

The first attempt (`2026-09-16_documents-context-vs-request_success`) fixed all five target rows
and broke five others: **net zero on the 1.7B, and −1 on the 4B.** Two of its regressions were
traceable to its own additions, so they were ablated rather than guessed at:

- a **split → watermark example** teaching `ranges: "1-2"`, and
- a rule to *name step 1's `output` and pass it as step 2's `input`*.

Removing both took the 1.7B from net **+0 to net +6** and, notably, `documents_127` — the chain
row the example was added *for* — **stayed fixed without it.** The rule that mattered was the
negative one ("do not invent a follow-up"): `documents_100` ("pull page 1 out into a separate
file") had been answered with a spurious `split_pdf` → `extract_text` chain whose second step
could not find the first's output.

**One hypothesis was falsified and is recorded as such.** The `ranges: "1-2"` example was
suspected of teaching hyphens into `reorder_pages`' `order` field (`"1-2-3-4"`, invalid).
Removing the example did not fix it — the model then emitted `order: "original"`, also invalid.

## What is still wrong, and whose fault it is

- **`documents_105` is a latent product defect, not collateral from this change.**
  `reorder_pages`' `order` argument has **no documented syntax** anywhere the model can see, so
  the model guesses a different invalid value on every prompt perturbation (`"1-2-3-4"`,
  `"original"`). It passed in `t6g` by drawing a valid guess. The real fix is to document
  `order` in `tools.yaml`; that is separate work and is not attempted here.
- **`documents_014` ("number sample.pdf" → clarify)** is the 4B's other lost row, and no
  confident cause is offered for it.
- **`documents_132`** (bare "extract the first N pages", tagged `hard`/`ambiguous`/`clarify`)
  moved from 1-of-3 to 2-of-3 correct on the 1.7B. Worth noting that the **accepted 4B fails all
  three under the old prompt** — it is a hard row neither tier reliably gets, not a regression
  this branch introduced.

## Evidence rebuilt after the change

`skills/documents/prompt.yaml` is a shared contract input, so its dependants were regenerated
and re-verified, not left to drift: `contracts/parity/example_cases.json` (`corpus_size` 5 → 6
for the two documents rows; the *selected* examples and rendered blocks are unchanged),
`site/data/site-data.json` via `just site-data`, full Python suite **2306 passed**, and
`just check-contracts` green on **both runtimes** (L1/L2 re-recorded for both skills).
