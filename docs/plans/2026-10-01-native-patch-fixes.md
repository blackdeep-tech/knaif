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
First locate where the backslash is lost or rejected. The RED test comes from the known issue's
wording. If the cause is shared with path-separator normalization (`normalize_path_separators`,
a port of Python's rule), confirm that Python behaves differently before changing it.

### - [x] B6 — A missing `core_tools.yaml` is an error, not a silent drop

Found 2026-10-01: a binary that cannot find `contracts/runtime/core_tools.yaml` loads the skill
without the core tools (`PlanSession::new`), so every `reject`/`clarify` fails as
`Unknown tool: "reject"`. Seen only with a dev binary run outside the install layout, but the
failure mode is silent. Make it a startup error that names the file and where it was looked for.

### - [ ] B7 — (optional) Combining character after a file name

*Not done in this pass — left as a known issue unless the owner wants it.*

1.2.0 known issue: a rare combining character (Unicode Other_Alphabetic, e.g. U+0345) right after a
file name stops the name being recognized in native. Do it only if the fix is local to native
tokenization and the L2 cases stay at 100%. Otherwise leave it as a known issue.

### - [ ] B8 — Verify

- `just check-native`, `just test-native`, `just check-contracts` (L1/L2 at 100%), `just check`.
- Patch gate: L4 sampled for both skills on the packaged binary, clean-room install, upgrade from
  1.2.0.
- At freeze, remove the fixed items from the CHANGELOG *Known issues* and add them to 1.2.1's
  *Fixed*.

## Not in this plan (moved to 1.3.0 or later)

- "The second frame" planned as the first: likely needs an argument or prompt change. Confirm
  before 1.3.0 scoping.
- The clarify message that names an internal argument (`I can't inspect document with output_key`):
  its wording is in the parity contract (`contracts/parity/nl_clarify_gate_cases.json`).
- The Python runtime finding supporting tools on PATH only; tool version detection.
- `knaif-qwen3-1.7b-v2` slice gaps (needs a model).
- A file name ending in a dot (`clip.`): follows Python 3.14's rule. Not a native defect.
