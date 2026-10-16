# Daemon plan-equality check — 2026-10-06

The daemon-mode gate ([daemon-mode](../../../docs/plans/2026-10-01-daemon-mode.md), D4): a plan
produced through the model daemon must be byte-identical to the in-process plan.

- **Build:** `feat/1.3.0` at 545afb6 plus the docs-only change that records this run; `release-cuda`
  (Windows 11, RTX 5080), `knaif-qwen3-4b-v2`.
- **Method:** `knaif plan --skill <skill> --batch <skill>.utterances.txt --json` over every utterance of
  `skills/<skill>/data/eval.jsonl`, once with `KNAIF_NO_DAEMON=1` (in-process) and once with a daemon
  started by `knaif daemon start` (1,039 requests served, including the repair retries).

| Skill | Utterances | In-process | Through the daemon | Result |
|---|---:|---:|---:|---|
| ffmpeg | 861 | 401 s | 446 s | **identical** (sha256 `3f99d45ce4743728…`) |
| documents | 164 | 44 s | 51 s | **identical** (sha256 `ed9ed67596bcc0a4…`) |

One ffmpeg line is an `error` envelope in both runs (line 132, the German utterance
"clip.mp4 auf 4K hochskalieren": the model emits invalid JSON) — model behaviour, identical on both
paths. Batch timings are not a speed measurement: `plan --batch` already loads the model once, so the
daemon only adds the socket round-trip here; the per-run saving is measured in the plan (2.64 s → 0.70 s).
