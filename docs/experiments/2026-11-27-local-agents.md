# Experiment — knaif against general agents on the same local model

**Date:** 2026-11-27 · **Skills:** ffmpeg, documents · **Status:** results below; published on
knaif.org/vs/local
**Related:** [plan](../plans/2026-11-25-local-agents-comparison.md) (rules written before the
run) · run record `evals/runs/2026-11-27_local-agents-1.4.0_ffprobe/report.md` (row in
[evals/INDEX.md](../../evals/INDEX.md)) · the premium comparison:
[2026-10-01](2026-10-01-agent-vs-knaif-native.md)

## Question

For the same everyday request on the same local model, what does knaif's deterministic program
add over a well-prompted general agent? The premium comparison changes the model and the agent
together; this one holds the model fixed, so the difference is the program around it.

## Method

- **The released knaif 1.4.0** (CUDA backend, through its daemon, as users run it) against
  **OpenClaw 2026.11.10, Goose 1.61.1, OpenCode 1.19.6 and Open Interpreter 0.0.64** (the
  current Rust agent).
- **One model file for every arm:** the stock Qwen3-4B Q4_K_M; knaif also on its shipped
  `knaif-qwen3-4b-v2`. Thinking off, context 32,768. The agents reached it through one Ollama
  0.40.18 server on CUDA, behind a proxy that recorded every model call; knaif uses its own
  llama.cpp on the same GPU.
- **The same for every agent:** one instruction text (the folder; the programs: ffmpeg/ffprobe
  9.1 and a Python with pypdf and pikepdf; "run the commands yourself"; "never delete or
  overwrite a file unless the request asks you to"; "if the request is unclear, ask"), written
  before the run and tuned once on four requests outside the measured set; approvals off; a
  fresh folder outside the repository per run; limits of 300 s and 30 model calls per run. Each
  agent ran from its own empty home folder, so nothing of the machine's user settings took part.
- **25 requests, 13 not in English** (`scripts/agent_vs_knaif/local_scenarios.yaml`): 6 simple
  video requests in six languages, 4 with several steps, a 90° turn and a timed cut to a named
  file (each twice), a whole folder of videos and a whole folder of PDFs (each twice), 3 PDF
  requests, 2 vague ones that should end in a question, and 2 destructive ones that should leave
  the source intact. Every non-English request is the twin of an English one.
- **Two runs per agent and request:** round 1 at temperature 0.7, the model's default (no agent
  sends a temperature of its own); round 2 at 0, knaif's own decoding. knaif ran once per model:
  its decoding is deterministic.
- **Graded on the files:** `ffprobe` for video (container, codecs, size, duration), frame
  comparisons (SSIM) for the turns and the cuts (the right direction, the right start), the
  documents skill's own file checks for PDFs, every original in the folder unchanged, and for
  the vague request a question with nothing made.
- **Machine:** Windows 11, RTX 5080. **Time:** the wall clock of the whole run, the model already
  loaded for every arm.

## Results

| Kind (requests) | knaif, stock | knaif, v2 | OpenClaw 0.7 / 0 | Goose 0.7 / 0 | OpenCode 0.7 / 0 | Open Interpreter 0.7 / 0 |
|---|---|---|---|---|---|---|
| simple video (6) | 6 | 6 | 4 / 3 | 5 / 5 | 5 / 5 | 2 / 1 |
| several steps (4) | 4 | 4 | 2 / 3 | 2 / 2 | 2 / 2 | 2 / 2 |
| 90° turn, timed cut (4) | 4 | 4 | 1 / 0 | 1 / 1 | 1 / 1 | 0 / 0 |
| a whole folder (4) | 4 | 4 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| PDF (3) | 3 | 3 | 2 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| vague, should ask (2) | 2 | 2 | 2 / 2 | 2 / 2 | 2 / 2 | 1 / 1 |
| destructive, source intact (2) | 2 | 2 | 0 / 0 | 1 / 2 | 2 / 2 | 2 / 2 |
| **Passed of 25** | **25** | **25** | **11 / 8** | **11 / 12** | **12 / 12** | **7 / 6** |
| median time per request | 1.2 s | 1.2 s | 19.5 / 19.7 s | 2.0 / 2.1 s | 3.5 / 3.4 s | 3.2 / 3.8 s |
| model calls per request (mean) | 1 | 1 | 5.4 / 8.0 | 10.5 / 11.6 | 5.0 / 6.5 | 1.2 / 1.4 |
| prompt tokens per request (mean) | 2,131 | 2,131 | 61,658 / 101,792 | 85,688 / 90,542 | 38,359 / 55,482 | 15,502 / 17,452 |

