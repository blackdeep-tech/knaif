# Corpus v2 — the proposal (2026-10-17)

Plan [2026-10-14-faster-release-evidence](../../../docs/plans/2026-10-14-faster-release-evidence.md),
Part G. One change to each corpus, then one re-lock per model per skill (G2.3). The owner
said: finish all changes on the corpus before re-locking. This file is the whole change,
waiting for approval; nothing here has touched `eval.jsonl`.

## 1. Removals — approved (8 on 2026-10-17) plus two labelling decisions

The audit's eight (`<skill>/report.md`): every utterance of each row is in the row kept in its
place, with the same expected result, and the pair graded alike in every 1.3.0 board.

| Skill | Remove | Kept in its place |
|---|---|---|
| ffmpeg | 007, 050, 063 | 080, 179, 080 |
| documents | 067, 071, 075, 082, 115 | 006, 007, 008, 142, 137 |

The audit also found five ffmpeg pairs with the **same words and different expected
criteria**. Decisions proposed:

| Pair | Same words | Proposal | Why |
|---|---|---|---|
| 010 / 168 | "make a gif from clip.mp4" | **remove 010**, keep 168 | 168 asks for the same gif with a scale filter and has four more phrasings; 010 names `clip.mp4` but its fixture is `clip_no_audio.mp4` |
| 023 / 097 | "prepare clip.mp4 for WhatsApp" | **remove 023**, keep 097 | 097 also requires h264/aac and has four more phrasings; 023's extra `max_height 720` follows from `max_width 1280` on this 16:9 source |
| 097 / 189 | "подготви clip.mp4 за WhatsApp" | keep both | 189 adds a Bulgarian paraphrase 097 lacks; its looser criteria (no width) grade the same files |
| 078 / 187 | "обърни clip.mp4" | keep both | 187 adds "пусни clip.mp4 на заден ход" |
| 084 / 185 | "преоразмери clip_4k.mp4 до 720p" | keep both | 185 adds "намали резолюцията…". (084's Chinese phrasing says `clip.mp4` for a `clip_4k.mp4` row — a slip in a kept row; left as is, G2.1) |

All ten are in `<skill>/decisions.json`, the file `evalsuite corpus-remove` reads.
After removals: ffmpeg 328 → 323 rows, documents 143 → 138.

## 2. New rows — tag `hard2` (G2.2), chains and multi-file requests first (G2.4)

Drafted in `new_rows.ffmpeg.jsonl` (10) and `new_rows.documents.jsonl` (8); each passes
`evalsuite corpus-check` (schema, tag, no id or utterance the corpus or `train.jsonl` has).
Aimed at the audit's coverage gaps: ffmpeg had no chain row through `concat_video`,
`prepare_for_platform` or `adjust_volume`; documents none through `unlock_pdf`,
`ocr_document`, `convert_document`, `reorder_pages`, `merge_pdfs`, `protect_pdf`.

| Row | Chain | Graded on |
|---|---|---|
| ffmpeg_hard2_001 | concat → compress | mp4, h264, 18 s |
| ffmpeg_hard2_002 | trim → prepare_for_platform (WhatsApp) | mp4, h264/aac, ≤1280 wide, 5 s |
| ffmpeg_hard2_003 | adjust_volume → extract_audio | mp3, `volume` filter in the chain |
| ffmpeg_hard2_004 | two thumbnails from two files (fan-out) | two mjpeg outputs |
| ffmpeg_hard2_005 | resize → strip_audio → convert (webm) | webm, ≤720 high, no audio |
| ffmpeg_hard2_006 | adjust_speed → trim | mp4, 3 s |
| ffmpeg_hard2_007 | prepare_for_platform (YouTube) + thumbnail | mp4 h264/aac, and an mjpeg |
| ffmpeg_hard2_008 | adjust_volume → resize | mp4, ≤480 high, `volume` and `scale` |
| ffmpeg_hard2_009 | two conversions of two files (fan-out) | mp4 h264, and mkv |
| ffmpeg_hard2_010 | trim → reverse | mp4, h264, 4 s |
| documents_hard2_001 | unlock → rotate | decrypted, rotation 90, 3 pages |
| documents_hard2_002 | ocr → compress | text layer "Scanned image text" |
| documents_hard2_003 | convert docx→pdf → add_page_numbers | pdf, docx text, numbered from 1 |
| documents_hard2_004 | reorder → protect | encrypted with `secret`, order Gamma/Alpha/Beta |
| documents_hard2_005 | merge (two files) → split | 2 pages, Alpha/Beta |
| documents_hard2_006 | watermark → rotate 180 | DRAFT on every page, rotation 180 |
| documents_hard2_007 | remove_pages → watermark → compress | 2 pages Alpha/Gamma, FINAL on both |
| documents_hard2_008 | split → reorder | 2 pages, Beta/Alpha |

What the drafts do not settle:

- **ffmpeg baselines are `validated_by: null`**: an agent never marks a baseline human-validated.
  The owner reviews them in the authoring notebook (`just baseline-authoring`) before or after
  the append; grading uses `success_criteria`, so the re-lock does not wait on it.
- **Chain shape.** A row's `outputs` entry count must match the chain the model actually
  produces (EVAL_FRAMEWORK, *Multi-output rows*). Only the three fan-out rows (004, 007, 009)
  carry `outputs`, with one entry per deliverable; the other chains are graded on the final file.
- **Fixture rule.** Rows that name a second file (`clip2.mp4`, `clip.mov`, `sample-scanned.pdf`)
  set `fixture` to the first one, as the existing multi-file rows (ffmpeg_252, documents_081) do.
- **The `hard2` slice floor** is written after the re-lock measures it (G2.2), by the owner.

After the append: ffmpeg 333 rows, documents 146.

## 3. What happens then (on the green light; nothing before)

`evals/runs/2026-10-18_relock-corpus-v2_success/run_all.sh`: `remove` → `append` → `run`
(both models, both skills, success verifier, CUDA; safety) → `compare` (row by row against the
previous source runs on the kept rows; prediction: no kept row grades differently) → `lock`
(each snapshot from its saved board, with `source_run`, in its own commit).
