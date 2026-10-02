//! The plain (piped) output of `knaif run`, pinned byte for byte.
//!
//! `python/core/knaif/evalsuite/native_lane.py` (`parse_run_output`) and users' scripts read these
//! lines from a pipe, so they are a contract. The terminal view planned in
//! `docs/plans/2026-10-01-cli-terminal-output.md` may only ever *add* a second rendering for a
//! terminal; whatever a pipe sees must stay what these tests say.
//!
//! No model and no external binary: `KNAIF_LLM_BACKEND=mock` plus `KNAIF_LLM_MOCK_RESPONSE` feeds
//! the runtime an exact plan, and `--dry-run` stops before anything executes. Executing lines
//! (`running:`, `✓`, `✗`, `✓ wrote`) need ffmpeg or a document fixture and are pinned where they are
//! rendered, in the unit tests for that code.

use std::process::Command;

/// Run `knaif run <skill> --dry-run <utterance>` with a canned plan. Output is piped, so this is
/// the plain form by construction.
fn run(skill: &str, plan: &str, utterance: &str) -> (String, String, bool) {
    let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["run", skill, "--dry-run", utterance])
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", plan)
        .env_remove("KNAIF_DUMP_PLAN")
        .env_remove("KNAIF_DEBUG")
        .env_remove("NO_COLOR")
        .output()
        .expect("run the knaif binary");
    (
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
        out.status.success(),
    )
}

const SINGLE: &str = r#"{"plan": [{"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "silent.mp4"}}]}"#;

const CHAIN: &str = r#"{"plan": [
    {"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "silent.mp4"}},
    {"tool": "resize_video", "args": {"inputs": ["silent.mp4"], "height": 720, "output": "small.mp4"}}
]}"#;

#[test]
fn a_dry_run_single_step_prints_only_the_command() {
    let (stdout, stderr, ok) = run("ffmpeg", SINGLE, "strip audio from clip.mp4");
    assert!(ok);
    assert_eq!(stdout, "ffmpeg -y -i clip.mp4 -an -c:v copy silent.mp4\n");
    assert_eq!(stderr, "");
}

#[test]
fn a_dry_run_chain_numbers_its_steps_and_prints_each_command() {
    let (stdout, stderr, ok) = run("ffmpeg", CHAIN, "strip audio from clip.mp4 and resize it");
    assert!(ok);
    assert_eq!(
        stdout,
        "step 1 of 2:\n\
         ffmpeg -y -i clip.mp4 -an -c:v copy silent.mp4\n\
         step 2 of 2:\n\
         ffmpeg -y -i silent.mp4 -vf scale=-2:720 -c:v libx264 -crf 23 -preset medium \
         -pix_fmt yuv420p -c:a copy -movflags +faststart small.mp4\n"
    );
    assert_eq!(stderr, "");
}

#[test]
fn clarify_is_one_prefixed_line_on_stdout() {
    let plan = r#"{"plan": [{"tool": "clarify", "args": {"question": "Which file?"}}]}"#;
    let (stdout, stderr, ok) = run("ffmpeg", plan, "resize clip.mp4");
    assert!(ok);
    assert_eq!(stdout, "clarify: Which file?\n");
    assert_eq!(stderr, "");
}

#[test]
fn reject_is_one_prefixed_line_on_stdout() {
    let plan = r#"{"plan": [{"tool": "reject", "args": {"reason": "Not allowed."}}]}"#;
    let (stdout, stderr, ok) = run("ffmpeg", plan, "resize clip.mp4");
    assert!(ok);
    assert_eq!(stdout, "reject: Not allowed.\n");
    assert_eq!(stderr, "");
}

#[test]
fn done_says_there_is_nothing_to_do() {
    let plan = r#"{"plan": [{"tool": "done", "args": {}}]}"#;
    let (stdout, _stderr, ok) = run("ffmpeg", plan, "resize clip.mp4");
    assert!(ok);
    assert_eq!(stdout, "Nothing to do.\n");
}

#[test]
fn an_unsafe_request_is_rejected_before_any_plan_is_read() {
    let (stdout, stderr, ok) = run(
        "ffmpeg",
        r#"{"plan": []}"#,
        "run rm -rf on the media folder",
    );
    assert!(ok);
    assert_eq!(
        stdout,
        "reject: this request is blocked by the ffmpeg skill's safety policy.\n"
    );
    assert_eq!(stderr, "");
}

#[test]
fn a_failing_step_exits_nonzero_and_names_the_step() {
    let plan = r#"{"plan": [
        {"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "silent.mp4"}},
        {"tool": "resize_video", "args": {"inputs": ["silent.mp4"], "aspect": "not-an-aspect"}}
    ]}"#;
    let (_stdout, stderr, ok) = run("ffmpeg", plan, "strip audio from clip.mp4 and resize it");
    assert!(!ok);
    assert!(
        stderr.starts_with("Error: step 2 of 2 failed; step 1 had already completed"),
        "got: {stderr}"
    );
}

