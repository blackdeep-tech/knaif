//! L2 / Workstream E: the ordered multi-step execution contract.
//!
//! **Every test here is `#[ignore]`d and red today, on purpose.** E5 requires the cases that
//! pin ordered execution to exist and fail *before* E2 builds the executor — otherwise the
//! executor would be certified correct by the same change that introduces it, which is the
//! pattern this plan exists to break. Un-skip them in E2/E3's PR.
//!
//! No model is involved: `KNAIF_LLM_BACKEND=mock` plus `KNAIF_LLM_MOCK_RESPONSE` feeds the
//! runtime an exact plan, so these are deterministic pipeline tests (L2), not behavioral ones.
//! Native currently answers a multi-step plan with `not_implemented:` and runs nothing, which
//! is what each of these asserts against.
//!
//! Note for whoever un-skips them: they execute `run --dry-run`, so the CI job that runs them
//! needs ffmpeg on PATH for dependency preflight. Today that is only true locally.
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
#[ignore = "red until E2 replaces StepDecision::Unsupported with an ordered loop"]
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
#[ignore = "red until E2 replaces StepDecision::Unsupported with an ordered loop"]
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
#[ignore = "red until E3 defines control-tool short-circuiting for multi-step plans"]
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
#[ignore = "red until E3 reports per-step outcomes instead of one verdict for the plan"]
fn a_failing_step_stops_the_chain_and_says_which_steps_ran() {
    // A chain that fails at step 2 of 3 must read as neither total success nor total failure:
    // step 1 already wrote a file to the user's disk.
    let plan = r#"{"plan": [
        {"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "silent.mp4"}},
        {"tool": "resize_video", "args": {"inputs": ["silent.mp4"], "height": "not-a-number"}},
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
