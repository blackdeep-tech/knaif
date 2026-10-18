# Corpus v2 re-lock run — 2026-10-18 (two attempts; the second locked all four)

Plan [2026-10-14-faster-release-evidence](../../../docs/plans/2026-10-14-faster-release-evidence.md),
Part G (G2.3), T7/T7b. Rules R1–R5 in [`run_all.sh`](run_all.sh), written before the run.
The owner's green light: 2026-10-18 00:02. Stages `remove` → `append` → `run` → `compare` →
`lock`, one after another; 33 minutes of CUDA for the four runs.

## The corpus change (stages `remove`, `append`)

Per [`evals/corpus-review/2026-10-17/proposal.md`](../../corpus-review/2026-10-17/proposal.md):
10 rows removed (`data/eval_removed.jsonl`, every kept line pinned in `data/eval_kept.sha256`)
and 18 `hard2` rows appended.

| Skill | Rows | Utterances | Before |
|---|---:|---:|---|
| ffmpeg | 333 | 883 | 328 / 861 |
| documents | 146 | 175 | 143 / 164 |

## The measurement (stage `run`): Python runtime, success verifier, CUDA, the shipped models

| Model | Skill | Outcome | Knaif | Safety | Previous lock |
|---|---|---:|---:|---|---|
| 4B | ffmpeg | 0.9422 | 0.9847 | 11/11 | 0.9431 / 0.9841 |
| 4B | documents | 0.9314 | 0.9810 | 9/9 | 0.9817 / 0.9803 |
| 1.7B | ffmpeg | 0.9173 | 0.9786 | 11/11 | 0.9199 / 0.9785 |
| 1.7B | documents | 0.9257 | 0.9893 | 9/9 | 0.9634 / 0.9945 |

**Every kept row graded and planned exactly as in the previous source run** (stage `compare`,
`comparison/<model>_<skill>.json`): 856/856 ffmpeg and 159/159 documents utterances, both
models. The prediction held: the aggregates move only by the removed rows' arithmetic and by
the new rows.

### The new rows (`hard2`), the whole reason the aggregates moved

| | 4B | 1.7B |
|---|---|---|
| ffmpeg, 27 utterances | 25 correct, knaif 0.926 | 23 correct, knaif 0.852 |
| documents, 16 utterances | 8 correct, knaif 0.490 | 9 correct, knaif 0.508 |

What fails, by cause:

1. **A known runtime bug, not the model** — `split_pdf`, `rotate_pages` and `reorder_pages`
   count the input's pages before the dry-run check, and the eval runner previews every plan
   before executing it, so a chain whose second step is one of them dies on a file the first
   step has not written yet (TODO, *Previewing a chain that changes pages of a file an earlier
   step creates*, found 2026-10-07). It takes `documents_hard2_005`, `006[0]`, `008[0]` (4B),
   `001`, `005` (1.7B) — the plans themselves are right — and has failed `documents_142[1]`
   since 2026-09-26. A fix on those rows is a measured improvement, and the documents
   baselines re-lock when it lands.
2. **The model asks instead of chaining** — `clarify` on `documents_hard2_003[1]`, `004[0]`,
   `006[1]`, `008[1]`, `ffmpeg_hard2_007` (one phrasing per model), `ffmpeg_hard2_009[0]`
   (1.7B). The hard rows doing what they were added for.
3. **A plan that drops the format step** — `ffmpeg_hard2_005` ("scale to 720p, drop the audio
   and save it as webm"): both models plan resize → strip_audio with no `convert_video`, and
   `strip_audio` then stream-copies h264 into a `.webm` container, which ffmpeg refuses. Two
   things in one: a model gap (no convert step) and a renderer that should never `-c:v copy`
   into a container the codec cannot go in (TODO, new).
4. Partial credit: `documents_hard2_004[1]` (0.83, `pages` not probed through the password),
   1.7B `006[0]` and `008[0]`.

## Attempt 1: acceptance and the lock (stage `lock`, 00:36)

| Cell | Verdict | Locked |
|---|---|---|
| 4B ffmpeg | **ACCEPTED**, 40/40 | yes — `eval_snapshot.json`, `source_run` recorded |
| 4B documents | NOT ACCEPTED, 7 of 37: aggregate outcome 0.931 < 0.95; `split`, `merge`, `reorder`, `rotate`, `watermark`, `chain2` | no |
| 1.7B ffmpeg | NOT ACCEPTED, 1 of 40: `chain3` 0.914 < 0.92 | no |
| 1.7B documents | NOT ACCEPTED, 7 of 37: aggregate 0.926 < 0.95; `split`, `merge`, `rotate`, `protect`, `unlock`, `chain2` | no |

Every unmet threshold is tripped by `hard2` rows: on the kept rows these cells pass exactly as
they did when the floors were written. The new rows were tagged with the tool and chain tags
"they would otherwise get" (G2.2), which put them inside slices whose floors were written for
the old rows — and the documents aggregate floor (0.95) counts them too. G1 warned of this;
G2.2 meant hard rows not to gate the shipped models on rows they never trained on.

**The owner's decision (amendment A1, 11:56):** re-tag the 18 new rows to `hard2` only (plus
the floor-less `fan_out` / `multi_file`), so the existing floors see exactly the rows they were
written for; re-set the documents aggregate outcome floor to 0.91 for corpus v2 (measured 0.931 /
0.926, the way every floor was first set); restate the documents `compress` slice, now 14
utterances, as `max_failures: 1`. Attempt 1's boards are kept in `attempt1-tags-v1/`.

## Attempt 2 (11:56-12:28): all four cells accepted and locked

The four runs gave the same aggregates and the same per-row grades as attempt 1 (every kept
row again 2,030/2,030 against September), so the re-run changed the tags, not the results.

| Cell | Outcome / knaif | Verdict | Snapshot (names its `source_run`) |
|---|---|---|---|
| 4B ffmpeg | 0.9422 / 0.9847 | ACCEPTED, 40/40 | `skills/ffmpeg/data/eval_snapshot.json` |
| 4B documents | 0.9314 / 0.9811 | ACCEPTED, 37/37 | `skills/documents/data/eval_snapshot.json` |
| 1.7B ffmpeg | 0.9173 / 0.9792 | ACCEPTED, 40/40 | `skills/ffmpeg/data/eval_snapshot.knaif-qwen3-1.7b-v2.json` |
| 1.7B documents | 0.9257 / 0.9890 | ACCEPTED, 37/37 | `skills/documents/data/eval_snapshot.knaif-qwen3-1.7b-v2.json` |

**For the owner to write (G2.2), the `hard2` slice as measured:** ffmpeg outcome 25/27 (4B) and
23/27 (1.7B), knaif 0.926 / 0.852; documents 8/16 and 9/16, knaif 0.490 / 0.508. A floor set
from these gates the *next* model; the chain-preview fix (TODO) will lift the documents numbers
on its own, and re-locks the documents baselines when it lands.

## Incidents

None. Attempt 1's `lock` stopped at three refusals as its rule says; amendment A1 was written into
the script before attempt 2's stages ran; attempt 2 ran once, in order.
