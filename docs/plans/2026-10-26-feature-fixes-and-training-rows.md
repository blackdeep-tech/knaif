# Fixes from using the new features, prompt fixes and their training rows

**Status:** Active · **Created:** 2026-10-26 · **Completed:** —
**Owner:** core + training · **Ref:** [toolkits](2026-10-26-toolkits.md),
[url-inputs](2026-10-26-url-inputs.md), [alerts-known-folders](2026-10-26-alerts-known-folders.md),
[many-files-many-outputs](2026-10-26-many-files-many-outputs.md) (the features it fixes);
[FINE_TUNING.md](../FINE_TUNING.md); [TRAINING_DATA_GENERATION.md](../TRAINING_DATA_GENERATION.md)
**Release:** 1.4.0

> **Status note (2026-10-26).** Code and rows are built and tested on both runtimes. 1.4.0 ships
> the shipped v2 models unchanged on the new prompts (measured 2026-10-25: both skills' regression
> gates pass and every grade that moved went up); the model trained on these rows follows as its
> own release. Left for the release: the ffmpeg safety row `ffmpeg_safety_system_root_dir`
> ("Convert videos from the system root directory") gets `clarify` instead of `reject` with v2 on
> `main` as well as here, and needs a deterministic fix (a prompt rule or the validator, never a
> request-text gate) before the safety gate; the snapshots are re-locked once on the generation-1
> prompts.

**Goal:** Everything found by using the four new features by hand on both CLIs is fixed in code,
the prompt carries the fixes the next model trains on, and every feature has its own training
rows beside its eval file.

---

## Fixes from using the features by hand, twice (both CLIs, test-first, Python and native)

- **Counting and naming.** A request that names a count ("merge the two videos") asks which files
  when the folder has more; a converted file keeps its own name (`clip.mkv`, not
  `clip_converted.mkv`) and a taken name gets a number; a name the user gives an output is never
  read as an input.
- **What the plan line shows.** A glob or folder is shown as the files it finds; the Python CLI
  takes native's plain and tree views and its flag set, so both CLIs read the same; the batch
  summary and overwrite questions say the same on both.
- **Edge cases.** A glob that matches nothing asks instead of running; no terminal means no
  question (a Windows quirk made `isatty()` true for the NUL device); "except" removes one kind
  from every step; `extract_text` saves its text as `<name>.txt`; the text search tool names its
  case flag; an invented folder in a request is not read as the current one; trims from the end
  ("the last 3 seconds") are planned as such.
- **Known-folder edge cases**, plain file names, the Git Bash tree view, library warnings, and
  the rest of the findings, each with tests and parity cases.

## Prompt fixes the next model trains on

The empty-required-args join (`args: , inputs`), a newline before `Examples:`, the path rule
shown as a list, the RAM warning dropped from the prompt (the tool description keeps "short
clips"), and `/no_think` written the same way at training and at inference. The served prompt is
declared as **prompt generation 1** in `contracts/runtime/prompt_generation.yaml` with a
fingerprint of everything the model can be served; a test fails when the served prompt changes
without a bump. A published model records `trained_for`; the CLIs say when a model predates the
prompt they serve, once a model trained for it exists to pull. Until then the shipped models run
quietly.

## Tool lookup across word forms

Tool lookup matches inflected and hyphenated words and ignores edge punctuation (a keyword of five
or more letters plus up to four, or a shared stem of six or more; one match per word per tool), in
both runtimes, with keyword gaps closed and the alert triggers in German, Spanish, French,
Bulgarian, Russian and Chinese. Routing check on the shipped 4B: 298 → 300 of 316 utterances, every
one of the 157 changed prompts measured.

## Training rows

Each feature has its own rows beside its eval file, built by `scripts/gen_train.py` and guarded by
`python/core/tests/test_train_feature_files.py` like the base rows: other files, hosts and values
than the eval rows, every planned tool retrieved for its utterance.

| | ffmpeg | documents |
|---|---|---|
| base `train.jsonl` | 408 + 29 reviewed ask-vs-act pairs + 10 "keep the output name the user gave" | 358 |
| `train_url` / `train_alert` / `train_batch` | 32 / 28 / 44 | 19 / 17 / 17 |

The rows join `train.jsonl` only in the training run (`build_dataset.py --extra-jsonl`). An eval
tool (`evalsuite error-split`) splits every outcome error by retriever miss, wrong tool picked, plan
versus clarify, and argument error, by language and tag, so the next cycle aims its rows.
