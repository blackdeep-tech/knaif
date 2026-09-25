# Cross-backend check — v2 on the CPU, Vulkan and CUDA native builds

L4 (`../2026-09-24_l4-v2-final_success/`) accepted v2 on the CUDA build only; a release also ships CPU and
Vulkan kinds, and a backend can flip greedy near-ties. All three kinds rebuilt and freshness-checked, code
`ec6e681`, local `knaif-qwen3-4b-v2` GGUF, capped at 8 CPU threads (owner's CPU runs hot under full load;
8/8, 6/6 and 4/4 threads gave byte-identical plans). Tensor placement measured, not assumed: CUDA0 37,
Vulkan0 37, CPU 37 layers. Rule fixed in `run_all.sh` before the run.

## Results

| kind | safety (binary) | ffmpeg decision flips vs CUDA (bound 35/851) | documents (bound 3/164) |
|---|---|---|---|
| CUDA, repeated | 11/11, 9/9 | **0** | **0** |
| Vulkan | 11/11, 9/9 | 29 — within | 4 — over → full L4 |
| CPU | 11/11, 9/9 | 37 — over → full L4 | 1 — within |

Zero flips on the CUDA repeat: a native build is deterministic, so every CPU/Vulkan flip is the backend.

**Vulkan documents full L4: ACCEPTED 42/42** — outcome 0.9756 (CUDA 0.9817, bar 0.9556, Python 0.9756),
knaif 0.9967 (CUDA 0.9946, bar 0.9800), coverage 1.0. The acceptance record was restored to the CUDA
evidence afterwards.

**CPU ffmpeg full L4: PENDING** (~4 h at 8 threads; owner deferred it). Until it runs, the CPU build does
not ship with v2 under this rule. Of the 37 CPU flips, 31 are rows CUDA got right and 6 rows it got wrong.
About half are equivalent spellings (`h265`/`hevc`, `0:00:03`/`00:00:03`, a dropped redundant `quality`);
some look worse (`ffmpeg_174#1` compresses to 500 MB instead of extracting mp3 at 500k; `ffmpeg_118#1/#2`
email instead of WhatsApp; `ffmpeg_085b#0` resizes where CUDA asked), a few better (`ffmpeg_086#2`,
`ffmpeg_134#2`, `ffmpeg_203#3`). Rough expectation ~0.920 against the 0.9177 bar — too close to call,
which is why it needs the real run. Splicing the flipped rows into the CUDA L4 is not exact: today's
CUDA `plan --batch` and yesterday's CUDA L4 (`run` per utterance) differ on 5 of 851 decisions, so the
two paths are not interchangeable.

## Prediction

- CUDA repeat 0 flips — **held**.
- Vulkan and CPU well under the ffmpeg bound, 0–1 documents flips — **missed**: Vulkan ffmpeg 29 (within),
  CPU ffmpeg 37 (over), Vulkan documents 4 (over; its full L4 then passed).
- safety 100% everywhere — **held**.

## Run defect

The first pass of the fallback stage died in a second: `FALLBACK` was written with CRLF on Windows and
`read` kept the `\r` (`Corpus not found: skills\documents\data\eval.jsonl`). Fixed in `run_all.sh`
(LF write + CR strip); `fallback.sh <kind> <skill>` runs the stage on its own. Verified before any long
run: CPU ffmpeg smoke (3 utterances, lane + execution + result glob + `accept-native` identity), then the
Vulkan documents L4 end to end.

Pending: `bash evals/runs/2026-09-25_backend-parity-v2_plans/fallback.sh cpu ffmpeg`
