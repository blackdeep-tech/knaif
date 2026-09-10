//! L2 / Workstream E: the ordered multi-step execution contract.
//!
//! **Every test here was `#[ignore]`d and red on purpose, and all four were verified red
//! immediately before E2.** E5 required the cases that pin ordered execution to exist and fail
//! *before* the executor was built — otherwise the executor would be certified correct by the
//! same change that introduces it, which is the pattern this plan exists to break. Un-skipped
//! 2026-09-10 with E2/E3; three went green on the executor alone, and the fourth needed its
//! *fixture* corrected (see the note on it) while its assertions stayed exactly as authored.
//!
//! No model is involved: `KNAIF_LLM_BACKEND=mock` plus `KNAIF_LLM_MOCK_RESPONSE` feeds the
//! runtime an exact plan, so these are deterministic pipeline tests (L2), not behavioral ones.
//! Before E2, native answered any multi-step plan with `not_implemented:` and ran nothing, which
//! is what each of these was written against.
//!
//! These execute `run --dry-run`, so the job that runs them needs ffmpeg on PATH for dependency
//! preflight. Today that is true locally and not in CI.
//!
//! See docs/plans/2026-09-10-skill-quality-lifecycle.md (Workstream E, L2a).

use std::process::Command;

/// Drive the CLI with a fixed plan and no model.
fn run_with_plan(plan: &str, utterance: &str) -> (String, String, bool) {
    let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["run", "ffmpeg", "--dry-run", utterance])
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", plan)
        .output()
        .expect("run the knaif binary");
    (
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
        out.status.success(),
    )
}

const TWO_STEP: &str = r#"{"plan": [
    {"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "silent.mp4"}},
    {"tool": "resize_video", "args": {"inputs": ["silent.mp4"], "height": 720, "output": "small.mp4"}}
]}"#;

#[test]
fn a_two_step_plan_previews_two_commands() {
    let (stdout, _stderr, ok) =
        run_with_plan(TWO_STEP, "strip the audio from clip.mp4 and resize it");
    assert!(ok, "a valid two-step plan must not fail");
    assert!(
        !stdout.contains("not_implemented:"),
        "native still refuses multi-step plans:\n{stdout}"
    );
    let commands = stdout.lines().filter(|l| l.contains("ffmpeg")).count();
    assert_eq!(commands, 2, "expected one command per step, got:\n{stdout}");
}

#[test]
fn steps_are_previewed_in_plan_order() {
    // The chain is file-mediated (prompt.yaml: "Never chain steps with $variable references"),
    // so order is observable: step 2 consumes exactly what step 1 declared it would write.
    let (stdout, _stderr, _ok) =
        run_with_plan(TWO_STEP, "strip the audio from clip.mp4 and resize it");
    let first = stdout.find("silent.mp4").unwrap_or(usize::MAX);
    let second = stdout.find("small.mp4").unwrap_or(usize::MAX);
    assert!(
        first < second,
        "the producing step must be previewed before the consuming one:\n{stdout}"
    );
}

#[test]
fn a_control_tool_short_circuits_the_whole_plan() {
    // A clarify is a statement about the request, not a step to run past. Anything after it
    // must not be previewed, whatever position it holds.
    let plan = r#"{"plan": [
        {"tool": "clarify", "args": {"question": "Which file?"}},
        {"tool": "resize_video", "args": {"inputs": ["clip.mp4"], "height": 720}}
    ]}"#;
    // The utterance names clip.mp4 so the clarify *gate* cannot fire — otherwise this would
    // pass without the executor being involved at all, which is worse than failing.
    let (stdout, _stderr, _ok) = run_with_plan(plan, "resize clip.mp4");
    assert!(
        stdout.contains("clarify:"),
        "expected the clarify:\n{stdout}"
    );
    assert!(
        !stdout.contains("ffmpeg"),
        "no step after a control tool may be previewed:\n{stdout}"
    );
}

#[test]
fn a_failing_step_stops_the_chain_and_says_which_steps_ran() {
    // A chain that fails at step 2 of 3 must read as neither total success nor total failure:
    // step 1 already wrote a file to the user's disk.
    //
    // **The step that fails changed when this was un-skipped, and the assertions did not.** As
    // authored, step 2 passed `"height": "not-a-number"`, on the assumption that a non-numeric
    // height would fail. It does not: `resize_video` declares no `arg_schema` for `height`, so
    // *both* runtimes accept the step and drop the argument — verified against Python's
    // `validate_plan`, which accepts it too. That is parity-correct behavior and a separate
    // question (an unschema'd arg is silently ignored); it is simply not a failing step, so the
    // case could never have observed what it was written to observe. `aspect` *is* validated at
    // expansion (`skills/ffmpeg/native/src/engine.rs`, `invalid_aspect_errors`), so it fails
    // where this case needs a failure.
    let plan = r#"{"plan": [
        {"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "silent.mp4"}},
        {"tool": "resize_video", "args": {"inputs": ["silent.mp4"], "aspect": "not-an-aspect"}},
        {"tool": "strip_audio", "args": {"inputs": ["resized.mp4"], "output": "final.mp4"}}
    ]}"#;
    let (stdout, stderr, ok) =
        run_with_plan(plan, "strip audio from clip.mp4, resize, strip again");
    let combined = format!("{stdout}{stderr}");
    assert!(!ok, "a chain with a failing step must not exit 0");
    assert!(
        combined.contains("step 2"),
        "the report must name the step that failed:\n{combined}"
    );
    assert!(
        !combined.contains("final.mp4"),
        "no step after the failure may run:\n{combined}"
    );
}

#[test]
fn done_ends_the_plan_without_reaching_a_skill() {
    // `done` is a control tool like the other two, and before E3 it was the one that leaked:
    // it fell through to the skill dispatch, where the only possible answer was "unknown tool".
    let plan = r#"{"plan": [
        {"tool": "done", "args": {}},
        {"tool": "resize_video", "args": {"inputs": ["clip.mp4"], "height": 720}}
    ]}"#;
    let (stdout, stderr, ok) = run_with_plan(plan, "resize clip.mp4");
    let combined = format!("{stdout}{stderr}");
    assert!(ok, "a done plan is a success, not an error:\n{combined}");
    assert!(
        !combined.contains("ffmpeg"),
        "no step after `done` may be previewed:\n{combined}"
    );
    assert!(
        !combined.to_lowercase().contains("unknown tool"),
        "`done` reached a skill dispatch:\n{combined}"
    );
}

#[test]
fn a_single_step_plan_reads_exactly_as_it_did_before_the_executor() {
    // The per-step preamble is for chains only. A one-step plan is the overwhelmingly common
    // case and its output is what every other test and every user expectation is built on, so
    // the executor must be invisible there.
    let plan = r#"{"plan": [
        {"tool": "resize_video", "args": {"inputs": ["clip.mp4"], "height": 720, "output": "small.mp4"}}
    ]}"#;
    let (stdout, _stderr, ok) = run_with_plan(plan, "resize clip.mp4 to 720p");
    assert!(ok, "single-step plan failed:\n{stdout}");
    assert!(
        !stdout.contains("step 1"),
        "a one-step plan must not announce its position:\n{stdout}"
    );
    assert_eq!(
        stdout.lines().filter(|l| l.contains("ffmpeg")).count(),
        1,
        "expected exactly one command:\n{stdout}"
    );
}