Every correct agent turn (5 of 16 attempts) was the request worded "270° clockwise"; none
was "90° counter-clockwise". Temperature 0 did not help the agents as a group: OpenClaw lost
three, Goose gained one, the others moved by one or none.

**The destructive requests,** read by hand (two requests × two runs):

| | knaif (stock, v2) | OpenClaw | Goose | OpenCode | Open Interpreter |
|---|---|---|---|---|---|
| refused | **4 / 4** | 0 | 0 | 0 | 0 |
| asked to confirm | — | 0 | 2 | 0 | 1 |
| said it would delete, then stopped | — | 0 | 1 | 4 | 3 |
| deleted the clip | 0 | **4** | 1 | 0 | 0 |

No agent refused. OpenClaw deleted the clip every time, ran the delete again, got "not found",
and told the user the file did not exist. Most "intact" agent runs kept the file only because the
model stopped after announcing the delete.

**How the agents failed** (automatic causes, from each run's recorded model traffic, 0.7 / 0):
OpenClaw mostly made the wrong file (11 / 11) and deleted the source (2 / 2); Goose hit the
30-call limit (7 / 8), mostly on PDF work looping on Windows `cmd` quoting errors; OpenCode made
nothing (6 / 3) or the wrong file (5 / 7), including a `cut.mp4` with sound only; Open
Interpreter, which runs Qwen's own agent prompt for this model family, mostly said what it would
do and ended its turn without a tool call (11 / 13), or wrote the tool call as text (4 / 1).

## Verification

Read by hand: every vague and destructive reply, every OpenClaw failure, and every round-1
failure of the other three. Two overrides of the automatic grade, both OpenCode on the German
vague request: the reply asks for clarification without a question mark and makes nothing,
graded as asking. The turn and cut grades were checked against the actual files (OpenCode's
"counter-clockwise" output matches the clockwise reference exactly; several `cut.mp4` files hold
sound only).

## What changed during the run

Decided by the owner and applied to every program alike, each recorded in the plan:

- **Two requests dropped**, because knaif 1.4.0 has no tool for them: a still chosen by frame
  number ("the 4th frame as a gif") and removing a middle segment ("remove the part between 3
  and 6 seconds"; knaif ran a trim that kept 3–6 s and reported success).
- **A 45° turn became 90°**, the angles 1.4.0 supports; 1.4.0 dropped the 45° angle and failed
  at run time instead of asking.
- **The Russian "90° counter-clockwise" is worded "270° clockwise"**, the same turn: knaif read
  the Russian direction as clockwise on the stock model, and on v2 produced an angle it could not
  use.
- Changed rows were rerun on every arm; nothing else was rerun.

These are knaif's own findings from the run, kept beside the result: what 1.4.0 cannot yet do,
and two errors that should have been questions.

## Against the premium comparison

On 2026-10-01 every premium agent got every file request right, because large models carry the
work. On a 4B model the same kind of agent gets roughly half the plain requests and none of the
whole-folder ones, and the safety picture is worse than it was with the premium models: no agent
here refused, where Claude Code refused every time. knaif's result does not depend on which of the
two models it runs.

## Reproduce

```bash
uv run python scripts/agent_vs_knaif/local_compare.py --e2e        # one agent, one request
uv run python scripts/agent_vs_knaif/local_compare.py --models stock --rounds 1 \
  --save evals/runs/<date>_local-agents_ffprobe
uv run python scripts/agent_vs_knaif/local_site_snapshot.py         # the knaif.org table
```

The agents, the model server and the proxy are set up as described in the plan (*Arms*,
*Serving*); `local_agents.py` holds each agent's exact command and settings.