#[test]
fn a_password_containing_a_backslash_is_not_asked_for_again() {
    // The model sees the normalized request (`a/b`) and echoes that spelling; the grounding check
    // has to compare against the same text, or a typed password counts as invented.
    let plan = r#"{"plan": [{"tool": "protect_pdf", "args": {"input": "sample.pdf", "password": "a/b"}}]}"#;
    let (stdout, _stderr, _ok) = run("documents", plan, r"protect sample.pdf with password a\b");
    assert!(
        !stdout.contains("What password should I use?"),
        "a password the user typed was treated as invented:\n{stdout}"
    );
}

#[test]
fn an_invented_password_is_still_asked_for() {
    let plan = r#"{"plan": [{"tool": "protect_pdf", "args": {"input": "sample.pdf", "password": "hunter2"}}]}"#;
    let (stdout, _stderr, _ok) = run("documents", plan, "protect sample.pdf with a password");
    assert_eq!(stdout, "clarify: What password should I use?\n");
}

#[test]
fn a_binary_that_cannot_find_core_tools_says_so() {
    // Run from a folder with no `contracts/` above it and a binary that is not beside one: copy
    // the executable somewhere bare and run it there.
    let dir = std::env::temp_dir().join(format!("knaif_nocore_{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let exe = dir.join(
        std::path::Path::new(env!("CARGO_BIN_EXE_knaif"))
            .file_name()
            .unwrap(),
    );
    std::fs::copy(env!("CARGO_BIN_EXE_knaif"), &exe).unwrap();
    let skills = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../skills");
    let out = Command::new(&exe)
        .current_dir(&dir)
        .args(["run", "ffmpeg", "--dry-run", "resize clip.mp4"])
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_SKILLS_ROOT", skills)
        .env("KNAIF_LLM_MOCK_RESPONSE", SINGLE)
        .output()
        .unwrap();
    let _ = std::fs::remove_dir_all(&dir);
    assert!(!out.status.success());
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("core_tools.yaml not found"),
        "got: {stderr}"
    );
}

/// Same drive as [`run`], but forcing the terminal view through a pipe. Colors stay off because a
/// pipe cannot take the Windows VT switch.
fn run_rich(plan: &str, utterance: &str, extra: &[&str]) -> (String, String, bool) {
    let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["run", "ffmpeg"])
        .args(extra)
        .arg(utterance)
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", plan)
        .env("KNAIF_VIEW", "rich")
        .env("NO_COLOR", "1")
        .output()
        .expect("run the knaif binary");
    (
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
        out.status.success(),
    )
}

#[test]
fn the_terminal_view_draws_a_tree_with_the_command_and_a_total() {
    let (stdout, stderr, ok) = run_rich(
        CHAIN,
        "strip audio from clip.mp4 and resize it",
        &["--dry-run"],
    );
    assert!(ok, "stderr: {stderr}");
    assert!(stdout.starts_with(" knaif · ffmpeg\n"), "{stdout}");
    assert!(stdout.contains("Plan · 2 steps"), "{stdout}");
    assert!(stdout.contains("Step 1/2 · strip_audio"), "{stdout}");
    assert!(
        stdout.contains("$ ffmpeg -y -i clip.mp4 -an -c:v copy silent.mp4"),
        "{stdout}"
    );
    assert!(
        stdout.contains("└─ Done · dry run, nothing executed · total "),
        "{stdout}"
    );
    assert!(
        !stdout.contains("step 1 of 2:"),
        "plain headers leaked:\n{stdout}"
    );
    assert_eq!(stderr, "");
}

#[test]
fn the_terminal_view_closes_on_a_question_without_a_done_line() {
    let plan = r#"{"plan": [{"tool": "clarify", "args": {"question": "Which file?"}}]}"#;
    let (stdout, _stderr, ok) = run_rich(plan, "resize clip.mp4", &["--dry-run"]);
    assert!(ok);
    assert!(
        stdout.contains("└─ ? I need more detail: Which file?"),
        "{stdout}"
    );
    assert!(!stdout.contains("Done"), "{stdout}");
    assert!(!stdout.contains("clarify:"), "{stdout}");
}

#[test]
fn the_terminal_view_names_the_error_that_stopped_the_run() {
    let plan = r#"{"plan": [
        {"tool": "strip_audio", "args": {"inputs": ["clip.mp4"], "output": "silent.mp4"}},
        {"tool": "resize_video", "args": {"inputs": ["silent.mp4"], "aspect": "not-an-aspect"}}
    ]}"#;
    let (stdout, _stderr, ok) = run_rich(
        plan,
        "strip audio from clip.mp4 and resize it",
        &["--dry-run"],
    );
    assert!(!ok);
    assert!(stdout.contains("└─ ✗ step 2 of 2 failed;"), "{stdout}");
    assert!(stdout.contains("· total "), "{stdout}");
}

#[test]
fn view_plain_overrides_a_terminal_and_matches_a_pipe() {
    let out = Command::new(env!("CARGO_BIN_EXE_knaif"))
        .args(["run", "ffmpeg", "--dry-run", "strip audio from clip.mp4"])
        .env("KNAIF_LLM_BACKEND", "mock")
        .env("KNAIF_LLM_MOCK_RESPONSE", SINGLE)
        .env("KNAIF_VIEW", "plain")
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8_lossy(&out.stdout),
        "ffmpeg -y -i clip.mp4 -an -c:v copy silent.mp4\n"
    );
}
