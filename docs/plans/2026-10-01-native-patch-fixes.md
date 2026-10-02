# Native patch fixes for 1.2.1

**Status:** Active · **Created:** 2026-10-01 · **Completed:** —
**Owner:** native CLI · **Ref:** [release-1.2.1](2026-09-30-release-1.2.1.md) · CHANGELOG 1.2.0 *Known issues*
**Release:** 1.2.1

**Goal:** Fix the native-runtime bugs from 1.2.0 that need no model, prompt, `tools.yaml` or
contract change, so the native binary behaves as the Python runtime already does.

---

## Selection rule

The patch rule decides what is in scope. A fix is in scope only if it changes no model, prompt
wording, `tools.yaml`, parity contract or CLI flag, and moves native *towards* the behavior Python
and the docs already describe. Everything else is listed under *Not in this plan*.

Each fix starts with a failing test (RED) that reproduces the reported behavior. A fix that
turns out to need a contract change stops there and is moved to 1.3.0, with the reason recorded
here.

## Tasks

### - [x] B1 — Declining a step stops the plan

1.2.0 known issue: in a chain, answering no at one step's prompt prints `Aborted (no changes made).`
and the next step runs anyway. Python stops. `run_ffmpeg_step` and `run_documents_step` return
`Continue` after an abort (`apps/cli/src/main.rs`, both `Aborted` sites). Return an outcome that
ends the plan, and say which steps did not run (reuse `chain_failure_context`'s wording style).
Test: a two-step mock plan, the first prompt declined, the second step never dispatched.

### - [x] B2 — `reverse_video` previews before confirmation

1.2.0 known issue: `reverse_video` asks without first showing what it will run. Find why its
preview path differs from the other ffmpeg intents (`skills/ffmpeg/native/src/run.rs`, the
`reverse_video` arm) and make it show the command like every other intent. Test: the preview list
handed to `confirm_action` is non-empty for `reverse_video`.

### - [x] B3 — A failed ffmpeg command names its cause

1.2.0 known issue: a failure shows the last three stderr lines and a raw exit status
(`0xfffffff3` for a folder it may not write to). The line naming the cause ("Permission denied")
can be cut off. Add a pure helper that picks the cause from ffmpeg's stderr: permission denied, no
such file, no space left, invalid data, unknown encoder, a fallback to the last meaningful line.
Turn it into one sentence. Use it in both views; the terminal-output plan (T5) renders it.
Keep the plain `✗ <output> (ffmpeg exited …)` prefix, which the eval lane reads. Test: recorded
stderr samples for each case, including the read-only-folder one.

### - [x] B4 — Encrypted PDFs say they are encrypted

Found 2026-10-01: on `sample-protected.pdf`, inspecting reports `0 page(s)` and rotating page 1
fails with `Page(s) out of range 1-0: [1]` (`skills/documents/native/src/pdf.rs`). Detect
encryption before page logic, and say the file is password-protected and must be unlocked first.
Check what Python does with the same input first: if it fails the same way, the fix covers both
runtimes or the item moves to 1.3.0. Test: both requests on the protected fixture.

### - [x] B5 — A password containing a backslash is accepted

1.2.0 known issue: native asks for the password again instead of accepting it; Python accepts it.

**Done in two parts.** First, native grounds the password against the normalized text, as Python
does, so it no longer asks again. Verification (2026-10-01) then found that *neither* runtime kept
the password: `password-protect sample.pdf with the password p\ss` planned `password: "p/ss"` on
both, because `normalize_path_separators` rewrites the token before the model sees it, so the file
would be locked with a password the user never typed. Fixed on both runtimes (owner, 2026-10-01):
after the clarify gate, each `grounded_args` value gets the user's own spelling back
(`restore_grounded_spelling` / `restore_grounded_args` in `prompt.py`, `nl_clarify_gate.py` and
`apps/cli/src/main.rs`). Paths keep their forward slashes. No prompt or contract change: the model
still sees the same text. Tests: `python/core/tests/test_grounded_spelling.py` and the matching
native tests; checked end to end on both runtimes.

### - [x] B6 — A missing `core_tools.yaml` is an error, not a silent drop

Found 2026-10-01: a binary that cannot find `contracts/runtime/core_tools.yaml` loads the skill
without the core tools (`PlanSession::new`), so every `reject`/`clarify` fails as
`Unknown tool: "reject"`. Seen only with a dev binary run outside the install layout, but the
failure mode is silent. Make it a startup error that names the file and where it was looked for.

### - [x] B7 — Combining character after a file name

1.2.0 known issue: a rare combining character (Unicode Other_Alphabetic, e.g. U+0345) right after a
file name stops the name being recognized in native.

**Done (owner, 2026-10-01).** `py_is_word` (`knaif-core/src/nl_clarify_gate.rs`) used
`char::is_alphanumeric`, which follows the Alphabetic property and so takes in Other_Alphabetic
marks; Python's `str.isalnum` counts letters and numbers by general category only. It now asks
the general category (`unicode-properties`, MIT/Apache-2.0, already in every build through
`lopdf`, so no new code or licence). Local to native tokenization; L1/L2 stay at 100%. Tests check
U+0345, U+093E, U+05B0 and U+0301 against Python's verdicts.

### - [x] B9 — Python batch requests fail when run for real

Found 2026-10-01 while testing the "one silent video fails the batch" candidate (which does not
reproduce: both runtimes skip a video without audio). Every real Python batch run ended in
`'*.mp4' not found in working directory`: `_preflight_inputs` (`skills/ffmpeg/python/_reporting.py`)
checked the planned glob as a file name. A glob now counts as present when it matches a file, by
the same rule `resolve_inputs` expands it with; only a request whose every input is a pattern
matching nothing is refused, with `no files match …`. Native was not affected. Owner: in 1.2.1.

### - [x] B10 — A declined step reads as declined in the terminal view

Found in the owner's test round 2 (2026-10-01): answering no printed `Aborted (no changes made).`
outside the tree and closed the run with `Done`. The terminal view now ends the step with
`└─ ⊘ Declined · no changes made` and the run with `Stopped at step N of M · you declined · …`.
The plain view keeps its 1.2.0 lines (golden test unchanged).

### - [x] B11 — No GPU, no "Vulkan already works here"

Found in WSL (2026-10-01): with no Vulkan device the run went to the CPU and printed both "No GPU
backend is active" and the optional CUDA tip saying Vulkan "already works here". The tip is chosen
by `cuda_offer_text`, which now knows whether this run's backend found a GPU; without one, the
payload is presented as the fix, as a warning.

### - [x] B8 — Verify

- `just check-native`, `just test-native`, `just check-contracts` (L1/L2 at 100%), `just check`.
- Patch gate: L4 sampled for both skills on the packaged binary, clean-room install, upgrade from
  1.2.0.
- At freeze, remove the fixed items from the CHANGELOG *Known issues* and add them to 1.2.1's
  *Fixed*.
- **Pre-freeze clean room and upgrade, 2026-10-01 — PASS, 16/16** (not the release gate, which
  reruns at freeze on the final build). 1.2.0's T12 harness, adapted: in Windows Sandbox (no GPU, no
  network, no developer tooling) the 1.2.1 zip runs; the published 1.2.0 installer (`c42ba04d…`)
  installs; 1.2.1 setup refuses while a 1.2.0 CLI holds the mutex, then upgrades in place (one
  Add/Remove row at 1.2.1, same folder, no folder-exists warning, no leftover library); the
  installed binary is the new one (by sha256: the Cargo version is bumped only at freeze); OCR
  through it with no `--model` produces the expected text; a PDF it locks with `p\ss` opens with
  `p\ss` and not `p/ss`. Static: no local paths in the staged tree, every PE import staged or
  Windows-provided. Harness in the gitignored `sandbox/r121/` (its `.wsb` names host paths).
