# CLI terminal output — a readable run, with timings

**Status:** Active · **Created:** 2026-10-01 · **Completed:** —
**Owner:** native CLI · **Ref:** [release-1.2.1](2026-09-30-release-1.2.1.md) · `apps/cli/src/main.rs`
**Release:** 1.2.1

> **Status note:** implemented 2026-10-01 on `fix/1.2.1` (uncommitted until the owner says so);
> what is left is T7's manual and measured checks, run by the owner. Deviations from the draft
> below: T2 is a `ui` module of pure renderers plus direct calls from `cmd_run`, not an `Event`
> enum (the renderers are tested without a terminal either way); the stderr silencing and the
> spinner on a private stderr copy apply only in the terminal view; `KNAIF_VIEW=rich|plain` is an
> environment override (not a flag) so the tree can be tested through a pipe; `✓` lines are
> relative paths only where the plain view's line is not pinned. Scope agreed with the owner 2026-10-01. The logo is drafted and shown to the
> owner, who decides on it after seeing it (T6). The owner tests the llama.cpp leak fix (T4) on
> their machines after the work is done; it was not reproduced on the dev box (see *Findings*).

**Goal:** In a terminal, `knaif run` reads as a flowchart of what happens, with the ffmpeg command,
a user-facing error, colors and a timer per phase. Piped output keeps every line that scripts and
the eval lane read.

---

## Decisions (owner, 2026-10-01)

1. **Show only what knaif means to show.** That is the plan, each step, the ffmpeg command it runs,
   the result, and a user-facing error. llama.cpp's own output (load trace, warnings such as an
   unused context, backend banners) appears only under `--verbose`.
2. **The ffmpeg command stays visible.** It is part of what the user is told, not internal detail.
3. **Errors are curated.** The default shows one sentence that names the cause. `--verbose` shows
   everything: the full error chain, the whole ffmpeg stderr and llama.cpp's trace.
4. **Steps are drawn as a flowchart** with `├─ │ └─` lines.
5. **Timers** in `x.xxx s` for planning (split into model load and inference) and for each step,
   and a total at the end that **excludes time spent waiting for the user** at a prompt.
6. **Colors**, which `NO_COLOR` turns off.
7. **Logo:** a text version of the wordmark, drafted and shown. The owner decides on it after seeing it.

## Owner's test round 1 (2026-10-01) and what changed

- **Logo:** the first draft was wrong. The real wordmark is `kn[AI]f` with a coral `[AI]`
  (`site/shared/assets/wordmark.svg`). A half-block raster of the SVG came next; it looked right in
  PowerShell but stretched and striped in the Ubuntu VS Code terminal, and filled `#`/`█` variants
  were worse. The approved version is plain ASCII (see T6).
- **CPU warning:** and the CUDA tips are framed and colored (`render_box`) in the terminal view; the
  plain view keeps its stderr text.
- **Empty line at the end** of every terminal-view command: `run`, help,
  `--version`, usage errors, `skills`/`models`/`backend`/`plan` and their errors (`end_view`).
- **ffmpeg progress:** the terminal view runs ffmpeg with `-progress pipe:1 -nostats` (not shown in
  the command line) and draws `⠋ 0:15 / 1:00 ███░░░ 25% · 2.4x · 1.234 s` in place; the length comes
  from probing the first input. The plain view still uses a plain wait.
- **Overwrites:** the terminal view warns `⚠ X already exists and will be replaced`. The behavior
  itself is unchanged and is a 1.3.0 question (see the release index, *Deferred*).

## Constraints (patch lane)

- **No new CLI flag.** The rich view turns on by detection, never by a flag. `NO_COLOR` is the
  common environment convention, not a knaif option. `--verbose` already exists and only gains output.
