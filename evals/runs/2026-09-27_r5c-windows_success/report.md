# R5c Windows, first attempt — stopped after 1.5 h; L3 failure diagnosed and fixed

**Plan:** [release 1.2.0](../../../docs/plans/2026-09-25-release-1.2.md) R5c · **Date:** 2026-09-27 ·
**RC:** `3077d2f` (driver committed as `8e97f50`) · **Artifact:** unpacked
`dist/knaif-1.2.0-windows-x64.zip` (sha256 `006aa396…`), `KNAIF_PDFIUM_PATH` unset, installed CUDA
payload · **Rules:** `run_all.sh`, written before the run

The owner stopped the run at 19:28: it had been launched as a ~13 h sequence without the time budget
shown first. The results it produced before the stop:

| Entry | Result |
|---|---|
| L3 4B documents | **PASS**: 0 port bugs, 0 missing capabilities, plan disagreement 0.70% (bound 1.83%) |
| L3 4B ffmpeg | **FAIL**: 4 port bugs, plan disagreement 11.92% (bound 4.11%) |
| L4 4B CUDA ffmpeg | **ACCEPTED**, 45/45 thresholds, placement `CUDA0` |
| L4 4B CUDA documents | **ACCEPTED**, 42/42 thresholds, placement `CUDA0` |
| L4 4B Vulkan ffmpeg | void: the stop killed it mid-run |

## Diagnosis of the L3 ffmpeg failure (2026-09-28)

**The 11.9% disagreement came from the Python CLI, not the model or the port.** On all 36 rows, the
parity run's native plans equal the native CUDA L4 plans; its Python plans equal the Python eval
lane's on only 5. The Python eval lane and native disagree on 1.97% of ffmpeg decisions over the
full corpus (17 of 861; documents 1.83%). Cause: `knaif-cli run` and `plan` called `agent.infer`
without the retrieved registry, so the model saw every tool, a prompt no lane measured, and the one
the published `knaif-cli` has been shipping. Fixed in `b03c686`; re-planned, 33 of the 36 now agree
with native and 35 with the Python eval lane.

**Of the 4 port bugs, one was real:**
- `ffmpeg_209` "cut just the end 2 seconds": native added `-t 2` after `-sseof -2` (end 0 read as a
  2 s span). Same output, different argv. Native now matches Python (`6a826fb`), with a contract case.
- `ffmpeg_293`, `ffmpeg_294` (aspect crop): the comparator's tokenizer turned the filter's `\,`
  escape into `/,` on one side and `//,` on the other. Identical commands.
- `ffmpeg_140` (trim → reverse): Python's dry-run stops at `reverse_video`'s preview confirmation and
  renders only the trim; native renders both.

All four rows execute correctly on the native binary (L4 above, knaif score 1.0 on every utterance).

**The comparator is rebuilt** (`62f7cc1`, `69622e9`): both CLIs dump each ffmpeg argv as JSON under
`$KNAIF_DUMP_PLAN` and L3 compares that, not display lines. Found by a Codex audit of the first
comparator fix: Python quotes nothing, so the corpus's `silent clip.mp4` (`ffmpeg_288`) could not be
compared from its display line, and any backslash rule broke either a path or a filter escape. A
chain one side renders in part is compared on the steps both rendered.

## Known differences, not fixed here

- **`reverse_video` UX**: Python renders a 10 s preview and asks twice (the second warns that
  reversing buffers the whole clip in RAM); native shows its single generic confirmation. The output
  is the same; the warning is Python-only.
- **Non-finite trim bounds** (Codex, low severity): with `start=-2, end=NaN`, Rust reads the end as
  "not positive" and drops it; Python keeps `-ss -2 -to NaN`. No model emits `NaN`; both parsers
  should reject non-finite timestamps.

## What happens to these results

The fixes change `python_core`, the native binary and `contracts`, so under the rerun rule every
L3/L4 entry above is stale and re-runs after the re-freeze (plan T6–T8): L3 for both skills (4B),
and the 4B CUDA cell. Nothing here is reused.
