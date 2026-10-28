# Many files, many outputs: a chain per file

**Status:** Active · **Created:** 2026-10-26 · **Completed:** —
**Owner:** core + ffmpeg + documents · **Ref:** [alerts-known-folders](2026-10-26-alerts-known-folders.md)
(builds on it); [chain-source-threading](2026-09-23-chain-source-threading.md) (fan-out, done);
`contracts/parity/batch_cases.json`, `kind_glob_cases.json`, `many_files_cases.json`
**Release:** 1.4.0

> **Status note (2026-10-26).** Built and measured on both runtimes with the shipped 4B model.
> Two known weak points go to the next model, not to this release's code: four ffmpeg batch shapes
> score below 0.70 on files (the model writes "every video" as `*.mp4`, and some two-result
> wordings), and the training rows for them ship with these features (`train_batch.jsonl`). Left for the
> release: the rows joined each skill's `eval.jsonl` on 2026-10-28 (tag `many_files`; ffmpeg's
> older `batch` slice is a different tag) and their floor is written from the re-lock that first
> measures them, on the generation-1 prompts; the two former clarify rows the new tools now do
> (`ffmpeg_204`, `ffmpeg_208`) are fixed as plan rows (release index *Decisions*); the release evidence runs per
> platform, and `docs/TOOL_SCHEMA.md` still needs a section on batches and output sets (the parity
> cases and the skills' `SPEC.md` are the documentation today).

**Goal:** A request over many files runs the model's steps once per file with a batch report; one
file can give many outputs; a later step reads the files an earlier step produced, whatever their
number.

---

## What it does

Every step has a cardinality (one in one out, each in its own out, many in one out, one in many
out), and a request is a sequence of them. The mechanism links any step's **output set** to the
next step's input, so the model keeps writing literal names and patterns while code does the
per-file work.

- **Batch, then next step:** "convert all the .mov files here to mp4 and make them smaller" runs
  both steps per file; step two reads the files step one produced, not a disk glob.
- **Each file gives several results:** "make a thumbnail and a short gif of every video here".
- **Many into one after a batch:** "compress every PDF here and merge them into one"; fan-in keeps
  input order. A whole folder or pattern merged in one step also works, in order.
- **One file into many, from one tool:** video pieces by duration ("split clip.mp4 into 10-second
  parts", "3 equal pieces"), frames by interval or count ("a picture every 5 seconds", "10 stills
  across the video"), stills at given times ("at 0:05, 0:30 and 1:10"), a PDF every N pages or
  one file per page.
- **Failures:** one bad file leaves the others done; it is printed when it happens and counted
  in the batch report at the end, in both runtimes.
- **The keep rule for intermediates:** a result of the same kind refines (the intermediate goes
  to the run scratch), another kind is a second result and stays, a name the user gave is always
  kept, inputs are never touched.
- **Folder and kind globs:** "every video in Videos", "all PDFs in this folder"; a pattern with no
  batch word clarifies; a glob that matches nothing asks instead of running on nothing.
- **Single-file plans take exactly the previous path.** No regression by construction.

## Bar and evidence

Fixed before measuring. New rows were in `skills/<skill>/data/eval_batch.jsonl` (in `eval.jsonl`, tag `many_files`,
since 2026-10-28; ffmpeg 34 rows,
documents 18, three wordings each) over every shape above and the six-row keep-or-delete table,
graded on every output file and on the files that must be gone.

| Bar | Result |
|---|---|
| New rows, executing verifier, shipped 4B | ffmpeg 0.931 outcome (files right on 82 of 102), documents 0.981 (53 of 54); bar ≥ 0.85 per skill |
| No shape below 0.70 | **not met for four ffmpeg shapes**, accepted on 2026-10-21: handed to the training rows |
| Both regression gates | hold on the full corpus; every moved existing row explained |
| Existing prompts unchanged | byte-identical except where a request carries batch words, a glob or the new tools' words |
| Rules, in tests, both runtimes | single-file unchanged, per-file chain, failure policy, keep rule, fan-in order, output sets, glob-without-batch-word clarifies |
| L1 / L2, both runtimes | 100%, with `batch_cases.json`, `kind_glob_cases.json`, `many_files_cases.json` |
| Native L3 and replay | 0 port bugs within the bound; native writes the same files and prints the same failure and summary lines |

Re-measured 2026-10-25 on the final code with the shipped 4B: ffmpeg 0.931 / 0.969,
documents 0.981 / 0.993; files left behind 0 of 622 (ffmpeg) and 0 of 162 (documents).

## Out of scope

Archives ("into a single zip") wait for an `archive` toolkit; nothing makes a zip today.
