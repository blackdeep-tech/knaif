# Completion alerts and known folders

**Status:** Active · **Created:** 2026-10-26 · **Completed:** —
**Owner:** core · **Ref:** [url-inputs](2026-10-26-url-inputs.md) (builds on it);
`contracts/runtime/core_tools.yaml` (`alert_when_done`); `contracts/parity/known_folder_cases.json`
**Release:** 1.4.0

> **Status note (2026-10-26).** Built and measured on both runtimes with the shipped 4B model; a
> prompt-only change made it emit the alert and known-folder outputs. Left for the release:
> `eval_alert.jsonl` needs an acceptance slice in both skills' `acceptance.yaml` (numbers below;
> documents' margin is one utterance), the snapshots are re-locked once on the generation-1
> prompts, the known-folder lookup on macOS and Linux is exercised by the release evidence (ported,
> measured on Windows so far), the Windows installer's app-id registration ships with the
> release so the toast says knaif, and `docs/TOOL_SCHEMA.md` and `docs/EVAL_FRAMEWORK.md` still
> need their sections (the core tool's YAML, the skills' `SPEC.md` rows and the eval's
> `expected_alert` / `expected_folder` fields are the documentation today).

**Goal:** "Beep when done" ends the run with a desktop notification and a beep, and Downloads,
Desktop, Documents, Videos, Music and Pictures named in a request mean the user's real folders.

---

## What it does

- **The alert is a core control tool**, `alert_when_done` in `contracts/runtime/core_tools.yaml`.
  It enters the prompt only when its triggers match ("beep", "notify", "let me know", "when
  done", and their German, Spanish, French, Bulgarian, Russian and Chinese wordings), so every
  other request's prompt is byte-identical. At most one per plan; the pipeline removes it before
  execution and keeps it as a run option. It fires when work started and the run ended: success,
  failure, or a confirmation declined mid-run; never after a clarify or reject before anything
  ran, never on stdout, never in a dry run.
- **Backends, in core:** Windows `MessageBeep` plus a toast with a balloon fallback; macOS
  `osascript`; Linux D-Bus, else `notify-send`, else the terminal bell. No desktop: the bell on
  stderr. `KNAIF_OS_SINK=<file>` records alerts as JSON lines for tests and evals, both runtimes.
- **Known folders are a path class resolved by code**, in one plan-level rewrite: an output whose
  first segment is `Downloads`, `Desktop`, `Documents`, `Pictures`, `Music`, `Videos`, `home` or
  `~` resolves into the user's real folder (Windows `SHGetKnownFolderPath`, so a redirected
  OneDrive Documents is found; macOS `~/<Name>`; Linux `xdg-user-dir`). The model never writes an
  absolute path. If a folder of that name also exists next to the input, the run clarifies, naming
  both. Only outputs move; inputs keep their resolution. With `--sandbox`, a known folder outside
  it is refused. `KNAIF_KNOWN_FOLDERS_ROOT=<dir>` redirects them for tests and evals.
- **Prompt:** one example per skill, and one safety line saying a known folder the user named is
  not outside the sandbox.

## Bar and evidence

Fixed before measuring. The new rows are in `skills/<skill>/data/eval_alert.jsonl` (ffmpeg 10 rows,
documents 6, three wordings each), graded on the alert recorded in the sink with the right outcome
and the file delivered in the redirected folder, exact files clean.

| Bar | Result |
|---|---|
| New rows, executing verifier, shipped 4B | ffmpeg 0.967, documents 0.889 (bar ≥ 0.85 per skill) |
| False alerts on the existing corpora | 0 |
| Existing prompts unchanged | 0 of 1,117 utterances changed |
| Both regression gates | hold; aggregates unchanged |
| Safety | 100% on both skills |
| L1 / L2, both runtimes | 100%, with `known_folder_cases.json` and the alert tool's cases |
| Native L3 and mock replay | 0 port bugs, 0.0000 disagreement; 41 of 41 replays identical |

Re-measured 2026-10-25 on the final code with the shipped 4B: ffmpeg 0.967 / 1.0,
documents 0.889 / 1.0. Documents' two misses are the model dropping the alert; ffmpeg's one is "in
my Downloads folder" read as where the input is, an ambiguous wording kept on purpose.

## Carried with it

`windows-sys` gains the shell, COM and windowing features knaif-core needs on Windows. Release
notes: alerts are a desktop notification and a beep, and Do not disturb holds the banner.