- **Release gates on the frozen build (`7e39b45`), 2026-10-02 — PASS.** `just check` green; L4
  sampled on both skills through the packaged binaries, per OS on CUDA, identical to 1.2.0's
  accepted runs (Windows 47/47 + 31/31, Linux 47/47 + 31/31) and a Python stage identical to the
  accepted L3 rows; release clean room 24/24 with Smart App Control enforcing, upgrade from 1.2.0
  included. `evals/runs/2026-10-02_r121-sampled`, equivalence `1.2.1-sampled`.
- Found 2026-10-01 while rebuilding: the B2 memory warning and the B6 error had lost their `\`
  line continuations, so each sentence carried a run of spaces. Fixed; the B2 test now rejects a
  double space.

## Not in this plan (moved to 1.3.0 or later)

- "The second frame" planned as the first: likely needs an argument or prompt change. Confirm
  before 1.3.0 scoping.
- The clarify message that names an internal argument (`I can't inspect document with output_key`):
  its wording is in the parity contract (`contracts/parity/nl_clarify_gate_cases.json`).
- The Python runtime finding supporting tools on PATH only; tool version detection.
- `knaif-qwen3-1.7b-v2` slice gaps (needs a model).
- A file name ending in a dot (`clip.`): follows Python 3.14's rule. Not a native defect.
