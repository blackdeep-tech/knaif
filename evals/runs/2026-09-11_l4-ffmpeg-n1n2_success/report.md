# L4 — ffmpeg on the shipped native binary, after N1 + N2, 2026-09-11

Third L4 run, taken after glob expansion (N1) and stem resolution (N2) landed natively, plus the
fourth lane defect (fixtures withheld from native that Python could see). **Supersedes
`../2026-09-11_l4-ffmpeg-rerun_success/`.**

| | |
|---|---|
| lane | `native-cli` — `knaif run ffmpeg --yes`, executing for real |
| compute | `CUDA0` (RTX 5080), probed from the binary |
| corpus | 314 rows / 847 utterances · verifier `success` |
| tree | **clean** — `git_sha 784a667`, `git_dirty: false` |

## Verdict: NOT ACCEPTED — 4 of 36 unmet (was 11, originally 16)

| metric | run 1 | run 2 | **run 3** | Python | floor | |
|---|---|---|---|---|---|---|
| `outcome_accuracy` | 0.8123 | 0.8430 | **0.88194** | 0.9020 | 0.88201 | ✗ by 0.00007 |
| `avg_knaif_score` | 0.9827 | 0.9835 | **0.9707** | 0.9738 | 0.9538 | ✓ |
| `schema_validity` | 0.8867 | 0.9174 | **0.9587** | 0.9847 | — | |
| coverage | 1.0 *(false)* | 1.0 | **1.0** | — | 1.0 | ✓ |
| safety | 5/9 +1 phantom | 6/9 | **6/9, 0 breaches** | 6/9 | 9/9 | ✗ |

**It misses the aggregate floor by 0.00007 — six hundredths of one row.** One more correct
utterance out of 847 would clear it. Outcomes: `plan` 530 → **560**, `error` 70 → **35**.

Remaining failures: the aggregate above, `resize` 0.883 (floor 0.900), `edge` 0.774 (floor 0.780),
and safety 6/9 — the three *over*-refusals that are the open S5 owner decision, identical to
Python's result on the same corpus.

## What the two fixes moved

| slice | before | after | Python |
|---|---|---|---|
| `batch` | 0.034 | **1.000** | 1.000 ✓ |
| `codec` | 0.773 | **1.000** | 1.000 ✓ |
| `thumbnail` | 0.750 | **1.000** | 1.000 ✓ |
| `multilingual` | 0.968 | **1.000** | 1.000 ✓ |
| `zh` | 0.900 | **1.000** | 1.000 ✓ |
| `extract_audio` | 0.897 | **0.949** | 0.949 ✓ |
| `convert` | 0.728 | 0.960 | 0.968 |
| `complex` | 0.835 | 0.892 | 0.906 |
| `compress` | 0.833 | 0.875 | 0.892 |
| `create_thumbnail` | 0.782 | 0.818 | 0.855 |

Six slices now equal Python exactly. `convert` — the largest failing slice at 125 utterances —
cleared its 0.920 floor.

## The one apparent regression is the instrument getting honest

`crop` 1.000 → 0.750 and `geometry` 1.000 → 0.833, all four rows being `ffmpeg_294`
("crop to 9:16"). **Investigated rather than reported as a regression:**

- The **plan and the rendered command are byte-identical** across the two runs.
- The command genuinely fails: reproduced by hand, ffmpeg writes a **0-byte** `clip_cropped.mp4`
  and exits non-zero, **deterministically** (two consecutive runs of the shipped binary, both
  exit 1; `ffprobe` on the output says *moov atom not found*).
- The previous run recorded `outcome=plan, knaif=1.0` with `matched=['filter:crop']` — it scored
  a **broken, empty artifact as a perfect pass** because the criteria matched *command text*
  rather than the file.

So this is **N4**, the known aspect-crop defect that fails on *both* runtimes, now being counted
instead of hidden. ⚠️ **What is not established** is why the process exit code differed between
the two runs — the command is identical and nothing in N1/N2 touches execution. Recorded as an
open question rather than explained away.

`avg_knaif_score` 0.9835 → 0.9707 has the same shape: 30 more rows entered the **scored**
population (`plan` 530 → 560), and previously-excluded failures now count. A quality average that
falls because more work is being graded is not a quality regression.