- **Rich view only when stdout and stderr are both terminals.** Otherwise (a pipe, a redirect, the
  eval lane's subprocess) output stays in today's plain form.
- **Lines the plain form must keep byte-identical:** `running: <argv>` (stderr), `clarify: …`,
  `reject: …`, `✓ <output>`, `✗ <output> …`, `✓ wrote …`, the `--dry-run` command lines on stdout,
  and the `not_implemented:` prefix. `python/core/knaif/evalsuite/native_lane.py`
  (`parse_run_output`, `_RUNNING_RE`) reads them. A bug fix may still change the wording of a plain
  *diagnostic* line that nothing parses (e.g. the GPU messages below).
- **No new dependency with a new licence.** Colors use `anstream`/`anstyle`, already in the
  dependency tree through `clap`; they handle Windows VT and `NO_COLOR`. The spinner stays on
  `indicatif`.
- **Native only.** The Python `knaif.app` CLI is not changed in this release.

## Target (rich view)

```
 knaif · ffmpeg · knaif-qwen3-4b-v2
 │
 ├─ Planning ································ 1.842 s
 │    model load 0.913 s · inference 0.929 s
 │
 ├─ Plan · 2 steps
 │   ├─ 1  extract_audio    clip.mp4 → clip_audio.mp3
 │   └─ 2  convert_video    clip.mp4 → clip_converted.gif
 │
 ├─ Step 1/2 · extract_audio
 │   │  $ ffmpeg -y -i clip.mp4 -vn -c:a libmp3lame -b:a 128k clip_audio.mp3
 │   └─ ✓ clip_audio.mp3 ························ 0.412 s
 ├─ Step 2/2 · convert_video
 │   │  $ ffmpeg -y -i clip.mp4 -vf fps=10,scale=-1:480:flags=lanczos -an clip_converted.gif
 │   └─ ✗ ffmpeg cannot write here: permission denied (clip_converted.gif)
 │
 └─ Stopped at step 2 of 2 · 1 file written · total 1.731 s
```

Clarify / reject end the tree in yellow `?` / red `⊘` with a plain sentence. A confirmation prompt
sits inside the step (`│  Run this command? [y/N]`), and the time it waits is not counted. Step
labels are the tool name in readable form. Human summaries would need `tools.yaml` wording, which is
a contract change and out of scope for the patch.

## Findings (2026-10-01, dev box: Windows 11, RTX 5080, 1.2.0 builds)

- The CUDA, Vulkan and CPU builds with `knaif-qwen3-4b-v2` printed **no** llama.cpp lines without
  `--verbose`. `void_logs` + `send_logs_to_tracing(..with_logs_enabled(false))` cover the
  `llama_log_set`/`ggml_log_set` paths. Anything the owner sees therefore comes from something that
  bypasses the log hooks: a backend library writing to stderr directly (`fprintf`/`std::cerr`), or
  a driver. Unconfirmed; the owner tests after T4.
- The CPU build on that GPU machine printed `⚠ No GPU detected — running on CPU` **and**
  `ℹ NVIDIA GeForce RTX 5080: CUDA offload is available` in the same run. The two lines contradict
  each other.
- Documents' read results print as debug text: `pdf: 0 page(s), 3238 bytes, encrypted=true,
  text_layer=false`, and matches as `p1 [12..20]: …`.
- `✓ wrote` prints an absolute path for documents, while ffmpeg's `✓` line is relative.

## Tasks

TDD throughout: the rendering is pure functions over a small event model, tested without a
terminal; the I/O layer only chooses the view and writes.

### - [x] T1 — Lock the plain form first

Add a golden test of the plain output for a representative set: single ffmpeg step, ffmpeg chain,
dry-run, clarify, reject, documents write, documents read, a failing step. Run it through the mock
backend. It must pass on today's code before anything else changes, and keep passing after every
later task. Also run `parse_run_output` against the golden output so the eval-lane contract is
under test, not just assumed.

### - [x] T2 — Event model and renderer

- An `Event` enum covering everything `cmd_run` reports: planning started/finished (with load and
  inference durations), plan summary, step started, command, prompt, step result (ok / failed with
  curated message), control outcome (clarify / reject / done), notes (renames), finish (with total).
- `render_plain(&Event) -> String` reproduces today's lines exactly (T1 proves it).
- `render_rich(&Event, &Style) -> String` draws the tree; `Style { color: bool }`.
- `cmd_run`, `execute_plan`, `run_step`, `run_ffmpeg_step` and `run_documents_step` emit events instead of
  calling `println!`/`eprintln!` directly.
- View choice: rich iff `stdout.is_terminal() && stderr.is_terminal()`. Color iff rich and
  `NO_COLOR` unset (anstream's own detection).

### - [x] T3 — Timers

- Measure model load and inference separately. The `KNAIF_TIMING` hook in
  `native/crates/knaif-llm/src/llama.rs` already times `load_from_file`, so expose the durations
  from the backend instead of printing them. The mock reports zero load.
- Time each step's execution from its start to its result, excluding any prompt.
- Track the total prompt-wait time (`ask_yes_no`, the model-download question) and subtract it
  from the wall-clock total.
- Format: `x.xxx s` (three decimals, seconds). Unit tests on the formatter and on the subtraction.
- The live spinner during planning (`thinking_spinner`) shows elapsed time in the same format
  instead of `[00:00:12]`.

### - [x] T4 — Silence llama.cpp outside `--verbose`

- Keep the existing log hooks.
- Add a stderr guard for the planning window (backend load, model load, inference). Without
  `--verbose`, point the process's stderr at the null device and restore it afterwards. On Unix
  that is `dup`/`dup2` on fd 2. On Windows, apply it to the C runtime's fd 2 (`_dup`/`_dup2`) and the
  `STD_ERROR_HANDLE`. Verify it against the CUDA, Vulkan and CPU backend DLLs, which may carry their
  own CRT.
- **Trap:** the spinner and any knaif message in that window also write to stderr. Make the spinner
  draw to a duplicate of the original stderr handle taken before the redirect, and route
  knaif's own messages the same way.
- A failed load or inference must still surface: the error is a Rust `Result`, not stderr text,
  so it is unaffected. Under `--verbose` there is no guard at all.
- Owner test after the work is done: the machines/backends where warnings were seen.

### - [x] T5 — Curated errors and `--verbose`

- Rich view: an error is one sentence naming the cause, in the tree, not anyhow's `Error: …
  Caused by:` chain. `--verbose` prints the full chain after it.
- ffmpeg failures: the cause line comes from the fix plan's B3 (shared helper). `--verbose` prints
  ffmpeg's whole stderr, not the last three lines.
- Fix the contradictory GPU messages: print one line that is true for the build. For a CPU-only
  build on a machine with a GPU, say the build has no GPU backend and how to get one; never
  "No GPU detected".
- Documents read results in the rich view: `sample.pdf — PDF, 3 pages, 12 KB, has text layer`,
  `page 1: …snippet…`. Relative paths in every `✓` line in the rich view. The plain `✓ wrote` line
  keeps its absolute path, since T1 locks it.

### - [x] T6 — Logo (owner decides after seeing it)

- Draft a 2–3 line text-art version of `site/shared/assets/wordmark.svg` (lowercase *knaif*), in the
  rich view only, in color when color is on.
- Proposed placement: bare `knaif`, `knaif --help`, and the first-run welcome; never on `run`.
- Show the owner a screenshot in Windows Terminal, conhost and a Linux terminal. Ship it, move it,
  or drop it on the owner's call; record the decision here.
- **Decision (owner, 2026-10-01): ship it.** `LOGO` in `apps/cli/src/ui.rs` is figlet-style plain
  ASCII, 6 rows, `kn` and `f` in the terminal's foreground and `[AI]` in coral, on bare `knaif` and
  `--help` in the terminal view only. Plain ASCII because it does not depend on the font or the
  cell shape; a unit test keeps it ASCII-only.

### - [ ] T7 — Verify

- `just check-native`, `just test-native`, `just check-contracts`, `just check`.
- T1's golden test unchanged.
- Patch gate: L4 sampled on both skills through the packaged binary. The lane runs piped, so this
  proves the plain form held.
- Manual: rich view in Windows Terminal, legacy conhost, VS Code's terminal, WSL/Linux; with
  `NO_COLOR=1`; and with output redirected to a file (plain).
- Owner: T4 on the machines where the warnings were seen, and T6's decision.
- **Open (owner's test round 2, 2026-10-01): VS Code's PowerShell terminal only.** After answering
  `y` at a step that prints a warning before `Proceed? [y/N]` (`reverse_video`), the warning and
  the prompt line vanished and the typed `y` ended up after the command line. Not reproduced in a
  ConPTY at 120 or 160 columns, nor in a Linux pty (both render correctly), so it looks specific to
  VS Code's terminal. Needs a screenshot and the terminal width to go further.
