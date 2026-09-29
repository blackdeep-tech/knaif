# Chain source threading — T6 evidence

**Plan:** [chain-source-threading](../../../docs/plans/2026-09-23-chain-source-threading.md) T6 ·
**Date:** 2026-09-25/26 · **Verifier:** `success` (+ safety) · **Driver:** `run_all.sh` (local, gitignored)

**Question:** do the named-once rule and the kind rule for chain source threading change
anything on the existing corpus, and do the new fan-out rows pass?

## Setup

| Arm | Code | Tree |
|---|---|---|
| control | `release/1.2.0` @ `9b66e47` | detached worktree `sandbox/t6-control`; `PYTHONPATH` at its own `python/core` (verified: `KNAIF_IMPORTED_FROM`), `models/` a junction |
| treatment | `fix/chain-source-threading` @ `5df2047` | main checkout, clean |

Both arms use the same model (`qwen3-4b-sft-v4-flat-q4`, public `knaif-qwen3-4b-v2`, aligned
config), the same interpreter, fixtures regenerated per arm, and ran one after the other on the
RTX 5080. ffmpeg's populations differ by design: treatment adds `ffmpeg_301/302` (10 utt).

## Results

| | control | treatment |
|---|---|---|
| ffmpeg outcome / knaif | 0.93772 / 0.98373 (851) | 0.93844 / 0.98401 (861) |
| ffmpeg, shared 851 utt | 0.93772 / 0.98373 | 0.93772 / 0.98373 |
| documents outcome / knaif | 0.97561 / 0.99782 (164) | 0.97561 / 0.99782 (164) |
| safety ffmpeg / documents | 11/11, 9/9, 0 breaches | 11/11, 9/9, 0 breaches |

- **Shared utterances:** every final plan and every score is identical, 851/851 for ffmpeg and
  164/164 for documents. The rule changed no existing row, so there was no chain row to read.
- **New rows:** all 10 utterances of `ffmpeg_301` (thumbnail + compress of `clip.mp4`, named
  twice) and `ffmpeg_302` (two trims of `clip.mp4`) score 1.0, in English, German, Bulgarian and
  Chinese.
- **Regression:** the control reproduces the committed ffmpeg snapshot exactly. `eval-regression`
  is OK for documents (treatment) and ffmpeg (control; the treatment's 861 cannot be compared
  with an 851 snapshot, and it equals the control row for row).
- **Acceptance (treatment):** ffmpeg ACCEPTED 39/39, documents ACCEPTED 36/36.

**Verdict:** pass. The ffmpeg snapshot is re-locked from the treatment arm (861 utt).

## Environment fault, fixed

The control's first documents pass is **invalid**: 81 rows errored with a missing `sample.pdf`.
The documents fixture generator shells out to `uv run python skills/documents/eval/fixtures.py`,
which ignores `PYTHONPATH`, built a separate venv inside the worktree, and silently wrote only the
`.md`/`.txt` fixtures. `skills/documents/eval` is identical between the two commits, so the control
was re-run on fixtures copied from the treatment arm (`fixture_hashes` equal). The invalid pass is
kept locally in `control/documents_missing_fixtures/`.

ffmpeg's `fixture_hashes` differ only in `sub/a.mp4` and `sub/b.mp4`: 1-byte strays from
2026-09-13 in the main checkout's sandbox. No generator makes them and no row reads them.
