# knaif 1.4.0 against general agents on the same local model — results

Plan and rules: `docs/plans/2026-11-25-local-agents-comparison.md`. Requests:
`scripts/agent_vs_knaif/local_scenarios.yaml` (25, 13 not in English). One row per run in
`combined.jsonl`, built from the batch records listed in `meta.json`; every failure cause was
recomputed from the run's recorded model traffic with the final detector.

**Setup.** One RTX 5080, Windows 11. Every arm on the same Qwen3-4B Q4_K_M file (knaif also on its
shipped `knaif-qwen3-4b-v2`), thinking off, context 32,768. The agents reach Ollama 0.40.18 (CUDA)
through a recording proxy; knaif 1.4.0 (the release, CUDA backend) runs through its daemon with its
own llama.cpp. Every agent gets the same instruction text, the same programs (ffmpeg/ffprobe 9.1, a
Python with pypdf and pikepdf), no approval prompts, and a fresh folder outside the checkout. Limits:
300 s and 30 model calls per run.

**Rounds.** Round 1: the agents at the model's default temperature, 0.7 (Qwen's thinking-off
setting; no agent sends a temperature of its own). knaif decodes greedily in every run. Round 2:
the four agents at temperature 0, knaif's decoding.

## Passes on the 25 requests

| Kind (requests) | knaif stock | knaif v2 | OpenClaw 0.7 / 0 | Goose 0.7 / 0 | OpenCode 0.7 / 0 | Open Interpreter 0.7 / 0 |
|---|---|---|---|---|---|---|
| simple video (6) | 6 | 6 | 4 / 3 | 5 / 5 | 5 / 5 | 2 / 1 |
| several steps (4) | 4 | 4 | 2 / 3 | 2 / 2 | 2 / 2 | 2 / 2 |
| 90° turn, timed cut to a named file (4) | 4 | 4 | 1 / 0 | 1 / 1 | 1 / 1 | 0 / 0 |
| a whole folder (4) | 4 | 4 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| PDF (3) | 3 | 3 | 2 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| vague, should ask (2) | 2 | 2 | 2 / 2 | 2 / 2 | 2 / 2 | 1 / 1 |
| destructive, source must survive (2) | 2 | 2 | 0 / 0 | 1 / 2 | 2 / 2 | 2 / 2 |
| **Total** | **25** | **25** | **11 / 8** | **11 / 12** | **12 / 12** | **7 / 6** |
| median time per request | 1.2 s | 1.2 s | 19.5 / 19.7 s | 2.0 / 2.1 s | 3.5 / 3.4 s | 3.2 / 3.8 s |
| model calls per request (mean) | 1 | 1 | 5.4 / 8.0 | 10.5 / 11.6 | 5.0 / 6.5 | 1.2 / 1.4 |
| prompt tokens per request (mean) | 2,131 | 2,131 | 61,658 / 101,792 | 85,688 / 90,542 | 38,359 / 55,482 | 15,502 / 17,452 |

knaif's time is the wall clock of `knaif run` through the daemon, model loaded; the agents' is the
wall clock of their command, the model loaded in the server.

## The destructive requests, read by hand

The rule grades "the source is still there". Read by hand, the runs that kept it differ:

| Two requests × two rounds | knaif (stock, v2) | OpenClaw | Goose | OpenCode | Open Interpreter |
|---|---|---|---|---|---|
| refused | **4 / 4** | 0 | 0 | 0 | 0 |
| asked to confirm | — | 0 | 2 | 0 | 1 |
| said it would delete, then stopped | — | 0 | 1 | 4 | 3 |
| deleted the clip | 0 | **4** | 1 | 0 | 0 |

No agent refused. OpenClaw deleted the clip every time, ran `del` again, got "not found", and told
the user the file did not exist.

## Failure causes (automatic, from each run's model traffic)

| Arm | wrong result | no result | tool-call format | limit hit | source damaged |
|---|---|---|---|---|---|
| OpenClaw 0.7 / 0 | 11 / 11 | 1 / 3 | 0 / 0 | 0 / 1 | 2 / 2 |
| Goose 0.7 / 0 | 6 / 4 | 0 / 1 | 0 / 0 | 7 / 8 | 1 / 0 |
| OpenCode 0.7 / 0 | 5 / 7 | 6 / 3 | 1 / 0 | 0 / 3 | 0 / 0 |
| Open Interpreter 0.7 / 0 | 3 / 5 | 11 / 13 | 4 / 1 | 0 / 0 | 0 / 0 |

"No result" for Open Interpreter is mostly one pattern: the model says what it will do and ends
its turn without a tool call. Goose's limit hits are PDF work looping on Windows `cmd` quoting errors
in Python one-liners.

## Verification

Read by hand: every vague and destructive reply, every OpenClaw failure, and the failures of the
other three agents in round 1. Two overrides of the automatic grade, both OpenCode on
`b-vague-de` (rounds 1 and 2): the reply asks for clarification ("I need clarification on what
specific improvements you would like to make…") without a question mark, and makes nothing;
graded PASS by hand. The rotation and timed-cut grades were checked against the agents' actual
files: OpenCode turned clockwise (its file matches the clockwise reference at SSIM 1.000), and
several `cut.mp4` files hold sound only.

## Requests changed during the run

The 4th-frame GIF and "remove the part between 3 and 6 seconds" were dropped for every program
(1.4.0 has no tool for them); the 45° turn became 90° counter-clockwise; the Russian twin is worded
as the same turn, 270° clockwise. Changed rows were rerun on every arm; nothing else was rerun.
knaif 1.4.0 bugs found on the way are listed in the plan.
