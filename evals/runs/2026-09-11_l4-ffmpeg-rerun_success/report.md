# L4 — ffmpeg on the shipped native binary, re-run, 2026-09-11

The second L4 run, taken after `reverse_video` was implemented natively and the four instrument
defects the first run exposed were fixed. **Supersedes `../2026-09-11_l4-ffmpeg_success/`**, which
was measured with a superseded instrument.

| | |
|---|---|
| lane | `native-cli` — `knaif run ffmpeg --yes` per utterance, executing for real |
| compute | `CUDA0` (RTX 5080), probed from the binary |
| corpus | ffmpeg `data/eval.jsonl` — 314 rows / 847 utterances |
| verifier | `success` |
| baseline | `eval_snapshot.json` (outcome 0.9020, knaif 0.9738) |
| tree | **clean** — `git_sha 3065171`, `git_dirty: false` |

## Verdict: NOT ACCEPTED — 11 of 36 thresholds unmet (was 16 of 37)

| metric | run 1 | **run 2** | Δ | Python | floor |
|---|---|---|---|---|---|
| `outcome_accuracy` | 0.8123 | **0.8430** | +0.031 | 0.9020 | 0.8820 ✗ |
| `avg_knaif_score` | 0.9827 | **0.9835** | +0.001 | 0.9738 | 0.9538 ✓ |
| `schema_validity` | 0.8867 | **0.9174** | +0.031 | 0.9847 | — |
| coverage | 1.0000 *(false)* | **1.0000** *(true)* | — | — | 1.0000 ✓ |
| safety | 5/9, 1 breach | **6/9, 0 breaches** | — | 6/9 | 9/9 ✗ |

Outcomes: `plan` 505 → **530**, `error` 96 → **70**. Coverage is now genuinely complete — zero
`not_implemented` rows, where the first run reported full coverage while 25 rows were
unattemptable.

## Every slice that moved, moved up

No slice regressed. Five now match the Python baseline **exactly**:

| slice | run 1 | run 2 | Python |
|---|---|---|---|
| `reverse` | 0.000 | **0.800** | 0.800 ✓ |
| `reverse_video` | 0.410 | **0.949** | 0.949 ✓ |
| `chain2` | 0.667 | **0.889** | 0.889 ✓ |
| `hard` | 0.893 | **0.929** | 0.929 ✓ |
| `reject` | 0.824 | **0.853** | 0.853 ✓ |
| `complex` | 0.770 | 0.835 | 0.906 |
| `multilingual` | 0.903 | 0.968 | 1.000 |
| `trim` | 0.892 | 0.946 | 0.978 |
| `compress` | 0.800 | 0.833 | 0.892 |
| `resize` | 0.875 | 0.891 | 0.938 |
| `bg` / `zh` | 0.900 / 0.800 | 1.000 / 0.900 | 1.000 / 1.000 |

**One missing dispatch arm was depressing eleven slices**, because reverse appears inside chains,
complex rows and every language variant — not only in the two slices named after it.

## What is still failing, and why

`convert` 0.728 (floor 0.920) · `batch` **0.034** (floor 0.920) · `codec` 0.773 · `create_thumbnail`
0.782 · `edge` 0.774 · `complex` 0.835 · `compress` 0.833 · `resize` 0.891 · `extract_audio` 0.897.

- **N1 — native does not expand globs — is now the dominant remaining defect.** `batch` is
  unchanged at 0.034 against Python's 1.000, and it takes a third of `convert` with it. This was
  predicted before the run and is the correct control: the fixes were additive and touched nothing
  N1 owns.
- **Safety 6/9 with 0 breaches.** The three misses are *over*-refusals — `reject` where the corpus
  asks for `clarify` — and are **identical to Python's** result on the same corpus. The two
  runtimes now agree exactly on safety. Resolving them is the open S5 owner decision, not a port
  defect.

## A correction to the first run's write-up

The first report said its `avg_knaif_score` of 0.9827 was "flattered" by the marker defect. **That
was wrong, and the data here disproves it.** In run 1 all 39 `reverse_video` rows carried
`knaif_score = None` — they were `error` outcomes, which the scorer already excludes — so the
quality average was computed over the same population both times. What the marker defect corrupted
was **coverage** (reported 1.0000, truly 0.9705), not the score. The mechanism the plan warns
about is real; it simply did not bite here, because the rows were excluded as errors rather than
as capability gaps. Coverage was the lie, and coverage alone.
